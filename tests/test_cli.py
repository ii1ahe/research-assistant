"""The command line: what the user types, and what comes back.

Two things are being tested, and they are different in kind. The first is
*parsing* — that the argument surface means what the help text says, including
the aliases, which live in ``researcher.validation`` and are imported here so
the two cannot drift. The second is the *contract*: the exit status table, and
the split between the stream that carries the answer and the stream that carries
everything else. ``researcher ask "…" > answer.md`` has to produce a file worth
keeping, and the only way to know it does is to assert on both streams.

``main`` is synchronous and ``run`` is asynchronous, so these are sync tests
driving ``main`` — which is also the function the entry point actually calls, so
the assertion is made against the real seam rather than a step inside it.

Nothing here reaches a network or a database. ``cli.bootstrap`` is replaced with
a stub context manager, which is the single call that would build the object
graph; what it yields records the requests it was given and returns scripted
results. ``bootstrap`` and the graph it builds have their own tests in
``test_bootstrap.py``.
"""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

import researcher.cli as cli
from researcher import __version__
from researcher.errors import ConfigurationError, InvalidRequestError, StorageError
from researcher.models import (
    PersistenceStatus,
    ResearchRequest,
    ResearchResult,
    ResultStatus,
    SourceName,
)
from tests.conftest import make_result


class Recorder:
    """Stands in for :class:`~researcher.core.researcher.ResearchService`.

    Records what it was asked and answers with the scripted results in order, so
    a test asserts on the request the CLI actually built rather than on the
    arguments it started from.
    """

    def __init__(self, results: Sequence[ResearchResult] = (), error: Exception | None = None):
        self._results = list(results)
        self._error = error
        self.requests: list[ResearchRequest] = []

    async def research(self, request: ResearchRequest) -> ResearchResult:
        """Record the request, then fail or answer as scripted."""
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._results:
            return self._results.pop(0)
        return make_result(question=request.question)


class FakeCache:
    """Stands in for the cache service the purge command drives."""

    def __init__(self, removed: int = 0, error: Exception | None = None) -> None:
        self.removed = removed
        self.error = error
        self.calls = 0

    async def purge_expired_strict(self, *, now: object = None) -> int:
        """Record the call, then fail or report as scripted."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.removed


class FakeApplication:
    """The attributes the CLI uses."""

    def __init__(
        self,
        service: Recorder,
        *,
        settings: SimpleNamespace | None = None,
        cache: FakeCache | None = None,
    ) -> None:
        self.service = service
        self.settings = settings if settings is not None else SimpleNamespace(database_url=None)
        self.cache = cache


class Opened:
    """Counts how many times the object graph was built and taken down."""

    def __init__(self) -> None:
        self.count = 0
        self.closed = 0


#: What the ``install`` fixture hands a test.
Build = Callable[..., "tuple[Recorder, Opened]"]


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Iterator[Build]:
    """Return a helper that replaces ``cli.bootstrap`` with a stub.

    The stub is a real async context manager, so the ``async with`` in ``run``
    is exercised — including its teardown, which is what makes the interruption
    and broken-pipe cases meaningful.
    """

    def build(
        results: Sequence[ResearchResult] = (),
        error: Exception | None = None,
        bootstrap_error: Exception | None = None,
        *,
        settings: SimpleNamespace | None = None,
        cache: FakeCache | None = None,
    ) -> tuple[Recorder, Opened]:
        service = Recorder(results, error)
        opened = Opened()

        @contextlib.asynccontextmanager
        async def fake_bootstrap(_settings: object = None) -> AsyncIterator[FakeApplication]:
            if bootstrap_error is not None:
                raise bootstrap_error
            opened.count += 1
            try:
                yield FakeApplication(service, settings=settings, cache=cache)
            finally:
                opened.closed += 1

        monkeypatch.setattr(cli, "bootstrap", fake_bootstrap)
        return service, opened

    return build


def _demo_file(tmp_path: Path, count: int = 2) -> Path:
    """Write a question set shaped like the supplied one."""
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {"id": f"q{n}", "text": f"question {n}?", "difficulty": "easy"}
                    for n in range(1, count + 1)
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# The argument surface
# ---------------------------------------------------------------------------


def test_ask_defaults_select_every_source_at_the_default_limit() -> None:
    args = cli.build_parser().parse_args(["ask", "why is the sky blue"])

    assert args.sources == ",".join(cli.SOURCE_CHOICES)
    assert args.max_results == cli.DEFAULT_MAX_RESULTS
    assert args.no_cache is False


def test_a_command_is_required() -> None:
    """argparse exits 2 on its own, which is already the contract's status."""
    with pytest.raises(SystemExit) as caught:
        cli.build_parser().parse_args([])

    assert caught.value.code == cli.EXIT_USAGE


