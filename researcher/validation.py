"""Input normalisation and output validation.

This module owns two things the rest of the application relies on:

**Entry-point validation.** Raw CLI strings are turned into the typed values of
:mod:`researcher.models`, and those values are then checked against
:class:`researcher.config.Settings`. Everything a user can get wrong — an
unknown source name, an oversized question, a web search with no credential —
is rejected here, with a message that says what to do, before any network call
is made.

**Semantic output validation.** The supplied ``ai.synthesize`` numbers citations
by position in the source list it was given, and drops out-of-range indices from
``AnswerWithCitations.citations``. It does *not* rewrite the answer prose, so an
answer can still contain a ``[7]`` that has no corresponding reference. The
checks here detect that and the related renumbering hazards rather than letting
a dangling citation reach the user.

Source-name aliases live here and nowhere else: the CLI imports them from this
module, so there is exactly one table to keep in step with the supplied
``ai.sources`` factories.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from ai.schemas import AnswerWithCitations, Source
from researcher.config import Settings
from researcher.errors import ConfigurationError, InvalidAnswerError, InvalidRequestError
from researcher.models import ResearchRequest, SourceName

__all__ = [
    "SOURCE_ALIASES",
    "SOURCE_NAMES",
    "canonicalize_query",
    "deduplicate_sources",
    "normalize_source_name",
    "normalize_url",
    "parse_sources",
    "unreferenced_citation_indices",
    "validate_answer",
    "validate_request",
]

#: Canonical source names, in the order used when none is specified.
SOURCE_NAMES: Final[tuple[str, ...]] = tuple(name.value for name in SourceName)

#: Accepted shorthands, normalised to their canonical name. This is the only
#: definition; ``researcher.cli`` imports it rather than repeating it. ``wiki``
#: is the sole alias the README and the CLI help advertise, so it is the sole
#: alias accepted — inventing more would be surface the docs do not promise.
SOURCE_ALIASES: Final[dict[str, str]] = {
    "wiki": "wikipedia",
}

#: Citation markers as the supplied synthesizer defines them: ``[1]``,
#: ``[2,3]`` or ``[2, 3]``. Mirrors ``ai.synthesizer._CITATION_RE`` so that what
#: is detected here is exactly what was generated there.
_CITATION_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

#: Characters stripped from the ends of a question when deriving its cache key.
#: Deliberately a fixed ASCII set rather than ``\W``: punctuation *inside* a
#: question is meaningful, so "CRISPR-Cas9" must not become "CRISPRCas9".
#: Unicode dashes and quotes are left alone; they are rare at the edges of a
#: question, and excluding them keeps the cache key free of ambiguity.
_EDGE_CHARS: Final[str] = " \t\r\n?!.,;:\"'`()[]{}<>*#-/\\|"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def normalize_source_name(raw: str) -> SourceName:
    """Resolve one user-supplied source name to its canonical value.

    Accepts the canonical names, the aliases in :data:`SOURCE_ALIASES`, and any
    mix of case and surrounding whitespace.

    Raises:
        InvalidRequestError: The name is not a known source or alias.
    """
    candidate = raw.strip().casefold()
    if not candidate:
        raise InvalidRequestError("source name must not be blank")
    candidate = SOURCE_ALIASES.get(candidate, candidate)
    try:
        return SourceName(candidate)
    except ValueError:
        accepted = ", ".join(SOURCE_NAMES)
        aliases = ", ".join(f"{alias}={canonical}" for alias, canonical in SOURCE_ALIASES.items())
        raise InvalidRequestError(
            f"unknown source {raw.strip()!r}. Choose from: {accepted}. Aliases: {aliases}."
        ) from None


def parse_sources(raw: str | Iterable[str] | None) -> tuple[SourceName, ...]:
    """Parse a comma-separated source list into canonical names.

    Blank entries are ignored, so ``"arxiv,"`` behaves as ``"arxiv"``.
    Duplicates are collapsed while preserving the order in which the user
    listed them, because that order determines citation numbering.

    Args:
        raw: A comma-separated string, an iterable of such strings, or ``None``
            to select every source in the canonical order.

    Raises:
        InvalidRequestError: No source remains after parsing, or a name is
            not recognised.
    """
    if raw is None:
        return tuple(SourceName)

    tokens: list[str] = []
    if isinstance(raw, str):
        tokens.extend(raw.split(","))
    else:
        for item in raw:
            tokens.extend(str(item).split(","))

    parsed: list[SourceName] = []
    for token in tokens:
        if not token.strip():
            continue
        name = normalize_source_name(token)
        if name not in parsed:
            parsed.append(name)

    if not parsed:
        raise InvalidRequestError(
            f"at least one source must be selected. Choose from: {', '.join(SOURCE_NAMES)}."
        )
    return tuple(parsed)


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for comparison and cache identity.

    Normalisation is intentionally shallow. It removes only differences that
    cannot change which resource is addressed — scheme case, host case, a
    leading ``www.``, a trailing slash and the fragment. Query strings are
    preserved because they frequently *do* select the resource.

    ``http`` and ``https`` are treated as the same resource, since deduplicating
    two links to one page is safe and the alternative ships a duplicate.
    """
    raw = url.strip()
    if not raw:
        return raw

    parts = urlsplit(raw)
    if not parts.scheme and not parts.netloc:
        return raw.casefold()

    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme.casefold()
    host = parts.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, parts.query, ""))


