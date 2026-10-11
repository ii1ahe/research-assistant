"""Compares sequential and concurrent retrieval with caching disabled and writes timing tables
and JSON results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from researcher.bootstrap import Application, bootstrap
from researcher.cli import DEFAULT_MAX_RESULTS, SOURCE_CHOICES, DemoQuestion, load_demo_questions
from researcher.config import Settings, get_settings
from researcher.core.researcher import ResearchService
from researcher.errors import ConfigurationError
from researcher.models import (
    ResearchRequest,
    ResultStatus,
    SourceName,
    SourceOutcome,
    SourceStatus,
)

# Concurrent mode can fetch all three source types at once.
CONCURRENT_BOUND = 3

# A bound of one runs the same retrieval code sequentially.
SEQUENTIAL_BOUND = 1

# Count errors and timeouts as failures, not empty results.
FAILED_STATUSES = frozenset({SourceStatus.TIMEOUT, SourceStatus.ERROR})

# These statuses mean answer generation completed.
ANSWERED_STATUSES = frozenset({ResultStatus.SUCCESS, ResultStatus.PARTIAL})

# Pause between questions to reduce rate-limit failures. The pause is not timed
# as part of retrieval; it only contributes to the whole sweep duration.
PAUSE_SECONDS = 10.0


# What is measured


@dataclass(frozen=True, slots=True)
class Measured:
    """One question, run once, in one mode."""

    identifier: str
    difficulty: str
    selected: tuple[SourceName, ...]
    status: ResultStatus
    retrieval_seconds: float
    synthesis_seconds: float
    total_seconds: float
    outcomes: tuple[SourceOutcome, ...]

    @property
    def failures(self) -> tuple[SourceOutcome, ...]:
        """The outcomes that did not deliver, for the failure count."""
        return tuple(outcome for outcome in self.outcomes if outcome.status in FAILED_STATUSES)


@dataclass(frozen=True, slots=True)
class Run:
    """One mode's sweep over the whole question set."""

    label: str
    bound: int
    wall_seconds: float
    measured: tuple[Measured, ...]

    @property
    def retrieval_seconds(self) -> float:
        """Total time spent inside retrieval, summed over the questions."""
        return sum(item.retrieval_seconds for item in self.measured)

    @property
    def synthesis_seconds(self) -> float:
        """Total time spent inside synthesis."""
        return sum(item.synthesis_seconds for item in self.measured)

    @property
    def total_seconds(self) -> float:
        """Total per-question wall time, excluding the gaps between them."""
        return sum(item.total_seconds for item in self.measured)

    @property
    def failure_count(self) -> int:
        """How many source fetches failed, across every question."""
        return sum(len(item.failures) for item in self.measured)

    @property
    def unanswered(self) -> tuple[Measured, ...]:
        """Questions that produced no usable sources at all."""
        return tuple(item for item in self.measured if item.status is ResultStatus.NO_SOURCES)

    @property
    def answered(self) -> int:
        """How many questions reached an answer."""
        return sum(1 for item in self.measured if item.status in ANSWERED_STATUSES)

    @property
    def synthesis_complete(self) -> bool:
        """Whether every question in the sweep reached an answer."""
        return self.answered == len(self.measured)

    def outcomes(self) -> tuple[SourceOutcome, ...]:
        """Every outcome from the sweep, in question order."""
        return tuple(outcome for item in self.measured for outcome in item.outcomes)


def _speedup(baseline: Run, candidate: Run) -> float:
    """How many times faster ``candidate`` is than ``baseline``."""
    if baseline.retrieval_seconds <= 0:
        return 0.0
    return baseline.retrieval_seconds / candidate.retrieval_seconds


# Running the sweep


def _plan(
    questions: Sequence[DemoQuestion], *, declared: bool
) -> list[tuple[DemoQuestion, tuple[SourceName, ...]]]:
    """Decide which sources each question is asked of."""
    every = tuple(SourceName(name) for name in SOURCE_CHOICES)
    if declared:
        return [
            (question, tuple(SourceName(name) for name in question.sources))
            for question in questions
        ]
    return [(question, every) for question in questions]


def _request(question: DemoQuestion, sources: tuple[SourceName, ...]) -> ResearchRequest:
    """Build the request for one benchmark question."""
    return ResearchRequest(
        question=question.text,
        sources=sources,
        use_cache=False,
        max_results=DEFAULT_MAX_RESULTS,
    )


