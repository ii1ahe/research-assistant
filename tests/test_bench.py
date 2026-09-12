"""The benchmark's arithmetic, checked before it is trusted with a number.

``scripts/bench.py`` is the one module whose output is quoted rather than
executed: the timings it prints go into the README and the report, and nobody
re-runs them to see whether they were right. That is exactly the situation where
a silent bug survives, so the parts that can be checked offline are checked
here — which sources each question is asked of, how a failure is counted, and
the shape of the two tables the documentation copies.

Nothing here runs a sweep, so nothing here spends API quota. The measurement
itself is the one thing that cannot be tested without doing the thing being
measured, and a script that faked it would prove nothing about the live run.

The invariant that matters most is the cheapest to state and the easiest to
break: ``use_cache`` is ``False`` on every benchmark request. A cache hit in the
second mode would replace a network round trip with a local read, and the
comparison would be reporting the speed of PostgreSQL.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

import pytest

from researcher.models import (
    FailureDetail,
    ResearchRequest,
    ResearchResult,
    ResultStatus,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from scripts import bench
from tests.conftest import make_result, make_source


def _outcome(
    source: SourceName,
    status: SourceStatus = SourceStatus.SUCCESS,
    *,
    elapsed_seconds: float = 1.0,
    results: int = 1,
) -> SourceOutcome:
    """Build an outcome that satisfies ``SourceOutcome``'s status/payload rule."""
    payload = tuple(make_source(source.value, n) for n in range(1, results + 1))
    if status is not SourceStatus.SUCCESS:
        # A non-success outcome must not carry sources, and the two failure
        # statuses must describe themselves.
        payload = ()
    failure = (
        FailureDetail(code="timeout", message="the source did not answer", source=source.value)
        if status in (SourceStatus.TIMEOUT, SourceStatus.ERROR)
        else None
    )
    return SourceOutcome(
        source=source,
        status=status,
        sources=payload,
        elapsed_seconds=elapsed_seconds,
        failure=failure,
    )


def _measured(
    identifier: str,
    *,
    retrieval: float = 3.0,
    synthesis: float = 1.0,
    status: ResultStatus = ResultStatus.SUCCESS,
    outcomes: Sequence[SourceOutcome] = (),
) -> bench.Measured:
    return bench.Measured(
        identifier=identifier,
        difficulty="easy",
        selected=(SourceName.WIKIPEDIA,),
        status=status,
        retrieval_seconds=retrieval,
        synthesis_seconds=synthesis,
        total_seconds=retrieval + synthesis,
        outcomes=tuple(outcomes),
    )


def _run(label: str, *measured: bench.Measured, wall: float = 20.0, bound: int = 1) -> bench.Run:
    return bench.Run(label=label, bound=bound, wall_seconds=wall, measured=tuple(measured))


# ---------------------------------------------------------------------------
# What each question is asked of
# ---------------------------------------------------------------------------


@pytest.fixture
def questions() -> list[bench.DemoQuestion]:
    return [
        bench.DemoQuestion(id="q1", text="one?", difficulty="easy", sources=("wikipedia",)),
        bench.DemoQuestion(id="q2", text="two?", difficulty="hard", sources=("arxiv", "web")),
    ]


def test_the_controlled_plan_gives_every_question_every_source(
    questions: list[bench.DemoQuestion],
) -> None:
    """The headline claim is only visible when there is something to overlap.

    A question with a single source has no concurrency to exploit, so a sweep
    over declared sources would understate the effect the benchmark exists to
    measure.
    """
    plan = bench._plan(questions, declared=False)

    assert [sources for _, sources in plan] == [
        tuple(SourceName(name) for name in bench.SOURCE_CHOICES),
    ] * 2


def test_the_declared_plan_uses_each_question_own_sources(
    questions: list[bench.DemoQuestion],
) -> None:
    plan = bench._plan(questions, declared=True)

    assert [sources for _, sources in plan] == [
        (SourceName.WIKIPEDIA,),
        (SourceName.ARXIV, SourceName.WEB),
    ]


def test_both_plans_ask_the_same_questions_in_the_same_order(
    questions: list[bench.DemoQuestion],
) -> None:
    """Only the source selection may differ, or the two modes aren't comparable."""
    assert [question for question, _ in bench._plan(questions, declared=False)] == [
        question for question, _ in bench._plan(questions, declared=True)
    ]