def deduplicate_sources(sources: Iterable[Source]) -> tuple[Source, ...]:
    """Collapse sources that point at the same resource.

    The first occurrence of a URL is kept and the original ordering is
    preserved, because the position of a source in this list is what fixes its
    citation number: reordering here would silently renumber every reference.
    """
    seen: set[str] = set()
    unique: list[Source] = []
    for source in sources:
        key = normalize_url(source.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return tuple(unique)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def canonicalize_query(question: str) -> str:
    """Derive the cache-normal form of a question.

    Two questions map to the same cache entry when they differ only in case,
    surrounding whitespace, internal whitespace runs, or trailing punctuation.
    Word order and wording are preserved: anything more aggressive risks
    returning one question's cached sources for a different question.

    The original question is never replaced by this form. It is used only as
    cache-key material.

    Raises:
        InvalidRequestError: Nothing remains after normalisation, which happens
            when the question consists solely of punctuation.
    """
    collapsed = _WHITESPACE_RE.sub(" ", question).strip()
    canonical = collapsed.casefold().strip(_EDGE_CHARS).strip()
    if not canonical:
        raise InvalidRequestError("question must contain more than punctuation and whitespace")
    return canonical


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def validate_request(request: ResearchRequest, settings: Settings) -> ResearchRequest:
    """Check a request against the active configuration.

    Structural rules — a non-empty question, at least one distinct source — are
    already enforced by :class:`~researcher.models.ResearchRequest`. This adds
    the limits that come from configuration, plus the cross-checks that turn a
    confusing runtime provider failure into an immediate, actionable message.

    Raises:
        InvalidRequestError: The question or result limit is out of range.
        ConfigurationError: The request needs a provider that is not usable,
            such as a web search with no credential configured.
    """
    question = request.question.strip()
    if not question:
        raise InvalidRequestError("question must be non-empty")

    limit = settings.max_question_length
    if len(question) > limit:
        raise InvalidRequestError(
            f"question is {len(question)} characters; the limit is {limit}. "
            "Raise MAX_QUESTION_LENGTH to allow longer questions."
        )

    if request.max_results > settings.max_results_per_source:
        raise InvalidRequestError(
            f"--max-results {request.max_results} exceeds the configured limit of "
            f"{settings.max_results_per_source}. Raise MAX_RESULTS_PER_SOURCE to allow it."
        )

    if SourceName.WEB in request.sources:
        _check_web_search_available(settings)

    return request


def _check_web_search_available(settings: Settings) -> None:
    """Ensure the configured web-search provider can actually be used.

    The supplied providers raise ``ProviderError`` when constructed without a
    credential, which would surface only once retrieval had already started and
    other sources had been fetched. Checking up front means the run fails
    cleanly with exit status 2 and nothing wasted.
    """
    if settings.web_search_provider == "duckduckgo":
        # DuckDuckGo needs no credential but does need its optional package.
        import importlib.util

        if importlib.util.find_spec("duckduckgo_search") is None:
            raise ConfigurationError(
                "WEB_SEARCH_PROVIDER is 'duckduckgo' but the 'duckduckgo-search' "
                "package is not installed. Install it, or set WEB_SEARCH_PROVIDER "
                "to 'tavily' or 'serper' with a matching API key, or drop 'web' "
                "from --sources."
            )
    elif not settings.web_search_api_key:
        variable = (
            "TAVILY_API_KEY" if settings.web_search_provider == "tavily" else "SERPER_API_KEY"
        )
        raise ConfigurationError(
            f"the 'web' source is selected but {variable} is not set for "
            f"WEB_SEARCH_PROVIDER={settings.web_search_provider!r}. Set {variable}, "
            "or drop 'web' from --sources."
        )


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------


def validate_answer(answer: AnswerWithCitations, sources: Sequence[Source]) -> AnswerWithCitations:
    """Check that an answer's references are internally consistent.

    Verifies that every citation index is positive, within range, unique, and
    still points at the source occupying that position. The last check is the
    important one: the supplied synthesizer fixes each citation to
    ``sources[index - 1]`` at the moment it builds the answer, so a mismatch
    means the source list was reordered afterwards and every reference number
    in the prose now points at the wrong source.

    These checks establish internal consistency only. They cannot and do not
    claim that a source actually supports the claim it is attached to.

    Raises:
        InvalidAnswerError: A citation is out of range, duplicated, or points
            at a source other than the one at its index.
    """
    total = len(sources)
    seen: set[int] = set()
    for citation in answer.citations:
        if citation.index < 1:
            raise InvalidAnswerError(
                f"citation index {citation.index} is not positive; reference numbering is 1-based",
                source="synthesis",
            )
        if citation.index > total:
            raise InvalidAnswerError(
                f"citation index {citation.index} is out of range for {total} source(s)",
                source="synthesis",
            )
        if citation.index in seen:
            raise InvalidAnswerError(
                f"citation index {citation.index} appears more than once",
                source="synthesis",
            )
        seen.add(citation.index)

        expected = sources[citation.index - 1]
        if citation.source != expected:
            raise InvalidAnswerError(
                f"citation {citation.index} points at {citation.source.url!r} but "
                f"that position holds {expected.url!r}; the source list was "
                "reordered after the answer was built",
                source="synthesis",
            )
    return answer


def unreferenced_citation_indices(answer: AnswerWithCitations) -> tuple[int, ...]:
    """Return markers that appear in the prose but have no matching reference.

    The supplied synthesizer removes out-of-range indices from
    ``AnswerWithCitations.citations`` without editing the prose, so an answer
    stating a fact "according to [7]" can ship with only three references. Those
    numbers are reported here so the caller can warn the user instead of
    presenting a dangling citation as though it resolved.
    """
    referenced = {citation.index for citation in answer.citations}
    dangling: set[int] = set()
    for match in _CITATION_RE.finditer(answer.answer):
        for piece in match.group(1).split(","):
            try:
                index = int(piece.strip())
            except ValueError:
                continue
            if index not in referenced:
                dangling.add(index)
    return tuple(sorted(dangling))
