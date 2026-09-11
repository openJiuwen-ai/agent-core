"""Host-owned citation metadata normalization for topic-survey sources."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    CitationMetadata,
)

_YEAR_RE = re.compile(r"\b(\d{4})\b")
_DOI_RE = re.compile(r"(?:^|/)\b(10\.\d{4,9}/[^\s\"'<>?#]+)", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
_EVIDENCE_FIELD_RE = re.compile(
    r"^-\s*\*{0,2}(Authors|Year|Venue|DOI)\*{0,2}:\s*(.*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _DOI_PREFIX_RE.sub("", str(value).strip())
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
    cleaned = cleaned.strip().rstrip(".,;:)]}>")
    return cleaned.lower() or None


def normalize_year(value: str | int | None) -> str | None:
    if value is None:
        return None
    match = _YEAR_RE.search(str(value))
    return match.group(1) if match else None


def doi_from_url(url: str) -> str | None:
    decoded = unquote(str(url or ""))
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.scheme and parsed.netloc:
        searchable = parsed.path
    else:
        searchable = decoded.split("?", 1)[0].split("#", 1)[0]
    match = _DOI_RE.search(searchable)
    if not match:
        return None
    return normalize_doi(match.group(1).split("?", 1)[0].split("#", 1)[0])


class _CitationMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.authors: list[str] = []
        self.year: str | None = None
        self.venue: str | None = None
        self.doi: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): (value or "").strip() for key, value in attrs}
        name = values.get("name", "").lower()
        property_name = values.get("property", "").lower()
        content = values.get("content", "").strip()
        if not content:
            return
        key = name or property_name
        if key in {"citation_author", "dc.creator", "author"}:
            self.authors.append(content)
        elif (
            key
            in {
                "citation_publication_date",
                "citation_date",
                "citation_online_date",
                "dc.date",
                "date",
            }
            and self.year is None
        ):
            self.year = normalize_year(content)
        elif (
            key
            in {
                "citation_journal_title",
                "citation_conference_title",
                "citation_inbook_title",
                "dc.source",
            }
            and self.venue is None
        ):
            self.venue = content
        elif key in {"citation_doi", "dc.identifier", "doi"} and self.doi is None:
            self.doi = normalize_doi(content)


def extract_html_citation_metadata(path: Path) -> CitationMetadata:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return CitationMetadata()
    parser = _CitationMetaParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - metadata extraction is best effort
        return CitationMetadata()
    return CitationMetadata(
        authors=list(dict.fromkeys(parser.authors)),
        year=parser.year,
        venue=parser.venue,
        doi=parser.doi,
    )


def extract_metadata_evidence(path: Path) -> CitationMetadata:
    """Recover fields written by the downloader's metadata-only fallback."""

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return CitationMetadata()
    values = {match.group(1).lower(): match.group(2).strip() for match in _EVIDENCE_FIELD_RE.finditer(text)}

    authors_value = values.get("authors", "")
    authors = (
        []
        if not authors_value or authors_value.lower() == "(unknown)"
        else [item.strip() for item in authors_value.split(",") if item.strip()]
    )

    def _known(value: str | None) -> str | None:
        if not value or value.lower() == "(unknown)":
            return None
        return value

    return CitationMetadata(
        authors=authors,
        year=normalize_year(_known(values.get("year"))),
        venue=_known(values.get("venue")),
        doi=normalize_doi(_known(values.get("doi"))),
    )


def merge_citation_metadata(*items: CitationMetadata) -> CitationMetadata:
    """Fill missing metadata from later candidates without overwriting facts."""

    authors: list[str] = []
    year: str | None = None
    venue: str | None = None
    doi: str | None = None
    for item in items:
        for author in item.authors:
            cleaned = author.strip()
            if cleaned and cleaned not in authors:
                authors.append(cleaned)
        year = year or normalize_year(item.year)
        venue = venue or (item.venue.strip() if item.venue and item.venue.strip() else None)
        doi = doi or normalize_doi(item.doi)
    return CitationMetadata(authors=authors, year=year, venue=venue, doi=doi)


def citation_exclusion_reasons(metadata: CitationMetadata) -> list[str]:
    reasons: list[str] = []
    if not metadata.authors:
        reasons.append("missing_author")
    if not metadata.year:
        reasons.append("missing_year")
    if not metadata.doi:
        reasons.append("missing_doi")
    return reasons


def stable_source_id(url: str) -> str:
    normalized = str(url or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def citation_key(title: str, url: str, metadata: CitationMetadata) -> str | None:
    if citation_exclusion_reasons(metadata):
        return None
    author = metadata.authors[0].split()[-1] if metadata.authors else "source"
    author_slug = re.sub(r"[^a-z0-9]+", "", author.lower())[:20] or "source"
    suffix = hashlib.sha256((normalize_doi(metadata.doi) or str(url) or title).encode("utf-8")).hexdigest()[:8]
    return f"{author_slug}{metadata.year}{suffix}"


@dataclass(frozen=True)
class CitationResolution:
    metadata: CitationMetadata
    key: str | None
    eligible: bool
    exclusion_reasons: list[str]


def resolve_citation(
    *,
    title: str,
    url: str,
    local_path: Path | None = None,
    supplied: CitationMetadata | None = None,
) -> CitationResolution:
    local = CitationMetadata()
    if local_path is not None:
        if local_path.suffix.lower() in {".html", ".htm"}:
            local = extract_html_citation_metadata(local_path)
        elif local_path.name.endswith(".metadata.md"):
            local = extract_metadata_evidence(local_path)
    from_url = CitationMetadata(doi=doi_from_url(url))
    metadata = merge_citation_metadata(supplied or CitationMetadata(), local, from_url)
    reasons = citation_exclusion_reasons(metadata)
    eligible = not reasons
    return CitationResolution(
        metadata=metadata,
        key=citation_key(title, url, metadata) if eligible else None,
        eligible=eligible,
        exclusion_reasons=reasons,
    )


__all__ = [
    "CitationResolution",
    "citation_exclusion_reasons",
    "citation_key",
    "doi_from_url",
    "extract_metadata_evidence",
    "extract_html_citation_metadata",
    "merge_citation_metadata",
    "normalize_doi",
    "normalize_year",
    "resolve_citation",
    "stable_source_id",
]