def test_the_plan_does_not_mutate_the_question_set() -> None:
    """``Question.sources`` is a tuple, so this holds by construction."""
    question = bench.DemoQuestion(id="q", text="?", difficulty="easy", sources=("wikipedia",))

    bench._plan([question], declared=False)

    assert question.sources == ("wikipedia",)


# ---------------------------------------------------------------------------
# The one invariant that makes the comparison mean anything
# ---------------------------------------------------------------------------


def test_every_benchmark_request_bypasses_the_cache() -> None:
    """A cache hit in the second mode would measure the database, not the network.

    The second mode runs against a cache the first one has just filled, so a
    request that consulted it would be answered locally and report a speedup
    that has nothing to do with concurrency.
    """
    question = bench.DemoQuestion(id="q", text="?", difficulty="easy", sources=("wikipedia",))

    request = bench._request(question, (SourceName.WIKIPEDIA,))

    assert isinstance(request, ResearchRequest)
    assert request.use_cache is False


def test_the_request_carries_the_question_verbatim() -> None:
    """Same guarantee as the CLI: the benchmark must not test a rewritten query."""
    question = bench.DemoQuestion(
        id="q", text="What is Photosynthesis?", difficulty="easy", sources=("wikipedia",)
    )

    assert bench._request(question, (SourceName.WIKIPEDIA,)).question == "What is Photosynthesis?"


def test_the_request_uses_the_documented_result_limit() -> None:
    """Hard-coded, so the benchmark cannot silently drift from the demo run."""
    question = bench.DemoQuestion(id="q", text="?", difficulty="easy", sources=("wikipedia",))

    assert (
        bench._request(question, (SourceName.WIKIPEDIA,)).max_results == bench.DEFAULT_MAX_RESULTS
    )


# ---------------------------------------------------------------------------
# Counting failures
# ---------------------------------------------------------------------------


def test_an_empty_source_is_not_counted_as_a_failure() -> None:
    """A provider that answered with nothing is a content outcome, not an outage.

    Counting it would overstate the failure rate, and the failure rate is one of
    the numbers the report uses to argue the run was healthy.
    """
    run = _run(
        "sequential",
        _measured(
            "q1",
            outcomes=(
                _outcome(SourceName.WIKIPEDIA, SourceStatus.EMPTY),
                _outcome(SourceName.ARXIV, SourceStatus.SUCCESS),
            ),
        ),
    )

    assert run.failure_count == 0


@pytest.mark.parametrize("status", [SourceStatus.TIMEOUT, SourceStatus.ERROR])
def test_a_timed_out_or_errored_source_is_counted(status: SourceStatus) -> None:
    run = _run(
        "sequential",
        _measured("q1", outcomes=(_outcome(SourceName.ARXIV, status),)),
    )

    assert run.failure_count == 1


def test_failures_are_summed_across_questions() -> None:
    run = _run(
        "sequential",
        _measured("q1", outcomes=(_outcome(SourceName.ARXIV, SourceStatus.TIMEOUT),)),
        _measured("q2", outcomes=(_outcome(SourceName.WEB, SourceStatus.ERROR),)),
    )

    assert run.failure_count == 2


def test_a_question_that_produced_nothing_is_reported_as_unanswered() -> None:
    """This is what makes a sweep invalid rather than merely slow."""
    run = _run(
        "sequential",
        _measured("q1"),
        _measured("q2", status=ResultStatus.NO_SOURCES),
    )

    assert [item.identifier for item in run.unanswered] == ["q2"]


def test_a_refused_synthesis_is_counted_as_unanswered() -> None:
    """``NO_SOURCES`` is not the only way a sweep comes back empty-handed.

    A sweep whose fetches all succeeded but whose synthesizer refused every
    question is just as invalid, and it is the more dangerous case: the answers
    are missing but the sources are not, so nothing about the run looks wrong.
    """
    run = _run("concurrent", _measured("q1", status=ResultStatus.FAILED), bound=3)

    assert run.unanswered == ()
    assert run.answered == 0
    assert not run.synthesis_complete


def test_a_partial_answer_still_counts_as_an_answer() -> None:
    """A disclosed partial is a success for this purpose; it has real timings."""
    run = _run("sequential", _measured("q1", status=ResultStatus.PARTIAL))

    assert run.answered == 1
    assert run.synthesis_complete


