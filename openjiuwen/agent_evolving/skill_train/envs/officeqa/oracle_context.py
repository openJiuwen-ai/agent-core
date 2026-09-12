# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Oracle page excerpt builder for OfficeQA items."""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse

_PAGE_CHAR_BUDGET = 24000
_TOTAL_CHAR_BUDGET = 80000
_PAGE_PARAM_KEYS = ("page", "pagenum", "page_id")
_PARSED_CORPUS_LABEL = "treasury_bulletins_parsed"
_DEFAULT_USAGE_HINT = (
    "Ground answers in the page excerpts below; treat controller web hits as "
    "supplemental only when they align with or extend those pages."
)
_TRUNC_SUFFIX = "[... {omitted} characters omitted from this parsed page ...]"
_TOTAL_TRUNC_SUFFIX = "[... oracle parsed page context truncated ...]"


class _PageAnchor(NamedTuple):
    doc_name: str
    page_num: int
    doc_url: str


class _TableGridParser(HTMLParser):
    """Walk HTML tables and collect a rectangular cell grid."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.grid: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._row.append(text)
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if any(self._row):
                self.grid.append(self._row)
            self._row = None
            self._cell_parts = None


def _normalize_listish(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(entry).strip() for entry in raw if str(entry).strip()]
    text = str(raw).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        return [str(entry).strip() for entry in decoded if str(entry).strip()]
    if "\n" in text:
        return [line.strip() for line in text.splitlines() if line.strip()]
    return [text]


def _page_from_url(reference: str) -> int | None:
    text = str(reference or "").strip()
    if not text:
        return None
    query = parse_qs(urlparse(text).query)
    for key in _PAGE_PARAM_KEYS:
        for raw in query.get(key, []):
            try:
                return int(str(raw).strip())
            except ValueError:
                continue
    matched = re.search(r"(?:[?&]|^)page=(\d+)", text)
    return int(matched.group(1)) if matched else None


def _anchors_from_item(source_files: object, source_docs: object) -> list[_PageAnchor]:
    files = _normalize_listish(source_files)
    docs = _normalize_listish(source_docs)
    if not files or not docs:
        return []

    anchors: list[_PageAnchor] = []
    seen: set[tuple[str, int, str]] = set()
    for idx, url in enumerate(docs):
        page = _page_from_url(url)
        if page is None:
            continue
        if idx < len(files):
            doc_name = files[idx]
        elif len(files) == 1:
            doc_name = files[0]
        else:
            continue
        key = (doc_name, page, url)
        if key in seen:
            continue
        seen.add(key)
        anchors.append(_PageAnchor(doc_name, page, url))
    return anchors


def _vault_path_from_spec(anchor: Path, *, ascend: int, attach_parsed: bool) -> Path:
    node = anchor
    for _ in range(ascend):
        node = node.parent
    if attach_parsed:
        node = node / _PARSED_CORPUS_LABEL
    return node


def _json_search_roots(docs_roots: list[str]) -> list[Path]:
    specs = ((0, False), (1, False), (0, True), (1, True))
    catalog: list[Path] = []
    seen: set[str] = set()
    for raw in docs_roots:
        anchor = Path(raw).expanduser()
        for ascend, attach_parsed in specs:
            candidate = _vault_path_from_spec(anchor, ascend=ascend, attach_parsed=attach_parsed)
            token = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if token in seen:
                continue
            seen.add(token)
            catalog.append(candidate)
    return catalog


def _json_basename_candidates(source_file: str) -> list[str]:
    path = Path(str(source_file).strip())
    if not path.name:
        return []
    base = path.name if path.suffix.lower() == ".json" else f"{path.stem or path.name}.json"
    variants = [base]
    if path.suffix.lower() == ".json" and path.stem:
        alt = f"{path.stem}.json"
        if alt not in variants:
            variants.append(alt)
    return variants


def _resolve_json_path(source_file: str, docs_roots: list[str]) -> Path | None:
    for root in _json_search_roots(docs_roots):
        for name in _json_basename_candidates(source_file):
            candidate = root.joinpath("jsons", name)
            if candidate.is_file():
                return candidate
    return None


def _md_cell(cell: str) -> str:
    return str(cell).replace("\n", " ").replace("|", "\\|").strip()


def _table_html_to_md(raw_html: str) -> str:
    parser = _TableGridParser()
    try:
        parser.feed(raw_html)
    except Exception:  # noqa: BLE001
        parser.grid = []
    if not parser.grid:
        plain = re.sub(r"(?is)<[^>]+>", " ", raw_html)
        return re.sub(r"\s+", " ", html.unescape(plain)).strip()

    width = max(len(row) for row in parser.grid)
    padded = [row + [""] * (width - len(row)) for row in parser.grid]
    header, *body = padded
    lines = [
        "| " + " | ".join(_md_cell(cell) for cell in header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _render_element_body(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    if "<table" in text.lower():
        return _table_html_to_md(text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _bbox_page_ids(element: dict) -> frozenset[int]:
    raw_boxes = element.get("bbox")
    if not isinstance(raw_boxes, list):
        return frozenset()
    page_ids: set[int] = set()
    for box in raw_boxes:
        if not isinstance(box, dict):
            continue
        page_token = box.get("page_id")
        try:
            page_ids.add(int(page_token))
        except (TypeError, ValueError):
            pass
    return frozenset(page_ids)


@lru_cache(maxsize=256)
def _load_document_elements(json_path: str) -> tuple[dict, ...]:
    text = Path(json_path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    root = payload if isinstance(payload, dict) else {}
    document = root.get("document")
    if not isinstance(document, dict):
        return ()
    elements = document.get("elements")
    if not isinstance(elements, list):
        return ()
    return tuple(el for el in elements if isinstance(el, dict))


@lru_cache(maxsize=2048)
def _assemble_page_body(json_path: str, page_id: int) -> str:
    rendered: list[str] = []
    for element in _load_document_elements(json_path):
        if page_id not in _bbox_page_ids(element):
            continue
        raw_content = element.get("content")
        if not isinstance(raw_content, str):
            continue
        snippet = _render_element_body(raw_content)
        if snippet:
            rendered.append(snippet)
    return "\n\n".join(rendered).strip()


def _truncate_with_notice(text: str, limit: int, *, notice: str) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    head = text[:limit].rstrip()
    return f"{head}\n\n{notice.format(omitted=omitted)}"


def _section_block(doc_name: str, page: int, url: str, body: str) -> str:
    return f"#### {doc_name} · page {page}\nSource: {url}\n\n{body}"


def build_oracle_parsed_pages_context(
    source_files: object,
    source_docs: object,
    docs_roots: list[str],
    *,
    max_page_chars: int = _PAGE_CHAR_BUDGET,
    max_total_chars: int = _TOTAL_CHAR_BUDGET,
    evidence_note: str = _DEFAULT_USAGE_HINT,
) -> str:
    """Build a markdown block of oracle page text for the given item refs."""
    anchors = _anchors_from_item(source_files, source_docs)
    if not anchors:
        return ""

    sections: list[str] = []
    consumed = 0
    visited: set[tuple[str, int]] = set()
    for anchor in anchors:
        json_path = _resolve_json_path(anchor.doc_name, docs_roots)
        if json_path is None:
            continue
        visit_key = (str(json_path), anchor.page_num)
        if visit_key in visited:
            continue
        visited.add(visit_key)

        body = _assemble_page_body(str(json_path), anchor.page_num)
        if not body:
            continue
        body = _truncate_with_notice(body, max_page_chars, notice=_TRUNC_SUFFIX)
        block = _section_block(anchor.doc_name, anchor.page_num, anchor.doc_url, body)

        if consumed + len(block) > max_total_chars:
            leftover = max_total_chars - consumed
            if leftover <= 0:
                break
            truncated = block[:leftover].rstrip()
            sections.append(f"{truncated}\n\n{_TOTAL_TRUNC_SUFFIX}")
            break
        sections.append(block)
        consumed += len(block)

    if not sections:
        return ""
    lead = (
        "Pre-extracted OfficeQA oracle pages (parsed text) follow. "
        f"{evidence_note.strip()}\n\n"
    )
    return lead + "\n\n".join(sections)
