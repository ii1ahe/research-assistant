"""What the user sees, and — more importantly — what they never see.

The functions here are pure, so these tests read the strings they return rather
than capturing a stream. That is deliberate: the choice of stream is a policy
decision made once in ``cli.py``, and mixing it into these tests would mean
testing the same decision eighteen times instead of once.

The claim worth guarding is the one ADR-005 states and the models enforce: a
source that failed, timed out or came back empty is *reported*, and is never
*referenced*. A citation number that resolves to something the answer did not
use is worse than no citation, because it reads as evidence.
"""

from __future__ import annotations

import pytest

from ai.schemas import AnswerWithCitations, Citation
from researcher.models import (
    FailureDetail,
    PersistenceStatus,
    ResultStatus,
    SourceName,
    SourceOutcome,
    SourceStatus,
)
from researcher.rendering import render_answer, render_diagnostics, render_summary
from tests.conftest import make_result, make_source, make_success


def _failed(source: SourceName, code: str = "upstream_error") -> SourceOutcome:
    return SourceOutcome(
        source=source,
        status=SourceStatus.ERROR,
        failure=FailureDetail(code=code, message="the provider refused the connection"),
        elapsed_seconds=0.2,
    )


def _empty(source: SourceName) -> SourceOutcome:
    return SourceOutcome(source=source, status=SourceStatus.EMPTY, elapsed_seconds=0.3)


def _timed_out(source: SourceName) -> SourceOutcome:
    return SourceOutcome(
        source=source,
        status=SourceStatus.TIMEOUT,
        failure=FailureDetail(code="upstream_timeout", message="the request took too long"),
    )


def _references_of(rendered: str) -> str:
    """Return everything after the ``References`` heading."""
    _, _, tail = rendered.partition("References")
    return tail


# ---------------------------------------------------------------------------
# The product
# ---------------------------------------------------------------------------


def test_the_question_the_answer_and_the_references_are_all_rendered() -> None:
    result = make_result(question="what is photosynthesis")

    rendered = render_answer(result)

    assert rendered.startswith("Question: what is photosynthesis")
    assert "A claim [1]." in rendered
    assert "References" in rendered


def test_each_reference_carries_its_title_and_url() -> None:
    """A reader follows the URL; a number with no URL is not a reference."""
    result = make_result(
        outcomes=[make_success(SourceName.WIKIPEDIA)],
    )
    source = result.answer.citations[0].source

    rendered = render_answer(result)

    assert f"[1] {source.title}" in rendered
    assert source.url in rendered


def test_reference_markers_stay_aligned_past_nine() -> None:
    """A two-digit number must not push its title out of the column."""
    sources = [make_source("web", n) for n in range(1, 11)]
    answer = AnswerWithCitations(
        question="q",
        answer=" ".join(f"Claim [{index}]." for index in range(1, 11)),
        citations=[
            Citation(index=index, source=source) for index, source in enumerate(sources, start=1)
        ],
    )
    result = make_result(answer=answer)

    tail = _references_of(render_answer(result))
    title_lines = [line for line in tail.splitlines() if line.startswith("[")]

    # Every marker is padded to the width of the widest, so every title starts
    # in the same column however many digits the number has.
    assert len(title_lines) == 10
    assert {line.index(sources[i].title) for i, line in enumerate(title_lines)} == {5}


def test_an_answer_that_cites_nothing_says_so_rather_than_showing_a_bare_heading() -> None:
    answer = AnswerWithCitations(question="q", answer="An answer from nowhere in particular.")
    result = make_result(answer=answer)

    rendered = render_answer(result)

    assert "none — the answer cites no sources." in rendered


def test_a_run_with_no_answer_states_the_reason_for_its_status() -> None:
    result = make_result(status=ResultStatus.NO_SOURCES)

    rendered = render_answer(result)

    assert "No answer was produced: no usable sources were retrieved." in rendered
    assert "References" not in rendered


def test_a_failed_run_is_distinguished_from_one_with_no_sources() -> None:
    result = make_result(status=ResultStatus.FAILED)

    assert "synthesis failed" in render_answer(result)


# ---------------------------------------------------------------------------
# The rule that matters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", [_empty, _failed, _timed_out], ids=["empty", "error", "timeout"]
)
def test_a_source_that_did_not_succeed_is_never_rendered_as_a_reference(
    outcome: SourceOutcome,
) -> None:
    """Reported in the diagnostics, absent from the references."""
    degraded = outcome(SourceName.ARXIV)
    result = make_result(outcomes=[make_success(SourceName.WIKIPEDIA), degraded])

    references = _references_of(render_answer(result))
    diagnostics = render_diagnostics(result)

    assert "arxiv" not in references
    assert "arxiv" in diagnostics