# ---------------------------------------------------------------------------
# Withholding what cannot be reported honestly
# ---------------------------------------------------------------------------


def _both(*, status: ResultStatus) -> tuple[bench.Run, bench.Run]:
    """Two sweeps of one question each, at the given status."""
    return (
        _run("sequential", _measured("q1", status=status)),
        _run("concurrent", _measured("q1", status=status), bound=3),
    )


def test_a_run_where_synthesis_was_refused_reports_no_end_to_end_row() -> None:
    """The failure that motivated this check, kept as a test.

    A refused synthesis returns in about a second where a real one takes
    several. An end-to-end row built from five of those against five real calls
    shows a large speedup — which reads as the design working, when what it
    actually records is a provider rejecting the work.
    """
    sequential, concurrent = _both(status=ResultStatus.FAILED)

    report = bench.render(sequential, concurrent, plan_note="x")
    rows = report.splitlines()

    # Asserted on the rows rather than the text: the note that explains the
    # omission has to name what it is omitting, so a plain substring search
    # would fail on the very message that makes the report honest.
    assert not any(line.startswith("end-to-end") for line in rows)
    assert not any(line.startswith("synthesis (control)") for line in rows)
    assert any(line.startswith("retrieval") for line in rows)
    assert "withheld" in report


def test_the_withheld_report_still_carries_the_retrieval_measurement() -> None:
    """Retrieval is timed before synthesis runs, so it survives a refused one."""
    sequential, concurrent = _both(status=ResultStatus.FAILED)

    report = bench.render(sequential, concurrent, plan_note="x")

    assert "retrieval" in report
    assert "sweep wall clock" in report


def test_the_withheld_report_says_how_many_questions_were_answered() -> None:
    """The count is what tells a reader whether it was one question or all five."""
    sequential = _run("sequential", _measured("q1"), _measured("q2", status=ResultStatus.FAILED))
    concurrent = _run(
        "concurrent",
        _measured("q1", status=ResultStatus.FAILED),
        _measured("q2", status=ResultStatus.FAILED),
        bound=3,
    )

    report = bench.render(sequential, concurrent, plan_note="x")

    assert "sequential answered 1/2" in report
    assert "concurrent answered 0/2" in report


def test_two_sweeps_of_different_lengths_are_refused_rather_than_tabulated() -> None:
    """The comparison is only meaningful between two sweeps of the same questions.

    ``_per_question`` zips them with ``strict=True``, so a mismatch is a crash
    rather than a table with one mode's extra row quietly dropped — which would
    make the two totals incomparable without saying so.
    """
    sequential = _run("sequential", _measured("q1"), _measured("q2"))
    concurrent = _run("concurrent", _measured("q1"), bound=3)

    with pytest.raises(ValueError, match="shorter than"):
        bench.render(sequential, concurrent, plan_note="x")


def test_the_markdown_table_omits_end_to_end_when_synthesis_was_refused() -> None:
    """The table the README copies must not carry a row the report withheld."""
    sequential, concurrent = _both(status=ResultStatus.FAILED)

    table = bench.markdown(sequential, concurrent)

    assert "Retrieval only" in table
    assert "End-to-end" not in table


def test_the_json_reports_no_end_to_end_speedup_when_synthesis_was_refused() -> None:
    """``null`` rather than a number, so no downstream reader can quote one."""
    sequential, concurrent = _both(status=ResultStatus.FAILED)

    payload = bench.as_dict(sequential, concurrent)

    assert payload["synthesis_complete"] is False
    speedup = payload["speedup"]
    assert isinstance(speedup, dict)
    assert speedup["end_to_end"] is None
    assert isinstance(speedup["retrieval"], float)


def test_a_healthy_run_still_reports_both_rows() -> None:
    """The withholding must be a response to failure, not the default."""
    sequential, concurrent = _both(status=ResultStatus.SUCCESS)

    assert "end-to-end" in bench.render(sequential, concurrent, plan_note="x")
    assert "End-to-end" in bench.markdown(sequential, concurrent)


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_the_totals_are_the_sum_of_the_questions() -> None:
    run = _run(
        "sequential", _measured("q1", retrieval=2.0, synthesis=1.0), _measured("q2", retrieval=4.0)
    )

    assert run.retrieval_seconds == 6.0
    assert run.synthesis_seconds == 2.0
    assert run.total_seconds == 8.0


