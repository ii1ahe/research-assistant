"""Parses ask, demo, and purge commands, prints results, and chooses the process exit status."""

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

# Use the same source names and aliases as request validation.
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

# Default used in CLI help and the benchmark. Requests use the active settings.
DEFAULT_MAX_RESULTS = 3

# Resolve demo data from the repo root so it works from other directories.
DEMO_DATA = Path(__file__).resolve().parent.parent / "data" / "research_questions.json"

# Build help text from the accepted aliases.
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
    """Read and validate all demo questions before sending any provider requests.

    Args:
        path: Optional data-file path, also used by tests.

    Returns:
        Questions in file order.

    Raises:
        ConfigurationError: The file cannot be read or has invalid question data.
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
    """Map a finished run onto the exit status contract."""
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
    """Run every supplied sample question, in the order they were given."""
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
    """Delete expired cache entries, and report how many were removed."""
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

    Raises:
        ConfigurationError: The environment is invalid, a configured database
            cannot be used, or the supplied question set is unusable.
        InvalidRequestError: The arguments do not describe a runnable request.
    """
    if args.command == "purge":
        async with bootstrap() as application:
            return await _purge(application)

    # Validate settings before opening resources and reuse them for result limits.
    settings = get_settings()
    use_cache = not args.no_cache

    if args.command == "demo":
        # Demo questions use the configured result limit.
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

    # Use the configured limit when --max-results is omitted.
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
    # argparse already exits with status 2 for invalid arguments.
    args = build_parser().parse_args(argv)

    try:
        return asyncio.run(run(args))
    except (ConfigurationError, InvalidRequestError) as exc:
        print(f"researcher: {exc.message}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # The context manager has already closed the pools.
        print("\nresearcher: interrupted", file=sys.stderr)
        return EXIT_FAILURE
    except BrokenPipeError:
        # A pipe such as researcher ask ... | head may close stdout early.
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return EXIT_OK
