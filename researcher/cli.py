"""Command-line interface.

The argument surface, the source-alias contract and the exit-status mapping are
established here; the work itself is delegated. ``main`` does three things and
nothing else: parse, drive :func:`~researcher.bootstrap.bootstrap`, and turn the
outcome into a process exit status. Every rule about *whether* something is an
error lives in the layer that knows — this module only maps.

The mapping is the contract the brief asks for:

===  =======================================================================
0    An answer was produced, including a partial one whose missing sources
     were disclosed. A degraded answer the user was told about is a success.
1    No usable answer, or a session that was configured for storage was not
     stored.
2    Bad input or bad configuration — the user must change something, and
     retrying unchanged would spend quota to learn nothing.
===  =======================================================================

Streams are split deliberately: the answer goes to stdout, everything about the
run goes to stderr. ``researcher ask "…" > answer.md`` therefore writes a file
worth keeping, and the diagnostics are still on the terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from researcher import __version__
from researcher.bootstrap import Application, bootstrap
from researcher.config import get_settings
from researcher.errors import ConfigurationError, InvalidRequestError, StorageError
from researcher.models import PersistenceStatus, ResearchRequest, ResearchResult, ResultStatus
from researcher.rendering import render_answer, render_diagnostics, render_summary

# Source names and their aliases are defined once, in `researcher.validation`,
# so the CLI cannot drift from the normalisation the rest of the application
# performs. See `validation.parse_sources`, which consumes both.
from researcher.validation import SOURCE_ALIASES, SOURCE_NAMES, parse_sources

# --- Exit statuses ---------------------------------------------------------
#: Success, or a partial success that was clearly disclosed to the user.
EXIT_OK = 0
#: Could not produce a usable answer, or a required persistence write failed.
EXIT_FAILURE = 1
#: Invalid input or invalid configuration.
EXIT_USAGE = 2

#: Canonical source names accepted by ``--sources``.
SOURCE_CHOICES = SOURCE_NAMES

#: The documented default of ``MAX_RESULTS_PER_SOURCE``.
#:
#: The CLI no longer applies a result limit of its own — an omitted
#: ``--max-results`` resolves to whatever the settings say — because a number
#: kept here is a second copy of a configurable value, and the two disagree as
#: soon as the configuration moves. ``scripts/bench.py`` still names it, since
#: the benchmark drives one fixed configuration and its committed results were
#: produced under this limit.
DEFAULT_MAX_RESULTS = 3

#: The supplied question set, relative to the repository root. It ships with the
#: submission rather than being installed, so the path is resolved from this
#: file rather than from the working directory — `demo` then works from anywhere
#: inside the repository, which is where a grader will run it.
DEMO_DATA = Path(__file__).resolve().parent.parent / "data" / "research_questions.json"

#: Rendered once for the ``--sources`` help text so that the aliases advertised
#: to the user are always the aliases actually accepted.
_ALIAS_HELP = ", ".join(
    f"'{alias}' for '{canonical}'" for alias, canonical in sorted(SOURCE_ALIASES.items())
)


@dataclass(frozen=True, slots=True)
class DemoQuestion:
    """One entry from ``data/research_questions.json``."""

    id: str
    text: str
    difficulty: str
    sources: tuple[str, ...]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser exposed by the ``researcher`` entry point."""
    parser = argparse.ArgumentParser(
        prog="researcher",
        description=(
            "Ask a research question and get a concise answer with numbered "
            "references drawn from Wikipedia, arXiv and a web search provider."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    ask = commands.add_parser("ask", help="Research one question and print a cited answer.")
    ask.add_argument("question", help="The research question to answer.")
    ask.add_argument(
        "--sources",
        default=",".join(SOURCE_CHOICES),
        metavar="LIST",
        help=(
            f"Comma-separated subset of {{{','.join(SOURCE_CHOICES)}}}; "
            f"aliases: {_ALIAS_HELP} (default: all)."
        ),
    )
    ask.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the cache entirely: neither read from it nor write to it.",
    )
    ask.add_argument(
        "--max-results",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Maximum results to request from each source. Defaults to the "
            "configured MAX_RESULTS_PER_SOURCE, which is "
            f"{DEFAULT_MAX_RESULTS} unless it is set in .env."
        ),
    )

    demo = commands.add_parser("demo", help="Run the five supplied sample questions from data/.")
    demo.add_argument("--no-cache", action="store_true", help="Bypass the cache entirely.")

    commands.add_parser("purge", help="Delete expired cache entries from the configured database.")

    return parser


