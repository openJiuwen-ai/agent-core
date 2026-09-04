"""Deterministic refs.bib builder — see docs/paper_writing_design.md §2.

No live citation search: every source here was already found and downloaded
by ``topic_survey``. This module does two things, both host-side and
deterministic:

1. Recover ``title``/``url``/``local_path`` per source. ``ResearchBrief``
   (``ReportingInput.survey``) only carries a flat ``resource_paths: list[str]``
   — no structured title/url. But ``topic_survey/artifacts.py::write_survey_artifacts``
   already renders exactly that structured data into the curated
   ``research_summary.md`` (``resource_paths[0]``, the same file
   ``reporting`` reads per ``docs/reporting_design.md`` §4) as a
   ``### N. [title](link)`` / ``- **URL:** ...`` block per source. This is
   an implicit format contract on that rendering (same category of
   implicit contract ``reporting_design.md`` open question 1 already flags
   for ``resource_paths[0]`` itself) — parsed here rather than duplicated,
   not re-derived from scratch.
2. Best-effort extract author/year/venue/DOI. First choice: if the source's
   own URL already carries an arXiv id or a DOI, resolve it against the
   arXiv API / Crossref for complete, verified metadata — this is
   *enrichment* of a source ``topic_survey`` already found and downloaded,
   never new literature search, so it doesn't reopen the citation-
   hallucination risk ``docs/reporting_design.md``'s "External research"
   section flags for live search (``docs/paper_writing_design.md`` §2 is
   explicit that live *search* stays out of this module; resolving an
   identifier the source's own URL already contains is a narrower, safer
   thing). Network-optional and best-effort: any failure (offline,
   timeout, no id in the URL) falls back to the second choice, PDF
   metadata / HTML ``citation_*`` <meta> tags, exactly as before. Falls
   back further to a ``@misc`` entry keyed on title+URL when nothing can be
   recovered, rather than inventing author/year.

The network fetch logic (arXiv API / Crossref lookup, BibTeX field mapping)
is ported from spark-to-paper-skills' ``ts-paper-cite/scripts/doi2bib.py``
(MIT license, Albus White) — fetched and read directly from its actual
source, not reconstructed from a summary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.latex import escape_latex

_SOURCE_BLOCK_RE = re.compile(
    r"^### \d+\.\s*\[(?P<title>.+?)\]\((?P<link>[^)]+)\)\s*\n\n"
    r"- \*\*URL:\*\* (?P<url>\S+)",
    re.MULTILINE,
)


@dataclass
class ParsedSource:
    title: str
    url: str
    local_path: Path


def parse_survey_sources(summary_path: Path) -> list[ParsedSource]:
    """Recover title/url/local_path per source from the ``## Sources``
    section of ``research_summary.md``. Returns ``[]`` (not an error) if the
    file is missing or doesn't match the expected shape — a paper with no
    recoverable bibliography still gets built, just with an empty
    ``refs.bib``, same "degrade, don't crash" stance every other module in
    this pipeline takes for missing optional input.
    """
    try:
        text = summary_path.read_text(encoding="utf-8")
    except OSError:
        return []
    sources: list[ParsedSource] = []
    for match in _SOURCE_BLOCK_RE.finditer(text):
        link = match.group("link").strip()
        local_path = (summary_path.parent / link).resolve()
        sources.append(
            ParsedSource(title=match.group("title").strip(), url=match.group("url").strip(), local_path=local_path)
        )
    return sources


@dataclass
class BibEntry:
    key: str
    title: str
    url: str
    authors: list[str] = field(default_factory=list)
    year: str | None = None
    venue: str | None = None
    doi: str | None = None

    def to_bibtex(self) -> str:
        # Reuse latex.py's escape_latex rather than a narrow brace-only
        # escape — a recovered title/URL can contain any LaTeX special
        # character (e.g. "SWE-bench Leaderboard & Framework"), and BibTeX
        # fields are typeset by the same LaTeX engine section prose is.
        entry_type = "article" if self.authors and self.year else "misc"
        lines = [f"@{entry_type}{{{self.key},"]
        lines.append(f"  title = {{{escape_latex(self.title)}}},")
        if self.authors:
            lines.append(f"  author = {{{' and '.join(escape_latex(a) for a in self.authors)}}},")
        if self.year:
            lines.append(f"  year = {{{self.year}}},")
        if self.venue:
            lines.append(f"  journal = {{{escape_latex(self.venue)}}},")
        if self.doi:
            lines.append(f"  doi = {{{self.doi}}},")
        lines.append(f"  howpublished = {{{escape_latex(self.url)}}},")
        lines.append("}")
        return "\n".join(lines)


class _CitationMetaParser(HTMLParser):
    """Pulls Google-Scholar-style ``citation_*`` <meta> tags out of an HTML
    source — a common convention on paper landing pages (arXiv, ACM, IEEE)."""

    def __init__(self) -> None:
        super().__init__()
        self.authors: list[str] = []
        self.title: str | None = None
        self.year: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        name = attr_map.get("name", "").lower()
        content = attr_map.get("content", "").strip()
        if not content:
            return
        if name == "citation_author":
            self.authors.append(content)
        elif name == "citation_title" and self.title is None:
            self.title = content
        elif name in ("citation_publication_date", "citation_date", "citation_online_date") and self.year is None:
            match = re.search(r"(19|20)\d{2}", content)
            if match:
                self.year = match.group(0)


def _extract_html_metadata(path: Path) -> tuple[list[str], str | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], None
    parser = _CitationMetaParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - best-effort metadata extraction, never fatal
        return [], None
    return parser.authors, parser.year


def _extract_pdf_metadata(path: Path) -> tuple[list[str], str | None]:
    try:
        from pypdf import (
            PdfReader,  # optional dependency — see pyproject.toml [paper_writing]
        )
    except ImportError:
        return [], None
    try:
        reader = PdfReader(str(path))
        info = reader.metadata or {}
    except Exception:  # noqa: BLE001 - best-effort metadata extraction, never fatal
        return [], None
    author_field = str(info.get("/Author") or "").strip()
    authors = [a.strip() for a in re.split(r",| and ", author_field) if a.strip()] if author_field else []
    year = None
    match = re.search(r"(19|20)\d{2}", str(info.get("/CreationDate") or ""))
    if match:
        year = match.group(0)
    return authors, year


def _slug(text: str, length: int = 16) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", text.lower())
    return slug[:length] or "source"


def _bib_key(title: str, url: str, authors: list[str], year: str | None) -> str:
    if authors and year:
        last_name = authors[0].split()[-1] if authors[0].split() else authors[0]
        return f"{_slug(last_name)}{year}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(title)}{digest}"


_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+?)(?:[.)\]]*)(?:[\s\"'<>]|$)")


@dataclass
class _EnrichedMetadata:
    authors: list[str]
    year: str | None
    venue: str | None
    doi: str | None


def _fetch_crossref(doi: str, *, timeout: float) -> dict | None:
    """One Crossref lookup for a known DOI — ported from doi2bib.py's
    ``fetch``. Returns ``None`` on any HTTP/parse failure rather than
    raising; the caller treats that identically to "no id found"."""
    import json as _json
    import urllib.parse
    import urllib.request

    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "auto-research-reporting/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return _json.load(resp)["message"]


def _fetch_arxiv(arxiv_id: str, *, timeout: float) -> dict | None:
    """One arXiv API lookup for a known id, normalized to the same
    author/issued/container-title shape ``_fetch_crossref`` returns —
    ported from doi2bib.py's ``fetch_arxiv``."""
    import urllib.parse
    import urllib.request
    from xml.etree import ElementTree as ET

    url = "http://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    req = urllib.request.Request(url, headers={"User-Agent": "auto-research-reporting/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed http host
        root = ET.fromstring(resp.read())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None or entry.find("a:id", ns) is None:
        return None
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    year = published[:4]
    authors = []
    for author_el in entry.findall("a:author", ns):
        name = (author_el.findtext("a:name", default="", namespaces=ns) or "").strip()
        given, _, family = name.rpartition(" ")
        authors.append({"given": given, "family": family or name})
    return {
        "author": authors,
        "issued": {"date-parts": [[int(year)]]} if year.isdigit() else {},
        "container-title": ["arXiv preprint"],
        "DOI": None,
    }


