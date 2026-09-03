"""Constrained raw PDF/HTML downloader for Topic Survey."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from openjiuwen.core.foundation.tool.base import Tool, ToolCard

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import to_project_relative

_TIMEOUT_SECONDS = 60
_MAX_PDF_CANDIDATES = 12
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

    def __init__(self, *, download_dir: Path, project_root: Path) -> None:
        super().__init__(
            ToolCard(
                id="download_survey_source",
                name="download_survey_source",
                description=(
                    "Download a public URL as its original PDF or HTML into the current "
                    "Topic Survey sources directory. HTML downloads return likely PDF links."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Public http(s) source URL."},
                        "filename": {
                            "type": "string",
                            "description": "Optional filename stem; directories and extensions are ignored.",
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
            if not (absolute.lower().split("?", 1)[0].endswith(".pdf") or "/pdf/" in absolute.lower() or _PDF_LINK_TEXT.search(label)):
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

        filename = _safe_filename(str((inputs or {}).get("filename", "") or ""))
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status >= 400:
                        return {"success": False, "error": f"HTTP {response.status} for {url}"}
                    body = await response.content.read()
                    final_url = str(response.url)
                    content_type = response.headers.get("Content-Type", "").lower()
        except aiohttp.ClientError as exc:
            return {"success": False, "error": f"download failed: {exc}"}

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
            "local_path": to_project_relative(target, root=self._project_root),
            "bytes_downloaded": len(body),
        }
        if not is_pdf:
            result["pdf_candidates"] = self._pdf_candidates(body, base_url=final_url)
        return result

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield await self.invoke(inputs, **kwargs)


__all__ = ["DownloadSurveySourceTool"]