def test_speedup_is_the_ratio_of_the_totals() -> None:
    slow = _run("sequential", _measured("q1", retrieval=6.0))
    fast = _run("concurrent", _measured("q1", retrieval=2.0), bound=3)

    assert bench._speedup(slow, fast) == 3.0


def test_an_empty_sweep_reports_no_speedup_instead_of_dividing_by_zero() -> None:
    """A report that raises is worse than a report that admits it has no data."""
    assert bench._speedup(_run("sequential"), _run("concurrent", bound=3)) == 0.0
    assert bench._ratio(1.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# The two tables the documentation copies
# ---------------------------------------------------------------------------


def test_the_report_names_every_question_and_both_bounds() -> None:
    sequential = _run("sequential", _measured("q1", retrieval=6.0), _measured("q2", retrieval=4.0))
    concurrent = _run(
        "concurrent", _measured("q1", retrieval=2.0), _measured("q2", retrieval=3.0), bound=3
    )

    report = bench.render(sequential, concurrent, plan_note="every source per question")

    assert "q1 (easy)" in report
    assert "q2 (easy)" in report
    assert "bound=1" in report
    assert "bound=3" in report
    assert "every source per question" in report


def test_the_report_shows_the_stage_breakdown_and_the_sweep_clock() -> None:
    """Retrieval, the synthesis control, end-to-end, and the outer wall clock."""
    report = bench.render(
        _run("sequential", _measured("q1"), wall=30.0),
        _run("concurrent", _measured("q1"), wall=18.0, bound=3),
        plan_note="x",
    )

    assert "retrieval" in report
    assert "synthesis (control)" in report
    assert "end-to-end" in report
    assert "sweep wall clock" in report


def test_the_report_states_the_failure_count_for_both_sweeps() -> None:
    sequential = _run(
        "sequential", _measured("q1", outcomes=(_outcome(SourceName.ARXIV, SourceStatus.TIMEOUT),))
    )

    report = bench.render(sequential, _run("concurrent", _measured("q1"), bound=3), plan_note="x")

    assert "1 source failure(s)" in report


def test_the_per_source_table_lists_every_source_by_name() -> None:
    outcomes = (
        _outcome(SourceName.WIKIPEDIA, elapsed_seconds=0.5),
        _outcome(SourceName.ARXIV, SourceStatus.EMPTY, elapsed_seconds=2.0),
    )
    report = bench.render(
        _run("sequential", _measured("q1", outcomes=outcomes)),
        _run("concurrent", _measured("q1", outcomes=outcomes), bound=3),
        plan_note="x",
    )

    assert "wikipedia" in report
    assert "arxiv" in report
    assert "empty" in report


def test_the_markdown_table_has_a_header_and_one_row_per_workload() -> None:
    sequential = _run("sequential", _measured("q1", retrieval=6.0), _measured("q2", retrieval=4.0))
    concurrent = _run(
        "concurrent", _measured("q1", retrieval=2.0), _measured("q2", retrieval=3.0), bound=3
    )

    lines = bench.markdown(sequential, concurrent).splitlines()

    assert lines[0].startswith("| Workload |")
    assert lines[1].startswith("| --- |")
    assert len(lines) == 2 + 3
    for line in lines:
        assert line.startswith("|")
        assert line.endswith("|")


def test_the_markdown_speedup_matches_the_report() -> None:
    """The two views must not be able to disagree about the headline number."""
    sequential = _run("sequential", _measured("q1", retrieval=6.0))
    concurrent = _run("concurrent", _measured("q1", retrieval=2.0), bound=3)

    assert "3.00x" in bench.markdown(sequential, concurrent)


# ---------------------------------------------------------------------------
# The machine-readable copy
# ---------------------------------------------------------------------------


def test_the_json_output_is_serialisable_and_complete() -> None:
    """Written to ``artefacts/`` for the report, so it has to survive ``json.dumps``."""
    payload = bench.as_dict(
        _run("sequential", _measured("q1", outcomes=(_outcome(SourceName.WIKIPEDIA),))),
        _run(
            "concurrent",
            _measured("q1", outcomes=(_outcome(SourceName.ARXIV, SourceStatus.ERROR),)),
            bound=3,
        ),
    )

    rendered = json.dumps(payload)

    assert "[1, 2]" not in rendered  # no tuples leaked through
    assert payload["speedup"] == {"retrieval": 1.0, "end_to_end": 1.0}
    runs = payload["runs"]
    assert isinstance(runs, list)
    assert [run["bound"] for run in runs] == [1, 3]
    outcomes = runs[1]["questions"][0]["outcomes"]
    assert outcomes[0] == {
        "source": "arxiv",
        "status": "error",
        "elapsed_seconds": 1.0,
        "attempts": 1,
        "results": 0,
    }


# ---------------------------------------------------------------------------
# Column layout
# ---------------------------------------------------------------------------


def test_columns_are_sized_to_their_widest_cell() -> None:
    """A header wider than every value must still not collide with the next column."""
    lines = bench._column([("a", "1")], ("a much longer heading", "n"))
    body = lines[2]

    assert body.startswith("a ")
    assert body.index("1") > len("a much longer heading")


def test_the_first_column_is_left_aligned_and_the_rest_right() -> None:
    """Labels read better ragged-right; numbers compare better on their digits."""
    lines = bench._column([("short", "1"), ("a longer label", "22")], ("label", "n"))

    assert lines[2].startswith("short")
    assert lines[3].startswith("a longer label")
    assert lines[2].endswith(" 1")


def test_a_single_row_table_still_renders_a_rule() -> None:
    lines = bench._column([("only", "1")], ("label", "n"))

    assert len(lines) == 3
    assert set(lines[1]) <= {"-", " "}


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


class _Clock:
    """A stand-in for the service that records when each question was asked."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    async def research(self, request: ResearchRequest) -> ResearchResult:
        self.asked.append(time.perf_counter())
        return make_result(question=request.question)


def _plan_of(count: int) -> list[tuple[bench.DemoQuestion, tuple[SourceName, ...]]]:
    return [
        (
            bench.DemoQuestion(
                id=f"q{n}", text=f"question {n}?", difficulty="easy", sources=("wikipedia",)
            ),
            (SourceName.WIKIPEDIA,),
        )
        for n in range(1, count + 1)
    ]


@pytest.mark.asyncio
async def test_the_pause_waits_between_questions_and_never_before_the_first() -> None:
    """The wait exists to stay inside a rate limit; it must not become latency.

    A pause that leaked inside a question would be charged to retrieval, and
    the benchmark would report its own throttling as the application being
    slow. Waiting before the first question would do the same to the sweep.
    """
    service = _Clock()

    started = time.perf_counter()
    await bench._sweep(service, _plan_of(3), pause=0.05)  # type: ignore[arg-type]

    assert service.asked[0] - started < 0.05, "the first question must not wait"
    gaps = [
        later - earlier for earlier, later in zip(service.asked, service.asked[1:], strict=False)
    ]
    assert len(gaps) == 2
    assert all(gap >= 0.05 for gap in gaps)


@pytest.mark.asyncio
async def test_no_pause_means_no_waiting_at_all() -> None:
    """A paid quota should be able to run the sweep flat out."""
    service = _Clock()

    started = time.perf_counter()
    await bench._sweep(service, _plan_of(3), pause=0.0)  # type: ignore[arg-type]

    assert time.perf_counter() - started < 0.05


def test_a_table_with_no_rows_renders_its_header_instead_of_raising() -> None:
    """The invalid run is the one whose report matters most.

    When no source returned anything there are no outcomes to group, so the
    per-source table has no body — and that is precisely the sweep the exit
    status exists to flag. Crashing on the way to saying so would replace a
    clear "these numbers are not a comparison" with a traceback.
    """
    lines = bench._column([], ("source", "n"))

    assert lines[0].startswith("source")
    assert set(lines[1]) <= {"-", " "}


def test_the_whole_report_renders_when_nothing_came_back() -> None:
    empty = _measured("q1", status=ResultStatus.NO_SOURCES, retrieval=0.0, synthesis=0.0)

    report = bench.render(
        _run("sequential", empty, wall=0.5),
        _run("concurrent", empty, wall=0.5, bound=3),
        plan_note="x",
    )

    assert "source" in report
    assert "0 source failure(s)" in report