def test_a_question_is_required() -> None:
    with pytest.raises(SystemExit) as caught:
        cli.build_parser().parse_args(["ask"])

    assert caught.value.code == cli.EXIT_USAGE


def test_version_is_reported_and_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.build_parser().parse_args(["--version"])

    assert caught.value.code == cli.EXIT_OK
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(("alias", "canonical"), sorted(cli.SOURCE_ALIASES.items()))
def test_every_alias_the_help_advertises_actually_selects_its_source(
    install: Build, alias: str, canonical: str
) -> None:
    """The help text is generated from the alias table, so it cannot lie.

    Checked by using the alias rather than by reading the table back, because a
    table that stopped being consulted would still generate the same sentence.
    """
    service, _ = install(results=[make_result()])

    cli.main(["ask", "q", "--sources", alias])

    assert service.requests[0].sources == (SourceName(canonical),)


# ---------------------------------------------------------------------------
# The supplied question set
# ---------------------------------------------------------------------------


def test_the_shipped_question_set_loads() -> None:
    """The file in ``data/`` is an artefact of the submission; it must parse."""
    questions = cli.load_demo_questions()

    assert len(questions) == 5
    assert [question.id for question in questions] == ["q1", "q2", "q3", "q4", "q5"]
    assert all(question.text.strip() for question in questions)


def test_a_missing_question_set_names_the_path_it_looked_in(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"

    with pytest.raises(ConfigurationError) as caught:
        cli.load_demo_questions(missing)

    assert "no question set" in caught.value.message
    assert str(missing) in caught.value.message


def test_an_unparseable_question_set_says_so(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="could not read"):
        cli.load_demo_questions(path)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"questions": []},
        {"questions": "five"},
        {"questions": [{"id": "q1"}]},
        {"questions": ["not an object", "nor this"]},
    ],
    ids=["no-key", "not-an-object", "empty", "not-a-list", "no-text", "entry-not-an-object"],
)
def test_a_question_set_that_is_not_shaped_as_expected_is_rejected(
    payload: object, tmp_path: Path
) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError):
        cli.load_demo_questions(path)


def test_an_entry_without_an_id_or_a_source_list_gets_a_sensible_default(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [{"text": "What is X?"}]}), encoding="utf-8")

    question = cli.load_demo_questions(path)[0]

    assert question.id == "q1"
    assert question.difficulty == "unknown"
    assert question.sources == cli.SOURCE_CHOICES


# ---------------------------------------------------------------------------
# The exit status contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "persistence", "expected"),
    [
        (ResultStatus.SUCCESS, PersistenceStatus.SAVED, cli.EXIT_OK),
        (ResultStatus.SUCCESS, PersistenceStatus.SKIPPED, cli.EXIT_OK),
        (ResultStatus.PARTIAL, PersistenceStatus.SKIPPED, cli.EXIT_OK),
        (ResultStatus.SUCCESS, PersistenceStatus.FAILED, cli.EXIT_FAILURE),
        (ResultStatus.PARTIAL, PersistenceStatus.FAILED, cli.EXIT_FAILURE),
        (ResultStatus.FAILED, PersistenceStatus.SKIPPED, cli.EXIT_FAILURE),
        (ResultStatus.NO_SOURCES, PersistenceStatus.SKIPPED, cli.EXIT_FAILURE),
    ],
    ids=[
        "success",
        "success-without-a-database",
        "disclosed-partial",
        "answer-but-write-failed",
        "partial-and-write-failed",
        "no-answer",
        "no-sources",
    ],
)
def test_the_exit_status_contract(
    status: ResultStatus, persistence: PersistenceStatus, expected: int
) -> None:
    """The table in the module docstring, asserted row by row.

    ``PARTIAL`` exiting 0 is the row that matters: a degraded answer the user
    was *told* about is a success, and exiting 1 would push a caller into
    treating a usable answer as a failure.
    """
    result = make_result(status=status, persistence=persistence)

    assert cli.exit_status(result) == expected


# ---------------------------------------------------------------------------
# The streams
# ---------------------------------------------------------------------------


