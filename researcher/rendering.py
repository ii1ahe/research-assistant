"""Turning a :class:`ResearchResult` into text a person can read.

The split this module exists to enforce is between what the user *asked for* and
what *happened*. The answer and its numbered references are the product; source
outcomes, warnings and timings are the report. They leave by different streams —
:func:`render_answer` to stdout, :func:`render_diagnostics` to stderr — so that
``researcher ask "…" > answer.md`` writes a file worth keeping instead of one
with a timing table stapled to the bottom.

Both functions are pure: they return strings and print nothing. That is what
makes them testable without capturing a stream, and it keeps the choice of
stream in one place, in ``cli.py``.

One rule is load-bearing and is why references are built from
``result.answer.citations`` and never from ``result.outcomes``. A source that
failed is *reported*, and a source that returned nothing is *reported*, but
neither may appear as a numbered reference. A citation number that resolves to
something the answer did not use is worse than no citation at all — it is a
citation that lies. ADR-005 states this as a requirement; the adjacency of the
two fields in :class:`~researcher.models.ResearchResult` is exactly what would
make it easy to violate.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai.schemas import AnswerWithCitations
from researcher.models import (
    PersistenceStatus,
    ResearchResult,
    ResultStatus,
    SourceOutcome,
    SourceStatus,
)

__all__ = ["render_answer", "render_diagnostics", "render_summary"]

#: Width of the source-name column in the diagnostics table.
_NAME_WIDTH = 10
#: Width of the status column.
_STATUS_WIDTH = 11

_STATUS_LABELS: dict[SourceStatus, str] = {
    SourceStatus.SUCCESS: "ok",
    SourceStatus.EMPTY: "no results",
    SourceStatus.TIMEOUT: "timed out",
    SourceStatus.ERROR: "failed",
}

#: What to say when there is no answer, per status. ``SUCCESS`` and ``PARTIAL``
#: are absent because they always carry one.
_NO_ANSWER: dict[ResultStatus, str] = {
    ResultStatus.NO_SOURCES: "No answer was produced: no usable sources were retrieved.",
    ResultStatus.FAILED: "No answer was produced: synthesis failed.",
}


def _plural(count: int, noun: str) -> str:
    """Return ``"1 result"`` or ``"3 results"``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _elapsed(seconds: float) -> str:
    """Format a duration for a table cell."""
    return f"{seconds:.2f}s"


def render_answer(result: ResearchResult) -> str:
    """Render the product: the question, the answer, and its references.

    Args:
        result: The finished run.

    Returns:
        Text for stdout. A run that produced no answer renders the question and
        a one-line reason; the explanation of *why* goes to
        :func:`render_diagnostics`, so the two do not repeat each other.
    """
    lines = [f"Question: {result.question}", ""]

    if result.answer is None:
        lines.append(_NO_ANSWER.get(result.status, "No answer was produced."))
        return "\n".join(lines)

    lines.extend([result.answer.answer.strip(), ""])
    lines.extend(_render_references(result.answer))
    return "\n".join(lines)


def _render_references(answer: AnswerWithCitations) -> list[str]:
    """Render the numbered reference list, in citation order.

    Snippets are deliberately omitted. They are carried on the source and are
    archived with the session, but a terminal answer is read, not studied — the
    URL is what a reader follows, and the supplied ``to_dict`` drops snippets at
    the same boundary for the same reason.
    """
    if not answer.citations:
        # A defensible outcome: the model may answer from the sources without
        # marking them. Saying so is better than an empty heading the reader
        # would take for a rendering bug.
        return ["References", "none — the answer cites no sources."]

    lines = ["References"]
    width = max(len(str(citation.index)) for citation in answer.citations)
    for citation in answer.citations:
        marker = f"[{citation.index}]".ljust(width + 2)
        lines.append(f"{marker} {citation.source.title}")
        lines.append(f"{' ' * (width + 2)} {citation.source.url}")
    return lines


def render_diagnostics(result: ResearchResult) -> str:
    """Render the report: what each source did, what went wrong, how long it took.

    Args:
        result: The finished run.

    Returns:
        Text for stderr. Never empty — per-source timing is a stated part of the
        deliverable, so it is shown on every run rather than only on a bad one.
    """
    lines = ["", "sources", *(_source_line(outcome) for outcome in result.outcomes)]

    if result.warnings:
        lines.extend(["", "warnings", *(f"  - {note}" for note in result.warnings)])

    if result.persistence is PersistenceStatus.FAILED:
        lines.append("")
        lines.append("note: this run was not recorded; see the warnings above.")

    lines.extend(["", _timing_line(result)])
    return "\n".join(lines)


def _source_line(outcome: SourceOutcome) -> str:
    """Render one row of the per-source table."""
    label = _STATUS_LABELS.get(outcome.status, outcome.status.value)
    name = outcome.source.value.ljust(_NAME_WIDTH)
    status = label.ljust(_STATUS_WIDTH)

    detail = _source_detail(outcome)
    return f"  {name} {status} {detail}".rstrip()


def _source_detail(outcome: SourceOutcome) -> str:
    """Describe what a source produced, in the order a reader cares about it."""
    if outcome.failure is not None:
        # The code, not the message: the message is already a warning below, and
        # a provider's text is not something to put in a table cell.
        return outcome.failure.code

    if outcome.status is SourceStatus.EMPTY:
        return "nothing found"

    found = _plural(len(outcome.sources), "result")
    origin = "cached" if outcome.cache_hit else _elapsed(outcome.elapsed_seconds)
    return f"{found} ({origin})"


def _timing_line(result: ResearchResult) -> str:
    """Render the run's wall-clock breakdown."""
    timing = result.timing
    total = (timing.finished_at - timing.started_at).total_seconds()
    return (
        f"timing  {_elapsed(timing.retrieval_seconds)} retrieval, "
        f"{_elapsed(timing.synthesis_seconds)} synthesis, "
        f"{_elapsed(total)} total"
    )


def render_summary(results: Sequence[ResearchResult]) -> str:
    """Render a one-line tally for a batch of runs, for ``researcher demo``.

    Args:
        results: The runs, in the order they were executed.

    Returns:
        Text for stderr, naming the count of runs that produced an answer and
        listing the ones that did not. Empty for an empty batch, since a batch
        that did not happen has nothing to report.
    """
    if not results:
        return ""

    answered = [result for result in results if result.answer is not None]
    unsaved = [result for result in results if result.persistence is PersistenceStatus.FAILED]

    lines = [f"{len(answered)}/{len(results)} questions answered."]
    for result in results:
        if result.answer is None:
            lines.append(f"  - {result.question} ({result.status.value})")
    if unsaved:
        lines.append(f"  {len(unsaved)} session(s) were not recorded.")
    return "\n".join(lines)