async def _sweep(
    service: ResearchService,
    plan: Sequence[tuple[DemoQuestion, tuple[SourceName, ...]]],
    *,
    pause: float = 0.0,
) -> tuple[float, tuple[Measured, ...]]:
    """Ask every question in order, timing the sweep as a whole."""
    started = time.perf_counter()
    measured: list[Measured] = []
    for index, (question, sources) in enumerate(plan):
        if index and pause > 0:
            await asyncio.sleep(pause)
        request = _request(question, sources)
        result = await service.research(request)
        measured.append(
            Measured(
                identifier=question.id,
                difficulty=question.difficulty,
                selected=request.sources,
                status=result.status,
                retrieval_seconds=result.timing.retrieval_seconds,
                synthesis_seconds=result.timing.synthesis_seconds,
                total_seconds=result.timing.total_seconds,
                outcomes=result.outcomes,
            )
        )
    return time.perf_counter() - started, tuple(measured)


async def _warm(
    application: Application, plan: Sequence[tuple[DemoQuestion, tuple[SourceName, ...]]]
) -> None:
    """Pay the one-off connection costs on a question nobody will read."""
    question, sources = plan[0]
    await application.service.research(_request(question, sources))


async def _run_mode(
    settings: Settings,
    label: str,
    bound: int,
    plan: Sequence[tuple[DemoQuestion, tuple[SourceName, ...]]],
    *,
    warm: bool,
    pause: float = 0.0,
) -> Run:
    """Boot the application at one bound and sweep the question set."""
    # Only the concurrency bound changes between modes.
    tuned = settings.model_copy(update={"max_parallel_sources": bound})
    async with bootstrap(tuned) as application:
        if warm:
            await _warm(application, plan)
            if pause > 0:
                # Warm-up also uses provider quota, so pause before the measured questions.
                await asyncio.sleep(pause)
        wall_seconds, measured = await _sweep(application.service, plan, pause=pause)
    return Run(label=label, bound=bound, wall_seconds=wall_seconds, measured=measured)


# Drawing the result

_WIDTH = 78


def _rule() -> str:
    return "=" * _WIDTH


