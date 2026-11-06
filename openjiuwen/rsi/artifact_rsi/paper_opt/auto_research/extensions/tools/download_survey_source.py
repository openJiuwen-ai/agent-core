"""Constrained raw PDF/HTML downloader for Topic Survey."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS, _domain_allowed

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import to_project_relative
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.citations import (
    normalize_doi,
    normalize_year,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    CitationMetadata,
)

_TIMEOUT_SECONDS = 60
_MAX_PDF_CANDIDATES = 12
_MAX_FALLBACK_EVIDENCE_CHARS = 12_000
_PDF_LINK_TEXT = re.compile(r"(?:download|view|full\s*text)?\s*pdf", re.IGNORECASE)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:100] or "source"


def _is_http_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https"}


class DownloadSurveySourceTool(Tool):
    """Download one public source as its original PDF or HTML response.

    The destination directory is fixed by the host at construction time. The
    model supplies only a URL and optional filename, so it cannot write outside
    the current survey's ``sources/`` folder.
    """

    def __init__(
        self,
        *,
        download_dir: Path,
        project_root: Path,
        proxy_url: str | None = None,
        allowed_domains: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(
            ToolCard(
                id="download_survey_source",
                name="download_survey_source",
                description=(
                    "Download a public URL as its original PDF or HTML into the current "
                    "Topic Survey sources directory. HTML downloads return likely PDF links. "
                    "When a download fails, optional fallback_evidence can persist a usable "
                    "abstract or search snippet without retrying the URL."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Public http(s) source URL."},
                        "filename": {
                            "type": "string",
                            "description": "Optional filename stem; directories and extensions are ignored.",
                        },
                        "fallback_evidence": {
                            "type": "object",
                            "description": (
                                "Optional source metadata and abstract/search evidence to save "
                                "if this one download attempt fails. It never triggers a second "
                                "HTTP request."
                            ),
                            "properties": {
                                "title": {"type": "string"},
                                "source_type": {"type": "string", "enum": ["paper", "web_page"]},
                                "authors": {"type": "array", "items": {"type": "string"}},
                                "year": {"type": "string"},
                                "venue": {"type": "string"},
                                "doi": {"type": "string"},
                                "abstract": {"type": "string"},
                                "search_snippet": {"type": "string"},
                                "evidence_text": {"type": "string"},
                                "summary": {"type": "string"},
                                "key_findings": {"type": "array", "items": {"type": "string"}},
                                "limitations": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "required": ["url"],
                },
                parallel_safe=True,
                idempotent=True,
            )
        )
        self._download_dir = download_dir.resolve()
        self._project_root = project_root.resolve()
        self._proxy_url = str(proxy_url or "").strip() or None
        self._allowed_domains = allowed_domains

    def _write_metadata_fallback(
        self,
        *,
        url: str,
        fallback: dict[str, Any],
        error: str,
    ) -> Path | None:
        """Persist usable metadata without making another request.

        This is deliberately part of the existing downloader contract rather
        than a proxy-specific rail.  A direct download that succeeds keeps the
        exact original PDF/HTML path; this path is used only after the one
        attempted request failed.
        """

        citation = self._normalize_fallback_citation(fallback)
        evidence_parts = [
            str(fallback.get("abstract") or "").strip(),
            str(fallback.get("search_snippet") or "").strip(),
            str(fallback.get("evidence_text") or "").strip(),
            str(fallback.get("summary") or "").strip(),
        ]
        if not any(evidence_parts):
            return None

        source_id = hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:12]
        target = self._download_dir / f"source-{source_id}.metadata.md"
        findings = fallback.get("key_findings") or []
        if not isinstance(findings, list):
            findings = [str(findings)]
        limitations = fallback.get("limitations") or []
        if not isinstance(limitations, list):
            limitations = [str(limitations)]

        def _section(title: str, value: str) -> str:
            value = value.strip()[:_MAX_FALLBACK_EVIDENCE_CHARS]
            return f"## {title}\n\n{value or '(not available)'}\n"

        lines = [
            "# Metadata-only Survey Evidence",
            "",
            f"- URL: {url}",
            f"- Title: {str(fallback.get('title') or url).strip()}",
            f"- Source type: {str(fallback.get('source_type') or 'paper').strip()}",
            "- Retrieval mode: metadata_only",
            f"- Download failure: {error}",
            f"- Authors: {', '.join(citation.authors) or '(unknown)'}",
            f"- Year: {citation.year or '(unknown)'}",
            f"- Venue: {citation.venue or '(unknown)'}",
            f"- DOI: {citation.doi or '(unknown)'}",
            "",
            _section("Abstract", str(fallback.get("abstract") or "")),
            _section("Search Snippet", str(fallback.get("search_snippet") or "")),
            _section("Fetched Evidence", str(fallback.get("evidence_text") or "")),
            _section("Summary", str(fallback.get("summary") or "")),
            "## Key Findings",
            "",
            "".join(f"- {str(item).strip()}\n" for item in findings if str(item).strip()) or "- (not available)\n",
            "## Limitations",
            "",
            "".join(f"- {str(item).strip()}\n" for item in limitations if str(item).strip())
            or "- Download failed; evidence is metadata-only.\n",
        ]
        self._download_dir.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    @staticmethod
    def _normalize_fallback_citation(fallback: dict[str, Any]) -> CitationMetadata:
        authors = fallback.get("authors") or []
        if not isinstance(authors, list):
            authors = [authors]
        venue = fallback.get("venue")
        return CitationMetadata(
            authors=[str(item) for item in authors if item is not None],
            year=normalize_year(fallback.get("year")),
            venue=str(venue) if venue is not None else None,
            doi=normalize_doi(fallback.get("doi")),
        )

    def _failure_result(
        self,
        *,
        url: str,
        error: str,
        fallback: Any,
    ) -> dict[str, Any]:
        if isinstance(fallback, dict):
            target = self._write_metadata_fallback(url=url, fallback=fallback, error=error)
            if target is not None:
                source_type = str(fallback.get("source_type") or "paper").strip()
                if source_type not in {"paper", "web_page"}:
                    source_type = "paper"
                citation_metadata = self._normalize_fallback_citation(fallback).model_dump(mode="json")
                result: dict[str, Any] = {
                    "success": True,
                    "url": url,
                    "title": str(fallback.get("title") or url).strip(),
                    "source_type": source_type,
                    "retrieval_mode": "metadata_only",
                    "downloaded": False,
                    "local_path": to_project_relative(target, root=self._project_root),
                    "download_error": error,
                    "message": (
                        "The URL was not downloaded. Metadata-only evidence was saved; "
                        "continue with another accessible source when possible."
                    ),
                    "citation": citation_metadata,
                    "citation_metadata": citation_metadata,
                }
                return result
        return {"success": False, "error": error}

    @staticmethod
    def _pdf_candidates(html: bytes, *, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            absolute = urljoin(base_url, href)
            label = anchor.get_text(" ", strip=True)
            if not _is_http_url(absolute):
                continue
            is_pdf_link = (
                absolute.lower().split("?", 1)[0].endswith(".pdf")
                or "/pdf/" in absolute.lower()
                or _PDF_LINK_TEXT.search(label)
            )
            if not is_pdf_link:
                continue
            if absolute not in seen:
                seen.add(absolute)
                candidates.append(absolute)
            if len(candidates) >= _MAX_PDF_CANDIDATES:
                break
        return candidates

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        url = str((inputs or {}).get("url", "") or "").strip()
        if not _is_http_url(url):
            return {"success": False, "error": "url must be an http(s) URL"}
        if not _domain_allowed(url, self._allowed_domains):
            return {
                "success": False,
                "error": "URL is outside the configured domestic academic source domains",
            }

        filename = _safe_filename(str((inputs or {}).get("filename", "") or ""))
        fallback = (inputs or {}).get("fallback_evidence")
        try:
            async with _http.new_session() as session:
                status, headers, body, final_url, _truncated = await _http.request(
                    session,
                    "GET",
                    url,
                    headers=_REQUEST_HEADERS,
                    timeout_seconds=_TIMEOUT_SECONDS,
                    max_bytes=None,
                    proxy_url=self._proxy_url,
                )
            if status >= 400:
                return self._failure_result(
                    url=url,
                    error=f"HTTP {status} for {url}",
                    fallback=fallback,
                )
            if not _domain_allowed(final_url, self._allowed_domains):
                return self._failure_result(
                    url=url,
                    error="redirected URL is outside the configured domestic academic source domains",
                    fallback=fallback,
                )
            content_type = headers.get("Content-Type", "").lower()
        except Exception as exc:  # noqa: BLE001 - surface transport errors to the agent
            return self._failure_result(
                url=url,
                error=f"download failed: {exc}",
                fallback=fallback,
            )

        is_pdf = "application/pdf" in content_type or body.startswith(b"%PDF-")
        extension = ".pdf" if is_pdf else ".html"
        if not filename or filename == "source":
            filename = f"source-{hashlib.sha256(final_url.encode('utf-8')).hexdigest()[:12]}"
        target = self._download_dir / f"{Path(filename).stem}{extension}"
        self._download_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

        result: dict[str, Any] = {
            "success": True,
            "url": url,
            "final_url": final_url,
            "source_type": "pdf" if is_pdf else "html",
            "retrieval_mode": "downloaded_pdf" if is_pdf else "downloaded_html",
            "downloaded": True,
            "local_path": to_project_relative(target, root=self._project_root),
            "bytes_downloaded": len(body),
        }
        if not is_pdf:
            candidates = self._pdf_candidates(body, base_url=final_url)
            if self._allowed_domains:
                candidates = [
                    candidate for candidate in candidates if _domain_allowed(candidate, self._allowed_domains)
                ]
            result["pdf_candidates"] = candidates
        return result

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield await self.invoke(inputs, **kwargs)


__all__ = ["DownloadSurveySourceTool"]