def _message_to_metadata(message: dict) -> _EnrichedMetadata:
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in message.get("author", [])
        if a.get("family")
    ]
    date_parts = (message.get("issued") or {}).get("date-parts") or [[None]]
    year = str(date_parts[0][0]) if date_parts and date_parts[0] and date_parts[0][0] else None
    venue = (message.get("container-title") or [None])[0]
    return _EnrichedMetadata(authors=authors, year=year, venue=venue, doi=message.get("DOI") or None)


def try_enrich_from_network(url: str, *, timeout: float = 5.0) -> _EnrichedMetadata | None:
    """Best-effort metadata enrichment for a source URL that already
    carries an arXiv id or a DOI. Returns ``None`` (never raises) when the
    URL has neither, or when the lookup fails for any reason (offline,
    timeout, 404) — the caller falls back to PDF/HTML metadata scraping
    exactly as before this existed. This resolves an identifier the source
    already has, it never searches for a new one — see the module
    docstring for why that distinction keeps this out of the
    citation-hallucination risk live search would reopen.
    """
    arxiv_match = _ARXIV_URL_RE.search(url)
    doi_match = None if arxiv_match else _DOI_URL_RE.search(url)
    if not arxiv_match and not doi_match:
        return None
    try:
        message = (
            _fetch_arxiv(arxiv_match.group(1), timeout=timeout)
            if arxiv_match
            else _fetch_crossref(doi_match.group(1), timeout=timeout)
        )
    except Exception:  # noqa: BLE001 - best-effort network call, never fatal
        return None
    if message is None:
        return None
    return _message_to_metadata(message)


@dataclass
class Bibliography:
    bib_text: str
    # title -> bibkey, so the section-writing prompt can tell the model
    # which \cite{key} corresponds to which already-surveyed source.
    title_to_key: dict[str, str]
    known_keys: set[str]


def build_bibliography(summary_path: Path, *, network_timeout: float = 5.0) -> Bibliography:
    parsed_sources = parse_survey_sources(summary_path)
    entries: list[BibEntry] = []
    title_to_key: dict[str, str] = {}
    seen_keys: set[str] = set()
    for source in parsed_sources:
        enriched = try_enrich_from_network(source.url, timeout=network_timeout)
        if enriched is not None:
            authors, year, venue, doi = enriched.authors, enriched.year, enriched.venue, enriched.doi
        else:
            if source.local_path.suffix.lower() == ".pdf":
                authors, year = _extract_pdf_metadata(source.local_path)
            else:
                authors, year = _extract_html_metadata(source.local_path)
            venue, doi = None, None
        key = _bib_key(source.title, source.url, authors, year)
        while key in seen_keys:
            key = f"{key}x"
        seen_keys.add(key)
        entries.append(
            BibEntry(key=key, title=source.title, url=source.url, authors=authors, year=year, venue=venue, doi=doi)
        )
        title_to_key[source.title] = key
    bib_text = "\n\n".join(entry.to_bibtex() for entry in entries)
    return Bibliography(bib_text=bib_text, title_to_key=title_to_key, known_keys=seen_keys)