def _column(rows: Sequence[tuple[str, ...]], headers: Sequence[str]) -> list[str]:
    """Lay out rows in fixed columns, sized to the widest cell in each."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        # Left-align labels and right-align numbers.
        return "  ".join(
            cell.ljust(widths[index]) if index == 0 else cell.rjust(widths[index])
            for index, cell in enumerate(cells)
        ).rstrip()

    return [
        line(headers),
        line(tuple("-" * width for width in widths)),
        *(line(row) for row in rows),
    ]


def _per_question(sequential: Run, concurrent: Run) -> list[str]:
    """The per-question retrieval table, which is where the claim is visible."""
    rows: list[tuple[str, ...]] = []
    for before, after in zip(sequential.measured, concurrent.measured, strict=True):
        label = f"{before.identifier} ({before.difficulty})"
        rows.append(
            (
                label,
                f"{before.retrieval_seconds:.2f}s",
                f"{after.retrieval_seconds:.2f}s",
                f"{before.retrieval_seconds / after.retrieval_seconds:.2f}x"
                if after.retrieval_seconds > 0
                else "-",
            )
        )
    rows.append(
        (
            "mean",
            f"{sequential.retrieval_seconds / len(sequential.measured):.2f}s",
            f"{concurrent.retrieval_seconds / len(concurrent.measured):.2f}s",
            f"{_speedup(sequential, concurrent):.2f}x",
        )
    )
    return [
        "Retrieval per question",
        *_column(
            rows,
            (
                "question",
                f"seq (bound={sequential.bound})",
                f"con (bound={concurrent.bound})",
                "speedup",
            ),
        ),
    ]


def _stages(sequential: Run, concurrent: Run) -> list[str]:
    """Retrieval, synthesis and end-to-end, side by side."""
    both_answered = sequential.synthesis_complete and concurrent.synthesis_complete
    rows = [
        (
            "retrieval",
            f"{sequential.retrieval_seconds:.2f}s",
            f"{concurrent.retrieval_seconds:.2f}s",
            f"{_ratio(sequential.retrieval_seconds, concurrent.retrieval_seconds):.2f}x",
        ),
    ]
    if both_answered:
        rows += [
            (
                "synthesis (control)",
                f"{sequential.synthesis_seconds:.2f}s",
                f"{concurrent.synthesis_seconds:.2f}s",
                f"{_ratio(sequential.synthesis_seconds, concurrent.synthesis_seconds):.2f}x",
            ),
            (
                "end-to-end",
                f"{sequential.total_seconds:.2f}s",
                f"{concurrent.total_seconds:.2f}s",
                f"{_ratio(sequential.total_seconds, concurrent.total_seconds):.2f}x",
            ),
        ]
    rows.append(
        (
            "sweep wall clock",
            f"{sequential.wall_seconds:.2f}s",
            f"{concurrent.wall_seconds:.2f}s",
            f"{_ratio(sequential.wall_seconds, concurrent.wall_seconds):.2f}x",
        )
    )
    lines = [
        "Stage totals over the sweep",
        *_column(
            rows,
            (
                "stage",
                f"seq (bound={sequential.bound})",
                f"con (bound={concurrent.bound})",
                "speedup",
            ),
        ),
    ]
    if not both_answered:
        lines += [
            "",
            "synthesis and end-to-end withheld — not every question reached an answer:",
            f"  sequential answered {sequential.answered}/{len(sequential.measured)}",
            f"  concurrent answered {concurrent.answered}/{len(concurrent.measured)}",
            "A refused synthesis returns in about a second, so an end-to-end figure "
            "taken now would compare real work against failures and report the "
            "difference as a speedup. The retrieval row above is unaffected: it is "
            "measured before synthesis runs and stands on its own.",
        ]
    return lines


def _ratio(baseline: float, candidate: float) -> float:
    """A guarded ratio, so an empty sweep cannot divide the report by zero."""
    return baseline / candidate if candidate > 0 else 0.0


def _per_source(runs: Sequence[Run]) -> list[str]:
    """Latency and reliability per source, per mode."""
    lines: list[str] = []
    for run in runs:
        grouped: dict[str, list[SourceOutcome]] = {}
        for outcome in run.outcomes():
            grouped.setdefault(outcome.source.value, []).append(outcome)
        rows = [
            (
                name,
                str(len(outcomes)),
                str(sum(1 for o in outcomes if o.status is SourceStatus.SUCCESS)),
                str(sum(1 for o in outcomes if o.status is SourceStatus.EMPTY)),
                str(sum(1 for o in outcomes if o.status in FAILED_STATUSES)),
                f"{statistics.fmean(o.elapsed_seconds for o in outcomes):.2f}s",
                f"{max(o.elapsed_seconds for o in outcomes):.2f}s",
            )
            for name, outcomes in sorted(grouped.items())
        ]
        lines.append(f"Per source — {run.label}")
        lines.extend(_column(rows, ("source", "n", "ok", "empty", "failed", "mean", "max")))
        lines.append("")
    # The caller adds spacing between tables.
    return lines[:-1]


def render(sequential: Run, concurrent: Run, *, plan_note: str) -> str:
    """The whole report, as it goes to the terminal."""
    per_question = sequential.measured
    failures = sequential.failure_count + concurrent.failure_count
    sections = [
        _rule(),
        "Sequential vs concurrent retrieval",
        _rule(),
        f"{len(per_question)} questions, {plan_note}",
        f"cache disabled; {failures} source failure(s) across both sweeps",
        "",
        *_per_question(sequential, concurrent),
        "",
        *_stages(sequential, concurrent),
        "",
        *_per_source((sequential, concurrent)),
        "",
    ]
    return "\n".join(sections)


def markdown(sequential: Run, concurrent: Run) -> str:
    """The README table, generated rather than transcribed."""
    count = len(sequential.measured)
    rows = [
        (
            "Retrieval only",
            f"{count} questions x {len(sequential.measured[0].selected)} sources",
            f"{sequential.retrieval_seconds:.1f} s",
            f"{concurrent.retrieval_seconds:.1f} s",
            f"**{_speedup(sequential, concurrent):.2f}x**",
        ),
        (
            "Per question, retrieval",
            "mean",
            f"{sequential.retrieval_seconds / count:.2f} s",
            f"{concurrent.retrieval_seconds / count:.2f} s",
            f"**{_speedup(sequential, concurrent):.2f}x**",
        ),
    ]
    if sequential.synthesis_complete and concurrent.synthesis_complete:
        # Compare end-to-end timings only when both modes produced all answers.
        rows.append(
            (
                "End-to-end",
                f"{count} questions",
                f"{sequential.total_seconds:.1f} s",
                f"{concurrent.total_seconds:.1f} s",
                f"**{_ratio(sequential.total_seconds, concurrent.total_seconds):.2f}x**",
            )
        )
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    header = (
        f"| Workload | N | Sequential (bound={sequential.bound}) "
        f"| Concurrent (bound={concurrent.bound}) | Speedup |\n"
        "| --- | --- | --- | --- | --- |"
    )
    return f"{header}\n{body}"


def as_dict(sequential: Run, concurrent: Run) -> dict[str, object]:
    """The raw measurements, for the report and for anyone re-plotting them."""
    return {
        "runs": [
            {
                "label": run.label,
                "bound": run.bound,
                "wall_seconds": run.wall_seconds,
                "retrieval_seconds": run.retrieval_seconds,
                "synthesis_seconds": run.synthesis_seconds,
                "total_seconds": run.total_seconds,
                "failures": run.failure_count,
                "answered": run.answered,
                "questions": [
                    {
                        "id": item.identifier,
                        "difficulty": item.difficulty,
                        "sources": [name.value for name in item.selected],
                        "status": item.status.value,
                        "retrieval_seconds": item.retrieval_seconds,
                        "synthesis_seconds": item.synthesis_seconds,
                        "total_seconds": item.total_seconds,
                        "outcomes": [
                            {
                                "source": outcome.source.value,
                                "status": outcome.status.value,
                                "elapsed_seconds": outcome.elapsed_seconds,
                                "attempts": outcome.attempts,
                                "results": len(outcome.sources),
                            }
                            for outcome in item.outcomes
                        ],
                    }
                    for item in run.measured
                ],
            }
            for run in (sequential, concurrent)
        ],
        "retrieval_valid": not sequential.unanswered and not concurrent.unanswered,
        "synthesis_complete": sequential.synthesis_complete and concurrent.synthesis_complete,
        "speedup": {
            "retrieval": _speedup(sequential, concurrent),
            # Only include end-to-end speedup when both answer-generation runs finished.
            "end_to_end": (
                _ratio(sequential.total_seconds, concurrent.total_seconds)
                if sequential.synthesis_complete and concurrent.synthesis_complete
                else None
            ),
        },
    }


# Entry point


def build_parser() -> argparse.ArgumentParser:
    """The argument surface. Kept small: a benchmark should not need a manual."""
    parser = argparse.ArgumentParser(
        prog="bench",
        description=(
            "Measure retrieval and end-to-end wall time with the sources fetched "
            "one after another and then together. Spends real API quota."
        ),
    )
    parser.add_argument(
        "--sources",
        choices=("all", "declared"),
        default="all",
        help=(
            "which sources to ask each question of. 'all' (default) is the "
            "controlled experiment — a question with one source has nothing to "
            "overlap, so only 'all' can show the effect. 'declared' uses each "
            "question's expected_sources and is the realistic workload."
        ),
    )
    parser.add_argument(
        "--bound",
        type=int,
        default=CONCURRENT_BOUND,
        help=f"concurrency bound for the concurrent mode (default {CONCURRENT_BOUND}).",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help=(
            "skip the discarded warm-up question. Faster and cheaper, but the "
            "mode that runs first pays every host's DNS and TLS handshake."
        ),
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=PAUSE_SECONDS,
        metavar="SECONDS",
        help=(
            f"wait between questions (default {PAUSE_SECONDS:g}). The free tier "
            "limits requests per minute, and a sweep questions the synthesizer "
            "faster than that allows; the wait is between questions, never "
            "inside one, so it cannot affect the retrieval timings. Set 0 to "
            "measure on a paid quota."
        ),
    )
    parser.add_argument("--json", type=Path, default=None, help="write the raw measurements here.")
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="print the README table instead of the full report.",
    )
    return parser


async def run(args: argparse.Namespace, settings: Settings) -> int:
    """Run both modes and report. Returns the process exit status."""
    questions = load_demo_questions()
    plan = _plan(questions, declared=args.sources == "declared")
    warm = not args.no_warmup

    pause = max(0.0, args.pause)
    sequential = await _run_mode(
        settings, "sequential", SEQUENTIAL_BOUND, plan, warm=warm, pause=pause
    )
    concurrent = await _run_mode(settings, "concurrent", args.bound, plan, warm=warm, pause=pause)

    note = (
        "every source per question (controlled)"
        if args.sources == "all"
        else "each question's declared sources"
    )
    if pause > 0:
        # Report the pause because it adds to the total sweep time.
        note += f"; {pause:g}s between questions"
    if args.markdown:
        print(markdown(sequential, concurrent))
    else:
        print(render(sequential, concurrent, plan_note=note))

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(as_dict(sequential, concurrent), indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nraw measurements written to {args.json}", file=sys.stderr)

    # Missing answers make the end-to-end comparison incomplete.
    # Check retrieval and answer generation separately.
    problems: list[str] = []
    unanswered = sequential.unanswered + concurrent.unanswered
    if unanswered:
        problems.append(f"{len(unanswered)} question(s) produced no usable sources")
    if not (sequential.synthesis_complete and concurrent.synthesis_complete):
        problems.append("synthesis did not complete in both modes")

    if problems:
        print(
            f"\n{'; '.join(problems)}. The end-to-end comparison is withheld; treat "
            "the retrieval figures alone as the result.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, load settings, and run the benchmark."""
    args = build_parser().parse_args(argv)
    try:
        # Use the same settings loader as the CLI.
        settings = get_settings()
    except ConfigurationError as exc:
        # Exit with status 2 for configuration errors, before sending requests.
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
