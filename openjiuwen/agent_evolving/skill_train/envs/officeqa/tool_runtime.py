# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import fnmatch
import html
import json
import os
import re
import socket
import time
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

_MAX_READ_CHARS = 4000
_MAX_GREP_MATCHES = 20
_MAX_GLOB_MATCHES = 50
_MAX_ORACLE_PAGE_CHARS = 24000
_MAX_ORACLE_CONTEXT_CHARS = 80000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0 Safari/537.36"
)
DEFAULT_CUSTOM_SEARCH_URL = "http://apisix.westus2.cloudapp.azure.com/search_tool/search"
DEFAULT_CUSTOM_SEARCH_AUTH_ENV = "OFFICEQA_CUSTOM_SEARCH_AUTH"
DEFAULT_CUSTOM_SEARCH_PROVIDER = "duckduckgo"
DEFAULT_CUSTOM_SEARCH_MAX_RESULTS = 4
DEFAULT_CUSTOM_SEARCH_TIMEOUT = 20
DEFAULT_CUSTOM_SEARCH_MAX_RETRIES = 4
DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS = 1.0


def _normalize_data_dirs(data_dirs: list[str] | tuple[str, ...] | str | None, project_root: Path) -> list[str]:
    if data_dirs is None:
        return []
    if isinstance(data_dirs, str):
        items = [part.strip() for chunk in data_dirs.split(os.pathsep) for part in chunk.split(",")]
    else:
        items = [str(item).strip() for item in data_dirs]
    resolved: list[str] = []
    for item in items:
        if not item:
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = project_root / path
        resolved.append(str(path))
    return resolved


