"""Checks questions and source names, builds cache keys, removes duplicate URLs, and checks
citation references.
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

# Define aliases once; the CLI imports this table.
SOURCE_ALIASES: Final[dict[str, str]] = {
    "wiki": "wikipedia",
}

# Match citation forms such as [1] and [2, 3], as the supplied generator does.
_CITATION_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

# Strip edge punctuation, but keep punctuation inside words.
# Keep # so questions about C# and C do not share a cache key.
_EDGE_CHARS: Final[str] = " \t\r\n?!.,;:\"'`()[]{}<>*-/\\|"


# Sources


def normalize_source_name(raw: str) -> SourceName:
    """Resolve one user-supplied source name to its canonical value.

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
    """Build a URL key for duplicate checks. Ignore scheme and host case, www., a trailing slash,
    and fragments. Treat HTTP and HTTPS as the same page; keep query strings.
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
    """Keep the first source for each URL, in its original position, so citation numbers stay
    stable.
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


# Queries


def canonicalize_query(question: str) -> str:
    """Build a cache key by normalizing case, spaces, and edge punctuation. Keep word order and
    characters inside words, including #. The original question is still sent to providers.

    Raises:
        InvalidRequestError: The question contains only punctuation.
    """
    collapsed = _WHITESPACE_RE.sub(" ", question).strip()
    canonical = collapsed.casefold().strip(_EDGE_CHARS).strip()
    if not canonical:
        raise InvalidRequestError("question must contain more than punctuation and whitespace")
    return canonical


# Request validation


def validate_request(request: ResearchRequest, settings: Settings) -> ResearchRequest:
    """Check a request against the active configuration.

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
    """Ensure the configured web-search provider can actually be used."""
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


# Answer validation


def validate_answer(answer: AnswerWithCitations, sources: Sequence[Source]) -> AnswerWithCitations:
    """Reject blank answers and invalid, repeated, or mismatched citation references. Answers
    without citations are allowed. These checks do not prove that a source supports a claim.

    Raises:
        InvalidAnswerError: The answer is blank or a citation reference is inconsistent.
    """
    if not answer.answer.strip():
        raise InvalidAnswerError(
            "the synthesizer returned an empty answer; there is nothing to report",
            source="synthesis",
        )

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
    """Return markers that appear in the prose but have no matching reference."""
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