def test_a_non_success_outcome_cannot_even_hold_a_source() -> None:
    """The rendering rule is enforced by the model, not merely observed by it.

    Worth asserting directly, because it is *why* the renderer can build
    references from the answer alone and be certain a dead source cannot appear:
    there is no representable state in which one has a source to offer.
    """
    with pytest.raises(ValueError, match="must not carry sources"):
        SourceOutcome(
            source=SourceName.ARXIV,
            status=SourceStatus.ERROR,
            sources=(make_source("arxiv"),),
            failure=FailureDetail(code="upstream_error", message="failed"),
        )


def test_references_come_from_the_answer_not_from_every_outcome() -> None:
    """A source that contributed but was not cited is still not a reference."""
    outcome = make_success(SourceName.WIKIPEDIA, count=3)
    answer = AnswerWithCitations(
        question="q",
        answer="Only the first is used [1].",
        citations=[Citation(index=1, source=outcome.sources[0])],
    )
    result = make_result(answer=answer, outcomes=[outcome])

    references = _references_of(render_answer(result))

    # Three sources were retrieved; one was cited.
    assert "wikipedia result 1" in references
    assert "wikipedia result 2" not in references


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_every_source_gets_a_row_whatever_happened_to_it() -> None:
    result = make_result(
        outcomes=[
            make_success(SourceName.WIKIPEDIA, count=2),
            _empty(SourceName.ARXIV),
            _failed(SourceName.WEB),
        ]
    )

    diagnostics = render_diagnostics(result)

    assert "wikipedia" in diagnostics
    assert "arxiv" in diagnostics
    assert "web" in diagnostics
    assert "ok" in diagnostics
    assert "no results" in diagnostics
    assert "failed" in diagnostics


def test_a_timed_out_source_is_distinguished_from_a_failed_one() -> None:
    """Different causes, different fixes: patience versus investigation."""
    result = make_result(outcomes=[_timed_out(SourceName.ARXIV)])

    assert "timed out" in render_diagnostics(result)


def test_a_cached_source_is_labelled_as_cached_rather_than_timed() -> None:
    """A cache hit performs no request, so its row reports no duration."""
    result = make_result(outcomes=[make_success(SourceName.WIKIPEDIA, cache_hit=True)])

    row = next(line for line in render_diagnostics(result).splitlines() if "wikipedia" in line)

    assert "(cached)" in row
    assert "0.40s" not in row


def test_a_failed_source_shows_its_code_rather_than_a_provider_message() -> None:
    """The message is already in the warnings; a table cell is not the place."""
    result = make_result(outcomes=[_failed(SourceName.WEB, code="rate_limited")])

    diagnostics = render_diagnostics(result)

    assert "rate_limited" in diagnostics
    assert "the provider refused the connection" not in diagnostics


def test_warnings_are_listed_verbatim() -> None:
    note = "arxiv was unavailable (upstream_error): the provider refused the connection"
    result = make_result(warnings=[note])

    assert f"- {note}" in render_diagnostics(result)


def test_diagnostics_are_never_empty() -> None:
    """Per-source timing is part of the deliverable, so it is always shown."""
    diagnostics = render_diagnostics(make_result(outcomes=[make_success()]))

    assert "timing" in diagnostics
    assert "warnings" not in diagnostics


def test_the_timing_line_reports_retrieval_synthesis_and_total() -> None:
    result = make_result()

    line = next(line for line in render_diagnostics(result).splitlines() if "timing" in line)

    assert "0.60s retrieval" in line
    assert "0.40s synthesis" in line
    assert "1.00s total" in line


def test_a_failed_write_is_called_out_where_the_reader_is_looking() -> None:
    result = make_result(persistence=PersistenceStatus.FAILED)

    assert "not recorded" in render_diagnostics(result)


def test_a_skipped_write_is_not_reported_as_a_problem() -> None:
    """An unconfigured database is a choice, not a fault."""
    result = make_result(persistence=PersistenceStatus.SKIPPED)

    assert "not recorded" not in render_diagnostics(result)


# ---------------------------------------------------------------------------
# The batch summary
# ---------------------------------------------------------------------------


def test_the_summary_counts_the_questions_that_were_answered() -> None:
    results = [
        make_result(question="one"),
        make_result(question="two"),
        make_result(question="three", status=ResultStatus.NO_SOURCES),
    ]

    summary = render_summary(results)

    assert "2/3 questions answered." in summary
    assert "three (no_sources)" in summary


def test_the_summary_names_unsaved_sessions() -> None:
    results = [make_result(persistence=PersistenceStatus.FAILED)]

    assert "1 session(s) were not recorded." in render_summary(results)


def test_an_empty_batch_has_nothing_to_summarise() -> None:
    assert render_summary([]) == ""
