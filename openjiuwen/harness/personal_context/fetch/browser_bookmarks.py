"""Windows Microsoft Edge bookmarks provider for the embedded PersonalContext core."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    candidate_in_time_range,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.retry import (
    classify_file_error,
    classify_payload_error,
    classify_transport_error,
    retry_provider_read,
)
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 20
_PAGE_TIMEOUT_SECONDS = 8.0
_MAX_PAGE_CHARS = 12_000
_MAX_BOOKMARKS_BYTES = 16 * 1024 * 1024
_MAX_PAGE_RESPONSE_BYTES = 4 * 1024 * 1024
_PAGE_CHUNK_SIZE = 64 * 1024
_MAX_PAGE_REDIRECTS = 5
_PAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 PersonalContextBrowserBookmarks/1.0"
)
_MAX_BOOKMARK_ID_CHARS = 256
_MAX_TITLE_CHARS = 4_096
_MAX_URL_CHARS = 4_096
_MAX_FOLDER_CHARS = 4_096
_MAX_DATE_ADDED_CHARS = 128
_ROOT_NAMES = {
    "bookmark_bar": "收藏夹栏",
    "other": "其他书签",
    "synced": "移动设备书签",
}


def _file_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR, error_msg=message, cause=cause)


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)


def _safe_error(exc: BaseException) -> str:
    message = re.sub(r"https?://\S+", "<redacted-url>", str(exc))
    return " ".join(message.split())[:512] or exc.__class__.__name__


def _canonical_fingerprint(*values: str) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _logical_id(url: str) -> str:
    normalized = _normalize_bookmark_url(url) or url.strip()
    return f"browser_bookmarks:edge:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _normalize_bookmark_url(value: str) -> str | None:
    """Normalize safe URLs without exposing malformed or credentialed URLs."""
    text = value.strip()
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        if not hostname or parsed.username is not None or parsed.password is not None:
            return None
        host = hostname.casefold()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        return f"{scheme}://{host}{parsed.path or '/'}{f'?{parsed.query}' if parsed.query else ''}"
    if not scheme:
        return None
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _redact_userinfo_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if parsed.username is None and parsed.password is None:
            return None
    except ValueError:
        return None
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"redacted-url:{digest}"


def _normalize_folder_path(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("bookmark folder path must be a string")
    return "/".join(part.strip() for part in value.replace("\\", "/").split("/") if part.strip())


def _folder_matches(path: str, filters: tuple[str, ...], *, include_subfolders: bool) -> bool:
    if not filters:
        return True
    normalized = _normalize_folder_path(path)
    return any(
        normalized == folder or (include_subfolders and normalized.startswith(f"{folder}/")) for folder in filters
    )


def _date_sort_value(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _bookmark_candidate_time(value: str) -> str:
    microseconds = _date_sort_value(value)
    if microseconds < 0:
        raise ValueError("bookmark date_added is invalid")
    epoch = datetime(1601, 1, 1, tzinfo=UTC)
    return (epoch + timedelta(microseconds=microseconds)).isoformat().replace("+00:00", "Z")


def _bounded_text(value: object, *, name: str, limit: int, fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        value = fallback
    if not isinstance(value, str):
        raise ValueError(f"bookmark {name} must be a string")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"bookmark {name} exceeds the size limit")
    return text


def _source_values(config: Any) -> tuple[Path, str, tuple[str, ...], bool, bool]:
    source = config.source
    if not isinstance(source, Mapping):
        raise _fetch_error("browser bookmarks source is invalid")
    raw_path = source.get("bookmarks_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise _fetch_error("browser bookmarks path is missing")
    bookmarks_path = Path(os.path.expanduser(raw_path))
    if not bookmarks_path.is_absolute():
        raise _fetch_error("browser bookmarks path must be absolute")
    profile = source.get("profile", "Default")
    if not isinstance(profile, str) or not profile.strip():
        raise _fetch_error("browser bookmarks profile is invalid")
    raw_filters = source.get("bookmark_folder_paths", [])
    if not isinstance(raw_filters, (list, tuple)):
        raise _fetch_error("bookmark folder paths must be a list")
    try:
        normalized_filters: list[str] = []
        for item in raw_filters:
            filter_path = _normalize_folder_path(item)
            if filter_path:
                normalized_filters.append(filter_path)
        filters = tuple(normalized_filters)
    except ValueError as exc:
        raise _fetch_error("bookmark folder path is invalid", exc) from None
    include_subfolders = source.get("include_subfolders", True)
    fetch_page_content = source.get("fetch_page_content", True)
    if not isinstance(include_subfolders, bool) or not isinstance(fetch_page_content, bool):
        raise _fetch_error("bookmark boolean options are invalid")
    return bookmarks_path, profile.strip(), filters, include_subfolders, fetch_page_content


def _read_bookmarks_file(path: Path) -> object:
    try:
        if not path.exists() or not path.is_file():
            raise _file_error("Edge Bookmarks file is missing")
        if path.stat().st_size > _MAX_BOOKMARKS_BYTES:
            raise _file_error("Edge Bookmarks file exceeds the size limit")
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _file_error("Edge Bookmarks file could not be read", exc) from None
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _file_error("Edge Bookmarks file is not valid JSON", exc) from None


def _bookmarks_read_retry_reason(exc: BaseException) -> str | None:
    return classify_file_error(exc) or classify_payload_error(exc)


def _bookmark_node(node: object, *, folders: tuple[str, ...], output: list[dict[str, str]]) -> None:
    if not isinstance(node, Mapping):
        raise ValueError("bookmark node must be an object")
    node_type = node.get("type")
    if node_type == "folder":
        children = node.get("children")
        if not isinstance(children, list):
            raise ValueError("bookmark folder must contain children")
        name = node.get("name", "")
        if not isinstance(name, str):
            raise ValueError("bookmark folder name must be a string")
        child_folders = folders + ((name.strip(),) if name.strip() else ())
        for child in children:
            _bookmark_node(child, folders=child_folders, output=output)
        return
    if node_type != "url":
        raise ValueError("bookmark node type is invalid")
    bookmark_id = _bounded_text(node.get("id"), name="ID", limit=_MAX_BOOKMARK_ID_CHARS)
    raw_url = _bounded_text(node.get("url"), name="URL", limit=_MAX_URL_CHARS)
    redacted_url = _redact_userinfo_url(raw_url)
    url = redacted_url or _normalize_bookmark_url(raw_url) or raw_url
    title = _bounded_text(node.get("name"), name="title", limit=_MAX_TITLE_CHARS, fallback=url)
    folder_path = "/".join(part for part in folders if part)
    if len(folder_path) > _MAX_FOLDER_CHARS:
        raise ValueError("bookmark folder path exceeds the size limit")
    date_added = node.get("date_added", "")
    if isinstance(date_added, bool) or not isinstance(date_added, (str, int, float)):
        raise ValueError("bookmark date_added is invalid")
    date_added_text = str(date_added).strip()
    if len(date_added_text) > _MAX_DATE_ADDED_CHARS:
        raise ValueError("bookmark date_added exceeds the size limit")
    output.append(
        {
            "bookmark_id": bookmark_id,
            "title": title or url,
            "url": url,
            "folder_path": folder_path,
            "date_added": date_added_text,
        }
    )


def _read_bookmarks(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, Mapping):
        raise _file_error("Edge Bookmarks JSON root is invalid")
    roots = payload.get("roots")
    if not isinstance(roots, Mapping) or not roots:
        raise _file_error("Edge Bookmarks JSON has no valid roots")
    result: list[dict[str, str]] = []
    for root_key, root in roots.items():
        if not isinstance(root_key, str):
            raise _file_error("Edge Bookmarks root name is invalid")
        if not isinstance(root, Mapping):
            raise _file_error("Edge Bookmarks root is invalid")
        root_name = root.get("name", _ROOT_NAMES.get(root_key, root_key))
        if not isinstance(root_name, str):
            raise _file_error("Edge Bookmarks root name is invalid")
        try:
            root_node = dict(root)
            root_node.setdefault("name", root_name)
            _bookmark_node(root_node, folders=(), output=result)
        except ValueError as exc:
            raise _file_error("Edge Bookmarks tree structure is invalid", exc) from None
    return result


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise ValueError("browser bookmarks cursor contains unsupported fields")


def _page_value(page: object, name: str, default: object = None) -> object:
    if isinstance(page, Mapping):
        return page.get(name, default)
    return getattr(page, name, default)


def _page_markdown(bookmark: Mapping[str, str], page: object) -> str:
    lines = [
        f"# {bookmark['title']}",
        "",
        "- Browser: Microsoft Edge",
        f"- Folder: {bookmark['folder_path'] or '(root)'}",
        f"- URL: {bookmark['url']}",
        f"- Added At: {bookmark['date_added'] or '(missing)'}",
    ]
    status = str(_page_value(page, "status", "disabled"))
    if status in {"ok", "warning", "skipped"}:
        lines.extend(["", "## Web Page", "", f"- Fetch Status: {status}"])
        final_url = _page_value(page, "final_url")
        title = _page_value(page, "title")
        description = _page_value(page, "description")
        error = _page_value(page, "error")
        if final_url:
            lines.append(f"- Final URL: {str(final_url)[:_MAX_URL_CHARS]}")
        if title:
            lines.append(f"- Page Title: {str(title)[:_MAX_TITLE_CHARS]}")
        if description:
            lines.append(f"- Page Description: {str(description)[:2_000]}")
        if error:
            lines.append(f"- Page fetch warning: {str(error)[:512]}")
        text = _page_value(page, "text")
        if isinstance(text, str) and text.strip():
            lines.extend(["", "## Extracted Text", "", text[:_MAX_PAGE_CHARS]])
    return "\n".join(lines) + "\n"


def _bookmark_item(bookmark: Mapping[str, str], *, profile: str, page: object) -> RawChangeItem:
    logical_id = _logical_id(bookmark["url"])
    fingerprint = _canonical_fingerprint(
        bookmark["title"], bookmark["url"], bookmark["folder_path"], bookmark["date_added"]
    )
    metadata: dict[str, object] = {
        "bookmark_id": bookmark["bookmark_id"],
        "profile": profile,
        "title": bookmark["title"],
        "url": bookmark["url"],
        "folder_path": bookmark["folder_path"],
        "date_added": bookmark["date_added"],
        "page_fetch_status": str(_page_value(page, "status", "disabled")),
    }
    for name, key in (
        ("final_url", "page_final_url"),
        ("title", "page_title"),
        ("description", "page_description"),
        ("error", "page_fetch_error"),
    ):
        value = _page_value(page, name)
        if isinstance(value, str) and value:
            metadata[key] = value[:512]
    return RawChangeItem(
        logical_id=logical_id,
        revision_id=fingerprint,
        operation="upsert",
        title=bookmark["title"],
        content=_page_markdown(bookmark, page),
        original_ref=bookmark["url"],
        metadata=metadata,
    )


async def _read_response_body(response: Any) -> bytes:
    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if isinstance(headers, Mapping) else None
    if content_length is not None:
        try:
            if int(content_length) > _MAX_PAGE_RESPONSE_BYTES:
                raise RuntimeError("page response exceeds the size limit")
        except ValueError as exc:
            raise RuntimeError("page response has an invalid size") from exc
    stream = getattr(response, "content", None)
    if stream is not None and hasattr(stream, "iter_chunked"):
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream.iter_chunked(_PAGE_CHUNK_SIZE):
            if not isinstance(chunk, bytes):
                raise RuntimeError("page response body is invalid")
            total += len(chunk)
            if total > _MAX_PAGE_RESPONSE_BYTES:
                raise RuntimeError("page response exceeds the size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    text = await response.text()
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_PAGE_RESPONSE_BYTES:
        raise RuntimeError("page response exceeds the size limit")
    return encoded


def _extract_page(html_text: str, *, final_url: str) -> dict[str, object]:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    description = ""
    for attrs in (
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ):
        node = soup.find("meta", attrs=attrs)
        if node is not None:
            content = node.get("content")
            description = " ".join(str(content).split()) if content else ""
            if description:
                break
    text_root = soup.find("article") or soup.find("main") or soup.body or soup
    cleaned_lines: list[str] = []
    for line in text_root.get_text("\n", strip=True).splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            cleaned_lines.append(cleaned)
    text = "\n".join(cleaned_lines)
    return {
        "status": "ok",
        "final_url": final_url,
        "title": title[:512] or None,
        "description": description[:2_000] or None,
        "text": text[:_MAX_PAGE_CHARS] or None,
        "error": None,
    }


async def _fetch_page_content(url: str) -> dict[str, object]:
    return await retry_provider_read(
        lambda: _fetch_page_content_once(url),
        provider="browser_bookmarks",
        operation_name="page_http_read",
        classify=classify_transport_error,
    )


async def _fetch_page_content_once(url: str) -> dict[str, object]:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return {
            "status": "skipped",
            "final_url": None,
            "title": None,
            "description": None,
            "text": None,
            "error": "unsupported URL scheme",
        }
    if parsed.username or parsed.password:
        return {
            "status": "skipped",
            "final_url": None,
            "title": None,
            "description": None,
            "text": None,
            "error": "URL credentials are not allowed",
        }
    timeout = aiohttp.ClientTimeout(total=_PAGE_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(
        timeout=timeout,
        cookie_jar=aiohttp.DummyCookieJar(),
        headers={
            "User-Agent": _PAGE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    ) as session:
        current_url = _normalize_bookmark_url(url) or url
        visited_urls = {current_url}
        for redirect_count in range(_MAX_PAGE_REDIRECTS + 1):
            async with session.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if redirect_count >= _MAX_PAGE_REDIRECTS:
                        raise RuntimeError("page has too many redirects")
                    location = getattr(response, "headers", {}).get("Location")
                    if not isinstance(location, str) or not location.strip():
                        raise RuntimeError("page redirect is missing a target")
                    redirect_url = urljoin(current_url, location.strip())
                    if not _is_fetchable_url(redirect_url):
                        raise RuntimeError("page redirect target is not allowed")
                    normalized_redirect = _normalize_bookmark_url(redirect_url)
                    if normalized_redirect is None:
                        raise RuntimeError("page redirect target is not allowed")
                    if normalized_redirect in visited_urls:
                        raise RuntimeError("page redirect loop detected")
                    visited_urls.add(normalized_redirect)
                    current_url = normalized_redirect
                    continue
                if response.status < 200 or response.status >= 300:
                    response.raise_for_status()
                    raise RuntimeError("page returned an unsuccessful HTTP status")
                content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).casefold()
                if content_type and not (
                    content_type.startswith("text/") or "html" in content_type or "xml" in content_type
                ):
                    raise RuntimeError("page content type is not text or HTML")
                body = await _read_response_body(response)
                final_url = str(getattr(response, "url", current_url))
                return _extract_page(body.decode("utf-8", errors="replace"), final_url=final_url)
        raise RuntimeError("page has too many redirects")


def _is_fetchable_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme.casefold() in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and _normalize_bookmark_url(url) is not None
        )
    except ValueError:
        return False


class BrowserBookmarksFetchService(ContextFetchService):
    """Read Windows Edge bookmarks and optionally bounded public page text."""

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id
        try:
            _validate_selection_cursor(cursor)
            bookmarks_path, profile, filters, include_subfolders, fetch_page_content = _source_values(self._config)
            payload = await retry_provider_read(
                lambda: asyncio.to_thread(_read_bookmarks_file, bookmarks_path),
                provider="browser_bookmarks",
                operation_name="bookmarks_file_read",
                classify=_bookmarks_read_retry_reason,
            )
            bookmarks = _read_bookmarks(payload)
            current: dict[str, dict[str, object]] = {}
            for bookmark in bookmarks:
                if not _folder_matches(
                    bookmark["folder_path"],
                    filters,
                    include_subfolders=include_subfolders,
                ):
                    continue
                logical_id = _logical_id(bookmark["url"])
                fingerprint = _canonical_fingerprint(
                    bookmark["title"],
                    bookmark["url"],
                    bookmark["folder_path"],
                    bookmark["date_added"],
                )
                candidate_time = _bookmark_candidate_time(bookmark["date_added"])
                candidate = {
                    "stable_id": logical_id,
                    "revision_id": fingerprint,
                    "candidate_time": candidate_time,
                    "resource_lane": "bookmark",
                    "locator": bookmark["url"],
                    "bookmark": bookmark,
                    "profile": profile,
                    "fetch_page_content": fetch_page_content,
                }
                if candidate_in_time_range(candidate_time, self._config.time_range, run_started_at):
                    current[logical_id] = candidate
            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            return select_latest_candidates(tuple(current.values()), cursor, max_items)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("Edge Bookmarks preparation failed", exc) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        next_cursor = dict(cursor) if cursor is not None else {}
        try:
            if not candidates:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                return
            for batch_index in range(0, len(candidates), _BATCH_SIZE):
                end = batch_index + _BATCH_SIZE
                chunk = candidates[batch_index:end]
                items: list[RawChangeItem] = []
                for candidate in chunk:
                    raw_bookmark = candidate.get("bookmark")
                    if not isinstance(raw_bookmark, Mapping):
                        raise _fetch_error("bookmark candidate has no source item")
                    bookmark = {str(key): str(value) for key, value in raw_bookmark.items()}
                    profile = candidate.get("profile")
                    if not isinstance(profile, str):
                        raise _fetch_error("bookmark candidate profile is invalid")
                    fetch_page_content = candidate.get("fetch_page_content")
                    if not isinstance(fetch_page_content, bool):
                        raise _fetch_error("bookmark candidate page setting is invalid")
                    page: object = {
                        "status": "disabled",
                        "final_url": None,
                        "title": None,
                        "description": None,
                        "text": None,
                        "error": None,
                    }
                    if fetch_page_content and _is_fetchable_url(bookmark["url"]):
                        try:
                            page = await _fetch_page_content(bookmark["url"])
                        except Exception as exc:
                            page = {"status": "warning", "error": _safe_error(exc)}
                    elif fetch_page_content:
                        page = {
                            "status": "skipped",
                            "final_url": None,
                            "title": None,
                            "description": None,
                            "text": None,
                            "error": "unsupported URL scheme",
                        }
                    items.append(_bookmark_item(bookmark, profile=profile, page=page))
                yield FetchBatch(
                    batch_id=f"batch-{batch_index // _BATCH_SIZE}",
                    items=tuple(items),
                    next_cursor=next_cursor,
                )
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("Edge Bookmarks fetch failed", exc) from None
