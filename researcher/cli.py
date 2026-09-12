"""Command-line interface.

Phase 1 establishes the argument surface, the source-alias contract, and the
exit-status mapping required by the project brief. The application wiring
(``bootstrap`` -> ``ResearchService`` -> ``rendering``) is added in Phase 6 of
the roadmap in ``docs/architecture.md``; until then each subcommand reports that
it is not yet wired rather than pretending to succeed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from researcher import __version__

# Source names and their aliases are defined once, in `researcher.validation`,
# so the CLI cannot drift from the normalisation the rest of the application
# performs. See `validation.parse_sources`, which consumes both.
from researcher.validation import SOURCE_ALIASES, SOURCE_NAMES

# --- Exit statuses ---------------------------------------------------------
#: Success, or a partial success that was clearly disclosed to the user.
EXIT_OK = 0
#: Could not produce a usable answer, or a required persistence write failed.
EXIT_FAILURE = 1
#: Invalid input or invalid configuration.
EXIT_USAGE = 2

#: Canonical source names accepted by ``--sources``.
SOURCE_CHOICES = SOURCE_NAMES

#: Rendered once for the ``--sources`` help text so that the aliases advertised
#: to the user are always the aliases actually accepted.
_ALIAS_HELP = ", ".join(
    f"'{alias}' for '{canonical}'" for alias, canonical in sorted(SOURCE_ALIASES.items())
)


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
        default=3,
        metavar="N",
        help="Maximum results to request from each source (default: 3).",
    )

    demo = commands.add_parser("demo", help="Run the five supplied sample questions from data/.")
    demo.add_argument("--no-cache", action="store_true", help="Bypass the cache entirely.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and return a process exit status.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        One of :data:`EXIT_OK`, :data:`EXIT_FAILURE` or :data:`EXIT_USAGE`.
    """
    args = build_parser().parse_args(argv)

    # Phase 6 replaces this with a call into the application service. Until
    # then the CLI is honest about not being wired up rather than exiting 0.
    print(
        f"researcher: '{args.command}' is not wired up yet. "
        "This build contains Phase 1 packaging only; see docs/architecture.md.",
        file=sys.stderr,
    )
    return EXIT_FAILURE