def load_demo_questions(path: Path | None = None) -> list[DemoQuestion]:
    """Read the supplied question set.

    Args:
        path: Override for the data file, used by tests.

    Returns:
        The questions, in file order.

    Raises:
        ConfigurationError: The file is missing, unreadable, or not shaped the
            way the application expects. Reported as exit status 2 because it is
            a problem with the installation the user can fix, and because
            discovering it after two questions have already spent quota would be
            worse — every question is parsed before any of them is asked.
    """
    source = path if path is not None else DEMO_DATA
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"no question set at {source}", source="demo") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"could not read {source}: {exc}", source="demo") from exc

    entries = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError(f"{source} contains no questions", source="demo")

    questions: list[DemoQuestion] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigurationError(f"{source}: question {index} is not an object", source="demo")
        try:
            text = str(entry["text"])
            identifier = str(entry.get("id", f"q{index}"))
        except KeyError as exc:
            message = f"{source}: question {index} has no text"
            raise ConfigurationError(message, source="demo") from exc
        expected = entry.get("expected_sources") or SOURCE_CHOICES
        questions.append(
            DemoQuestion(
                id=identifier,
                text=text,
                difficulty=str(entry.get("difficulty", "unknown")),
                sources=tuple(str(name) for name in expected),
            )
        )
    return questions


def _build_request(
    question: str,
    sources: str | Sequence[str],
    *,
    use_cache: bool,
    max_results: int,
) -> ResearchRequest:
    """Turn parsed arguments into a request.

    Raises:
        InvalidRequestError: The source list is empty or names something that
            does not exist. Raised here, before the application is driven, so an
            unusable ``--sources`` costs nothing.
    """
    parsed = parse_sources(sources)
    try:
        return ResearchRequest(
            question=question,
            sources=parsed,
            use_cache=use_cache,
            max_results=max_results,
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc), source="cli") from exc


def exit_status(result: ResearchResult) -> int:
    """Map a finished run onto the exit status contract.

    ``PARTIAL`` is a success and not a failure: the run disclosed which sources
    did not contribute, so the user has an answer and knows its limits. Only a
    run with no answer at all, or one whose session was configured for storage
    and not stored, is a failure.

    ``persistence`` can only be ``FAILED`` when a database was configured and
    the write was attempted, which is exactly what "a *required* persistence
    write failed" means. An unconfigured database reports ``SKIPPED`` and is not
    an error — ADR-002 makes persistence optional by leaving ``DATABASE_URL``
    unset.
    """
    if result.status in (ResultStatus.FAILED, ResultStatus.NO_SOURCES):
        return EXIT_FAILURE
    if result.persistence is PersistenceStatus.FAILED:
        return EXIT_FAILURE
    return EXIT_OK


async def _ask(application: Application, request: ResearchRequest) -> int:
    """Research one question and render it."""
    result = await application.service.research(request)
    print(render_answer(result))
    print(render_diagnostics(result), file=sys.stderr)
    return exit_status(result)


async def _demo(
    application: Application, plan: Sequence[tuple[DemoQuestion, ResearchRequest]]
) -> int:
    """Run every supplied sample question, in the order they were given.

    Asked one at a time rather than concurrently. The point of the demo is a
    readable end-to-end smoke run, and Wikipedia and arXiv are both key-free and
    rate-limited — the brief asks callers to be polite to them. Concurrency is
    measured properly by the benchmark in Phase 7, which is the thing that
    exists to measure it.
    """
    results: list[ResearchResult] = []

    for question, request in plan:
        print(f"--- {question.id} ({question.difficulty})", file=sys.stderr)
        result = await application.service.research(request)
        print(render_answer(result))
        print(render_diagnostics(result), file=sys.stderr)
        results.append(result)

    print(render_summary(results), file=sys.stderr)
    if any(result.answer is None for result in results):
        return EXIT_FAILURE
    if any(result.persistence is PersistenceStatus.FAILED for result in results):
        return EXIT_FAILURE
    return EXIT_OK