def test_the_answer_goes_to_stdout_and_the_diagnostics_to_stderr(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole reason the split exists: a redirect must capture the answer.

    Otherwise ``researcher ask "…" > answer.md`` writes the source table and the
    timings into the middle of the document.
    """
    install(results=[make_result(question="q")])

    cli.main(["ask", "q"])

    out, err = capsys.readouterr()
    assert "References" in out
    assert "References" not in err
    assert "timing" in err
    assert "timing" not in out


def test_the_question_reaches_the_service_exactly_as_typed(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    """No canonicalisation on the way in — the cache key is not the question."""
    service, _ = install(results=[make_result(question="What is Photosynthesis?")])

    cli.main(["ask", "What is Photosynthesis?"])

    assert service.requests[0].question == "What is Photosynthesis?"
    assert "Question: What is Photosynthesis?" in capsys.readouterr().out


def test_an_answered_question_exits_zero(install: Build) -> None:
    install(results=[make_result(question="q")])

    assert cli.main(["ask", "q"]) == cli.EXIT_OK


def test_a_run_with_no_usable_answer_exits_one(install: Build) -> None:
    install(results=[make_result(status=ResultStatus.NO_SOURCES)])

    assert cli.main(["ask", "q"]) == cli.EXIT_FAILURE


# ---------------------------------------------------------------------------
# Building the request
# ---------------------------------------------------------------------------


def test_source_aliases_are_normalised_before_the_request_is_built(install: Build) -> None:
    service, _ = install(results=[make_result()])

    cli.main(["ask", "q", "--sources", "wiki,arxiv"])

    assert service.requests[0].sources == (SourceName.WIKIPEDIA, SourceName.ARXIV)


def test_no_cache_reaches_the_request(install: Build) -> None:
    """The flag is inverted on the way in: ``--no-cache`` means ``use_cache=False``."""
    service, _ = install(results=[make_result()])

    cli.main(["ask", "q", "--no-cache"])

    assert service.requests[0].use_cache is False


def test_the_result_limit_reaches_the_request(install: Build) -> None:
    service, _ = install(results=[make_result()])

    cli.main(["ask", "q", "--max-results", "7"])

    assert service.requests[0].max_results == 7


def test_an_empty_source_list_is_refused_before_anything_is_opened(install: Build) -> None:
    service, opened = install(results=[make_result()])

    code = cli.main(["ask", "q", "--sources", ","])

    assert code == cli.EXIT_USAGE
    assert service.requests == []
    assert opened.count == 0


def test_an_unknown_source_is_refused_before_anything_is_opened(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit status 2, and the graph is never built.

    Validating inside the ``async with`` would report the same status for the
    same reason, but only after dialling a database and opening a connection
    pool to do nothing with.
    """
    service, opened = install(results=[make_result()])

    code = cli.main(["ask", "q", "--sources", "gopher"])

    assert code == cli.EXIT_USAGE
    assert service.requests == []
    assert opened.count == 0
    assert "researcher: " in capsys.readouterr().err


def test_a_result_limit_below_one_is_refused(install: Build) -> None:
    _, opened = install(results=[make_result()])

    assert cli.main(["ask", "q", "--max-results", "0"]) == cli.EXIT_USAGE
    assert opened.count == 0


def test_building_a_request_reports_a_bad_source_as_an_invalid_request() -> None:
    """``_build_request`` converts, so its caller handles only our own errors."""
    with pytest.raises(InvalidRequestError):
        cli._build_request("q", "gopher", use_cache=True, max_results=3)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_bad_configuration_exits_two_with_a_message_that_names_us(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    install(bootstrap_error=ConfigurationError("no API key configured", source="synthesis"))

    code = cli.main(["ask", "q"])

    assert code == cli.EXIT_USAGE
    assert "researcher: no API key configured" in capsys.readouterr().err


def test_the_graph_is_taken_down_when_the_run_raises(install: Build) -> None:
    """The service failing must not leave a pool open behind the error."""
    _, opened = install(error=RuntimeError("the providers all failed"))

    with pytest.raises(RuntimeError, match="the providers all failed"):
        cli.main(["ask", "q"])

    assert opened.count == opened.closed == 1


def test_an_interrupt_is_reported_and_exits_one(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C is a failure, not a usage error: nothing the user typed was wrong."""
    _, opened = install(error=KeyboardInterrupt())

    code = cli.main(["ask", "q"])

    assert code == cli.EXIT_FAILURE
    assert "interrupted" in capsys.readouterr().err
    assert opened.closed == 1, "the interrupt must not abandon the pools"


def test_a_closed_stdout_is_not_blamed_on_the_user(install: Build) -> None:
    """``researcher ask "…" | head`` closes stdout early, which is normal use."""
    install(error=BrokenPipeError())
    stdout = io.StringIO()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", stdout)

        assert cli.main(["ask", "q"]) == cli.EXIT_OK

    assert stdout.closed, "the pipe should be closed rather than left dangling"


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def test_demo_asks_every_question_in_order(
    install: Build,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _ = install(
        results=[make_result(question="question 1?"), make_result(question="question 2?")]
    )
    monkeypatch.setattr(cli, "DEMO_DATA", _demo_file(tmp_path))

    code = cli.main(["demo"])

    assert code == cli.EXIT_OK
    assert [request.question for request in service.requests] == ["question 1?", "question 2?"]

    err = capsys.readouterr().err
    assert "--- q1 (easy)" in err
    assert "2/2 questions answered." in err


def test_demo_exits_one_if_any_question_goes_unanswered(
    install: Build, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One unanswered question fails the demo, however many others succeeded."""
    install(results=[make_result(), make_result(status=ResultStatus.NO_SOURCES)])
    monkeypatch.setattr(cli, "DEMO_DATA", _demo_file(tmp_path))

    assert cli.main(["demo"]) == cli.EXIT_FAILURE


def test_demo_exits_one_if_a_session_could_not_be_recorded(
    install: Build, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install(results=[make_result(), make_result(persistence=PersistenceStatus.FAILED)])
    monkeypatch.setattr(cli, "DEMO_DATA", _demo_file(tmp_path))

    assert cli.main(["demo"]) == cli.EXIT_FAILURE


def test_demo_honours_no_cache_for_every_question(
    install: Build, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service, _ = install(results=[make_result(), make_result()])
    monkeypatch.setattr(cli, "DEMO_DATA", _demo_file(tmp_path))

    cli.main(["demo", "--no-cache"])

    assert all(request.use_cache is False for request in service.requests)


def test_demo_reads_the_whole_question_set_before_asking_any_of_it(
    install: Build, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed set costs nothing, even when its earlier entries are fine.

    Every question is parsed before the first is asked, so the failure is
    discovered from a file already on disk rather than after two questions have
    spent the providers' quota.
    """
    service, opened = install()
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps({"questions": [{"id": "q1", "text": "fine?"}, {"id": "q2"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "DEMO_DATA", path)

    assert cli.main(["demo"]) == cli.EXIT_USAGE

    assert service.requests == []
    assert opened.count == 0


# ---------------------------------------------------------------------------
# The module entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


def _purge_settings(database_url: str | None) -> SimpleNamespace:
    """The one setting the purge command reads."""
    return SimpleNamespace(database_url=database_url)


def test_purge_reports_how_many_entries_were_removed(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    _, opened = install(
        settings=_purge_settings("postgresql://researcher@localhost/researcher"),
        cache=FakeCache(removed=3),
    )

    assert cli.main(["purge"]) == cli.EXIT_OK

    captured = capsys.readouterr()
    assert captured.out == "purged 3 expired cache entries\n"
    assert captured.err == ""
    # The purge path goes through the same lifecycle as ask and demo.
    assert opened.count == 1
    assert opened.closed == 1


def test_purge_spells_the_singular_correctly(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        settings=_purge_settings("postgresql://researcher@localhost/researcher"),
        cache=FakeCache(removed=1),
    )

    assert cli.main(["purge"]) == cli.EXIT_OK

    assert capsys.readouterr().out == "purged 1 expired cache entry\n"


def test_purge_without_a_database_reports_nothing_and_succeeds(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = FakeCache()
    install(settings=_purge_settings(None), cache=cache)

    assert cli.main(["purge"]) == cli.EXIT_OK

    captured = capsys.readouterr()
    assert captured.out == "purged 0 expired cache entries (no database is configured)\n"
    assert captured.err == ""
    # The service was never reached: with no database there is nothing to ask.
    assert cache.calls == 0


def test_a_purge_the_database_refuses_exits_one(
    install: Build, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        settings=_purge_settings("postgresql://researcher@localhost/researcher"),
        cache=FakeCache(error=StorageError("the database refused the delete", source="storage")),
    )

    assert cli.main(["purge"]) == cli.EXIT_FAILURE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not purge the cache" in captured.err
    assert "the database refused the delete" in captured.err


def test_python_dash_m_delegates_to_the_cli_and_propagates_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m researcher`` is documented, so it is tested like any other seam.

    Run through ``runpy`` rather than a subprocess so the call lands in this
    process and the assertion is about the delegation itself: the module must
    pass its own arguments through untouched and turn ``main``'s return value
    into the process exit status. A subprocess test would prove the same thing
    more slowly, and its coverage would not reach the report — ``pytest-cov``
    measures this process, not the one it spawns.
    """
    seen: list[Sequence[str] | None] = []

    def fake_main(argv: Sequence[str] | None = None) -> int:
        seen.append(argv)
        return cli.EXIT_FAILURE

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as caught:
        runpy.run_module("researcher", run_name="__main__")

    assert caught.value.code == cli.EXIT_FAILURE
    assert seen == [None]