def resolve_docs_roots(data_dirs: list[str] | tuple[str, ...] | str | None = None) -> list[str]:
    project_root = Path(__file__).resolve().parents[3]
    env_value = os.environ.get("OFFICEQA_DOCS_DIR", "").strip()
    candidates = _normalize_data_dirs(data_dirs, project_root)
    candidates.extend(_normalize_data_dirs(env_value, project_root))
    candidates.extend([
        str(project_root / "data" / "officeqa_docs_official"),
        str(project_root / "data" / "officeqa_smoke_docs"),
        os.path.expanduser("~/officeqa-sparse/treasury_bulletins_parsed"),
        os.path.expanduser("~/officeqa/treasury_bulletins_parsed"),
    ])
    roots: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_dir():
            continue
        transformed = path / "transformed"
        resolved = str((transformed if transformed.is_dir() else path).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
    if not roots:
        raise FileNotFoundError("OfficeQA docs directory not found. Set OFFICEQA_DOCS_DIR or env.data_dirs.")
    return roots


def _is_allowed(path: str, allowed_roots: list[str], allowed_files: list[str]) -> bool:
    try:
        resolved = str(Path(path).resolve())
    except FileNotFoundError:
        return False
    if not any(resolved.startswith(root + os.sep) or resolved == root for root in allowed_roots):
        return False
    if not allowed_files:
        return True
    base = os.path.basename(resolved)
    return base in allowed_files


def resolve_candidate_files(source_files: list[str], allowed_roots: list[str]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for root in allowed_roots:
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if source_files and filename not in source_files:
                    continue
                full = str(Path(dirpath, filename).resolve())
                if full in seen:
                    continue
                seen.add(full)
                resolved.append(full)
    return resolved


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, list):
        return [str(item).strip() for item in loaded if str(item).strip()]
    if "\n" in text:
        return [part.strip() for part in text.splitlines() if part.strip()]
    return [text]


def _extract_page_number(source_doc: str) -> int | None:
    text = str(source_doc or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    for key in ("page", "pagenum", "page_id"):
        for raw_value in query.get(key, []):
            try:
                return int(str(raw_value).strip())
            except ValueError:
                continue
    match = re.search(r"(?:[?&]|^)page=(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def _iter_oracle_refs(source_files: object, source_docs: object) -> list[tuple[str, int, str]]:
    files = _as_list(source_files)
    docs = _as_list(source_docs)
    refs: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    if not files or not docs:
        return refs
    for index, source_doc in enumerate(docs):
        page_number = _extract_page_number(source_doc)
        if page_number is None:
            continue
        if index < len(files):
            source_file = files[index]
        elif len(files) == 1:
            source_file = files[0]
        else:
            continue
        key = (source_file, page_number, source_doc)
        if key in seen:
            continue
        seen.add(key)
        refs.append(key)
    return refs


def _parsed_root_candidates(docs_roots: list[str]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in docs_roots:
        path = Path(root).expanduser()
        for candidate in (
            path,
            path.parent,
            path / "treasury_bulletins_parsed",
            path.parent / "treasury_bulletins_parsed",
        ):
            resolved = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(candidate)
    return candidates


def _locate_parsed_json(source_file: str, docs_roots: list[str]) -> Path | None:
    source_path = Path(str(source_file).strip())
    stem = source_path.stem if source_path.suffix else source_path.name
    if not stem:
        return None
    candidate_names = [stem + ".json"]
    if source_path.suffix == ".json":
        candidate_names.insert(0, source_path.name)
    for root in _parsed_root_candidates(docs_roots):
        for name in candidate_names:
            path = root / "jsons" / name
            if path.is_file():
                return path
    return None


class _TableMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"td", "th"} and self._cell is not None and self._row is not None:
            cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(cell)
            self._cell = None
        elif normalized_tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _escape_markdown_cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _html_table_to_markdown(raw_html: str) -> str:
    parser = _TableMarkdownParser()
    try:
        parser.feed(raw_html)
    except Exception:  # noqa: BLE001
        parser.rows = []
    rows = parser.rows
    if not rows:
        text = re.sub(r"(?is)<[^>]+>", " ", raw_html)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()
    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    header = normalized_rows[0]
    body = normalized_rows[1:]
    lines = [
        "| " + " | ".join(_escape_markdown_cell(cell) for cell in header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(_escape_markdown_cell(cell) for cell in row) + " |" for row in body)
    return "\n".join(lines)


def _render_parsed_content(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    if "<table" in text.lower():
        return _html_table_to_markdown(text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _element_page_ids(element: dict) -> set[int]:
    page_ids: set[int] = set()
    bbox = element.get("bbox")
    if not isinstance(bbox, list):
        return page_ids
    for box in bbox:
        if not isinstance(box, dict):
            continue
        raw_page_id = box.get("page_id")
        try:
            page_ids.add(int(raw_page_id))
        except (TypeError, ValueError):
            continue
    return page_ids


@lru_cache(maxsize=256)
def _load_parsed_elements(json_path: str) -> tuple[dict, ...]:
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)
    document = payload.get("document") if isinstance(payload, dict) else {}
    elements = document.get("elements") if isinstance(document, dict) else []
    if not isinstance(elements, list):
        return ()
    return tuple(element for element in elements if isinstance(element, dict))


@lru_cache(maxsize=2048)
def _render_parsed_page(json_path: str, page_number: int) -> str:
    rendered: list[str] = []
    for element in _load_parsed_elements(json_path):
        if page_number not in _element_page_ids(element):
            continue
        content = element.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        section = _render_parsed_content(content)
        if section:
            rendered.append(section)
    return "\n\n".join(rendered).strip()


def build_oracle_parsed_pages_context(
    source_files: object,
    source_docs: object,
    docs_roots: list[str],
    *,
    max_page_chars: int = _MAX_ORACLE_PAGE_CHARS,
    max_total_chars: int = _MAX_ORACLE_CONTEXT_CHARS,
    evidence_note: str = "Treat it as primary document evidence and combine it with custom web search results when useful.",
) -> str:
    """Render oracle parsed OfficeQA pages referenced by source_docs/source_files."""
    refs = _iter_oracle_refs(source_files, source_docs)
    if not refs:
        return ""

    blocks: list[str] = []
    total_chars = 0
    seen_pages: set[tuple[str, int]] = set()
    for source_file, page_number, source_doc in refs:
        json_path = _locate_parsed_json(source_file, docs_roots)
        if json_path is None:
            continue
        page_key = (str(json_path), page_number)
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        page_text = _render_parsed_page(str(json_path), page_number)
        if not page_text:
            continue
        if len(page_text) > max_page_chars:
            omitted = len(page_text) - max_page_chars
            page_text = page_text[:max_page_chars].rstrip() + f"\n\n[... {omitted} characters omitted from this parsed page ...]"
        block = (
            f"### {source_file} page {page_number}\n"
            f"Source URL: {source_doc}\n\n"
            f"{page_text}"
        )
        if total_chars + len(block) > max_total_chars:
            remaining = max_total_chars - total_chars
            if remaining <= 0:
                break
            block = block[:remaining].rstrip() + "\n\n[... oracle parsed page context truncated ...]"
            blocks.append(block)
            break
        blocks.append(block)
        total_chars += len(block)
    if not blocks:
        return ""
    return (
        "The following content is pre-parsed from the oracle OfficeQA source page(s). "
        f"{evidence_note.strip()}\n\n"
        + "\n\n".join(blocks)
    )


def _extract_search_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    candidate_keys = (
        "results",
        "items",
        "data",
        "organic",
        "organic_results",
        "search_results",
        "webPages",
        "value",
    )
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_search_items(value)
            if nested:
                return nested
    return []


def _normalize_search_item(item: dict, index: int) -> str:
    title = str(
        item.get("title")
        or item.get("name")
        or item.get("headline")
        or item.get("source")
        or f"Result {index}"
    ).strip()
    url = str(
        item.get("url")
        or item.get("link")
        or item.get("href")
        or item.get("display_url")
        or ""
    ).strip()
    snippet = str(
        item.get("snippet")
        or item.get("description")
        or item.get("body")
        or item.get("text")
        or item.get("content")
        or ""
    ).strip()
    lines = [f"[{index}] {title}"]
    if url:
        lines.append(f"URL: {url}")
    if snippet:
        lines.append(f"Snippet: {snippet}")
    return "\n".join(lines)


def _format_search_payload(query: str, payload: object) -> str:
    items = _extract_search_items(payload)
    header = f"Query: {query}"
    if not items:
        body = json.dumps(payload, ensure_ascii=False) if payload else "[no results]"
        return f"{header}\n{body}"
    rendered = [_normalize_search_item(item, index) for index, item in enumerate(items, start=1)]
    return f"{header}\n\n" + "\n\n".join(rendered)


def _is_retryable_search_http_error(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500


def custom_search(
    query: str,
    *,
    api_url: str = DEFAULT_CUSTOM_SEARCH_URL,
    auth_token: str | None = None,
    auth_env: str = DEFAULT_CUSTOM_SEARCH_AUTH_ENV,
    provider: str = DEFAULT_CUSTOM_SEARCH_PROVIDER,
    max_num_results: int = DEFAULT_CUSTOM_SEARCH_MAX_RESULTS,
    timeout: int = DEFAULT_CUSTOM_SEARCH_TIMEOUT,
    max_retries: int = DEFAULT_CUSTOM_SEARCH_MAX_RETRIES,
    initial_backoff_seconds: float = DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS,
) -> str:
    query = str(query or "").strip()
    if not query:
        raise ValueError("custom_search query must be non-empty")
    token = str(auth_token or os.environ.get(auth_env, "")).strip()
    if not token:
        raise ValueError(f"custom_search auth token missing; set {auth_env}")
    payload = json.dumps(
        {
            "query": query,
            "max_num_results": int(max_num_results),
            "provider": provider,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        api_url,
        data=payload,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    attempts = max(1, int(max_retries) + 1)
    last_error: RuntimeError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(req, timeout=timeout) as response:
                raw_body = response.read().decode("utf-8", errors="ignore")
            break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(f"custom_search HTTP {exc.code}: {detail[:1000]}")
            if attempt >= attempts or not _is_retryable_search_http_error(exc.code):
                raise last_error from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            last_error = RuntimeError(f"custom_search connection error: {exc}")
            if attempt >= attempts:
                raise last_error from exc
        backoff_seconds = max(0.0, float(initial_backoff_seconds)) * (2 ** (attempt - 1))
        if backoff_seconds > 0:
            time.sleep(backoff_seconds)
    else:
        raise last_error or RuntimeError("custom_search failed without a captured error")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return f"Query: {query}\n\n{raw_body.strip() or '[empty response]'}"
    return _format_search_payload(query, parsed)


def run_tool(name: str, arguments: dict, *, allowed_roots: list[str], allowed_files: list[str]) -> tuple[str, str]:
    if name == "glob":
        pattern = str(arguments.get("pattern") or "*")
        matches: list[str] = []
        for root in allowed_roots:
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    if allowed_files and filename not in allowed_files:
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, filename), root)
                    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(filename, pattern):
                        matches.append(os.path.join(dirpath, filename))
                    if len(matches) >= _MAX_GLOB_MATCHES:
                        break
                if len(matches) >= _MAX_GLOB_MATCHES:
                    break
        return f"glob(pattern={pattern!r})", "\n".join(matches) if matches else "[no matches]"

    if name == "read":
        path = str(arguments.get("path") or "")
        if not path:
            return "read(path='')", "[read error: missing path]"
        if not _is_allowed(path, allowed_roots, allowed_files):
            return f"read(path={path!r})", "[read error: path not allowed]"
        start = max(int(arguments.get("start") or 1), 1)
        limit = max(int(arguments.get("limit") or 80), 1)
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        excerpt = "".join(lines[start - 1:start - 1 + limit])
        return f"read(path={path!r}, start={start}, limit={limit})", excerpt[:_MAX_READ_CHARS] or "[empty file]"

    if name == "grep":
        pattern = str(arguments.get("pattern") or "").lower()
        path = str(arguments.get("path") or "")
        if not pattern or not path:
            return f"grep(pattern={pattern!r}, path={path!r})", "[grep error: missing pattern or path]"
        if not _is_allowed(path, allowed_roots, allowed_files):
            return f"grep(pattern={pattern!r}, path={path!r})", "[grep error: path not allowed]"
        matches: list[str] = []
        with open(path, encoding="utf-8") as f:
            for idx, line in enumerate(f, start=1):
                if pattern in line.lower():
                    matches.append(f"{idx}: {line.rstrip()}")
                if len(matches) >= _MAX_GREP_MATCHES:
                    break
        return f"grep(pattern={pattern!r}, path={path!r})", "\n".join(matches) if matches else "[no matches]"

    return name, f"[tool error: unknown tool {name}]"