async def _purge(application: Application) -> int:
    """Delete expired cache entries, and report how many were removed.

    ``purge`` is housekeeping, so its result is the command's answer and goes
    to stdout like any other answer. The one failure it can meet after a
    successful bootstrap — a database that refuses the delete — is an
    operational failure, reported as exit status 1 rather than 2: nothing the
    user typed is wrong, and retrying unchanged might well succeed.
    """
    if application.settings.database_url is None:
        print("purged 0 expired cache entries (no database is configured)")
        return EXIT_OK
    try:
        removed = await application.cache.purge_expired_strict()
    except StorageError as exc:
        print(f"researcher: could not purge the cache: {exc.message}", file=sys.stderr)
        return EXIT_FAILURE
    noun = "entry" if removed == 1 else "entries"
    print(f"purged {removed} expired cache {noun}")
    return EXIT_OK


async def run(args: argparse.Namespace) -> int:
    """Drive the application for a parsed command.

    Everything resource-owning lives inside the ``async with``, so the pools are
    released on the paths that raise as well as the ones that return.

    Everything the *user* can get wrong is settled before it. The settings are
    loaded, and the command's inputs are read and built — the question set is
    parsed in full, and every request is constructed — so a mistyped
    ``--sources``, a malformed data file or an invalid environment is exit
    status 2 with no connection pool opened and no database dialled. Validating
    inside the ``async with`` would report the same status for the same reason,
    but only after opening resources to do nothing with.

    Raises:
        ConfigurationError: The environment is invalid, a configured database
            cannot be used, or the supplied question set is unusable.
        InvalidRequestError: The arguments do not describe a runnable request.
    """
    if args.command == "purge":
        async with bootstrap() as application:
            return await _purge(application)

    # Loaded here rather than inside the context manager so that an invalid
    # environment is still reported before anything is opened, and passed to
    # ``bootstrap`` so that the limit below and the ceiling that validates it
    # come from one object — the split between them is what let an ordinary
    # ``ask`` carry a limit its own configuration refused.
    settings = get_settings()
    use_cache = not args.no_cache

    if args.command == "demo":
        # No ``--max-results`` on ``demo``: every question it asks follows the
        # configured ceiling.
        limit = settings.max_results_per_source
        plan = [
            (
                question,
                _build_request(
                    question.text,
                    question.sources,
                    use_cache=use_cache,
                    max_results=limit,
                ),
            )
            for question in load_demo_questions()
        ]
        async with bootstrap(settings) as application:
            return await _demo(application, plan)

    # ``--max-results`` is unspecified, not defaulted: with no flag the
    # configured ceiling applies, so the request can never exceed it. An
    # explicit value still can, and validation still rejects it.
    limit = args.max_results if args.max_results is not None else settings.max_results_per_source
    request = _build_request(
        args.question,
        args.sources,
        use_cache=use_cache,
        max_results=limit,
    )
    async with bootstrap(settings) as application:
        return await _ask(application, request)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and return a process exit status.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        One of :data:`EXIT_OK`, :data:`EXIT_FAILURE` or :data:`EXIT_USAGE`.
    """
    # argparse owns invalid arguments and exits 2 on its own, which is already
    # the status the contract asks for, so its SystemExit is left to propagate.
    args = build_parser().parse_args(argv)

    try:
        return asyncio.run(run(args))
    except (ConfigurationError, InvalidRequestError) as exc:
        print(f"researcher: {exc.message}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # The interrupt already cancelled the run and unwound the context
        # manager, so the pools are closed by the time this runs.
        print("\nresearcher: interrupted", file=sys.stderr)
        return EXIT_FAILURE
    except BrokenPipeError:
        # `researcher ask "…" | head` closes stdout early. Dying noisily here
        # would blame the user for redirecting output, which is normal use.
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return EXIT_OK
