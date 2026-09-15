"""RSS and Atom feed provider for the embedded PersonalContext core."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

import aiohttp
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    candidate_in_time_range,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.retry import (
    classify_payload_error,
    classify_transport_error,
    retry_provider_read,
)
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 20
_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_RAW_BYTES = 2 * 1024 * 1024
_MAX_CONTENT_CHARS = 2_000_000
_MAX_TEXT_FIELD_CHARS = 4_096
_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "openjiuwen-personal-context/1.0"
_UNSAFE_XML_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class RssFeedFetchService(ContextFetchService):
    """Read one HTTPS RSS/Atom feed without login or persistent cookies."""

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
            feed_url = _source_url(self._config)
            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                    "User-Agent": _USER_AGENT,
                },
            ) as session:
                body = await _request_feed(session, feed_url)
            feed_type, feed_title, candidates = _parse_feed(
                body,
                feed_url=feed_url,
                time_range=self._config.time_range,
                run_started_at=run_started_at,
            )
            del feed_type, feed_title
            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            return select_latest_candidates(tuple(candidates), cursor, max_items)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("RSS feed preparation failed", exc) from None

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
            for index in range(0, len(candidates), _BATCH_SIZE):
                items = tuple(_change_item(candidate) for candidate in candidates[slice(index, index + _BATCH_SIZE)])
                yield FetchBatch(
                    batch_id=f"batch-{index // _BATCH_SIZE}",
                    items=items,
                    next_cursor=next_cursor,
                )
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("RSS feed fetch failed", exc) from None


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)


def _source_url(config: PersonalContextFetchServiceConfig) -> str:
    value = config.source.get("feed_url")
    if not isinstance(value, str) or not value.strip():
        raise _fetch_error("RSS feed URL is invalid")
    return _normalize_url(value, name="feed_url")


def _normalize_url(value: str, *, name: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() != "https" or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{name} must be an https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{name} must not contain userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must not contain a custom port") from exc
    if port is not None:
        raise ValueError(f"{name} must not contain a custom port")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise ValueError("RSS feed cursor contains unsupported fields")


async def _request_feed(session: Any, url: str) -> bytes:
    try:
        return await retry_provider_read(
            lambda: _request_feed_once(session, url),
            provider="rss_feed",
            operation_name="feed_http_read",
            classify=lambda exc: classify_transport_error(exc) or classify_payload_error(exc),
        )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _fetch_error("RSS feed request failed", exc) from None


async def _request_feed_once(session: Any, url: str) -> bytes:
    current_url = url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        try:
            async with session.get(current_url, allow_redirects=False) as response:
                status = int(getattr(response, "status", 0))
                if status in {301, 302, 303, 307, 308}:
                    if redirect_count >= _MAX_REDIRECTS:
                        raise _fetch_error("RSS feed redirected too many times")
                    location = getattr(response, "headers", {}).get("Location")
                    if not isinstance(location, str) or not location.strip():
                        raise _fetch_error("RSS feed redirect is invalid")
                    current_url = _normalize_url(urljoin(current_url, location), name="redirect target")
                    continue
                if status < 200 or status >= 300:
                    response.raise_for_status()
                    raise RuntimeError("RSS feed returned an unsuccessful HTTP status")
                body = await _read_response_body(response)
                if not body:
                    raise EOFError("RSS feed response body is empty")
                return body
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("RSS feed request failed", exc) from None
    raise _fetch_error("RSS feed redirected too many times")


async def _read_response_body(response: Any) -> bytes:
    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if hasattr(headers, "get") else None
    if content_length is not None:
        try:
            if int(content_length) > _MAX_RESPONSE_BYTES:
                raise _fetch_error("RSS feed response exceeds the size limit")
        except ValueError as exc:
            raise _fetch_error("RSS feed response size is invalid", exc) from None
    stream = getattr(response, "content", None)
    if stream is not None and hasattr(stream, "iter_chunked"):
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream.iter_chunked(64 * 1024):
            if not isinstance(chunk, bytes):
                raise _fetch_error("RSS feed response body is invalid")
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise _fetch_error("RSS feed response exceeds the size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    read = getattr(response, "read", None)
    if callable(read):
        body = await read()
        if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
            raise _fetch_error("RSS feed response body is invalid or too large")
        return body
    text = getattr(response, "text", None)
    if callable(text):
        value = await text()
        if not isinstance(value, str):
            raise _fetch_error("RSS feed response body is invalid")
        body = value.encode("utf-8")
        if len(body) > _MAX_RESPONSE_BYTES:
            raise _fetch_error("RSS feed response exceeds the size limit")
        return body
    raise _fetch_error("RSS feed response body is unavailable")


def _parse_feed(
    body: bytes,
    *,
    feed_url: str,
    time_range: Mapping[str, object],
    run_started_at: datetime,
) -> tuple[str, str, list[dict[str, object]]]:
    if _UNSAFE_XML_DECLARATION.search(body):
        raise ValueError("RSS feed XML declarations are not allowed")
    try:
        root = ElementTree.fromstring(body)
    except Exception as exc:
        raise ValueError("RSS feed XML is invalid") from exc

    root_name = _local_name(root.tag)
    if root_name == "feed":
        feed_type = "atom"
        container = root
        entries_parent = root
        entry_name = "entry"
    elif root_name in {"rss", "rdf"}:
        feed_type = "rss"
        container = _first_child(root, "channel") or root
        entries_parent = root if root_name == "rdf" else container
        entry_name = "item"
    else:
        raise ValueError("RSS feed root element is unsupported")

    feed_title = _child_text(container, "title")
    entries = [child for child in list(entries_parent) if _local_name(child.tag) == entry_name]
    if not entries:
        raise ValueError("RSS feed contains no entries")

    candidates: dict[str, dict[str, object]] = {}
    for entry in entries:
        candidate = _entry_candidate(
            entry,
            feed_type=feed_type,
            feed_title=feed_title,
            feed_url=feed_url,
            time_range=time_range,
            run_started_at=run_started_at,
        )
        if candidate is None:
            continue
        stable_id = str(candidate["stable_id"])
        previous = candidates.get(stable_id)
        if previous is None or _time_value(str(candidate["candidate_time"])) >= _time_value(
            str(previous["candidate_time"])
        ):
            candidates[stable_id] = candidate
    return feed_type, feed_title, list(candidates.values())


def _entry_candidate(
    entry: Element,
    *,
    feed_type: str,
    feed_title: str,
    feed_url: str,
    time_range: Mapping[str, object],
    run_started_at: datetime,
) -> dict[str, object] | None:
    title = _bounded_text(_child_text(entry, "title"), fallback="未命名条目")
    link = _entry_link(entry, feed_url)
    raw_identifier = _child_text(entry, "guid", "id")
    author = _child_text(entry, "author", "creator")
    if not author:
        author_node = _first_child(entry, "author")
        author = _child_text(author_node, "name") if author_node is not None else ""
    content_source = _child_text(entry, "encoded", "content", "description", "summary")
    content, content_truncated = _html_to_text(content_source)
    if not content:
        content = _fallback_content(title, link, feed_url)
    content = content[:_MAX_CONTENT_CHARS]
    dates = _date_values(entry)
    candidate_datetime = max((parsed for _name, _value, parsed in dates), default=None)
    if candidate_datetime is None:
        if time_range.get("mode") != "all":
            raise ValueError("RSS feed entry has no usable published or updated time")
        candidate_time = "1970-01-01T00:00:00Z"
    else:
        candidate_time = candidate_datetime.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if not candidate_in_time_range(candidate_time, time_range, run_started_at):
        return None

    identifier = _stable_identifier(raw_identifier or link or content)
    revision_payload = {
        "title": title,
        "link": link,
        "author": author,
        "content": content,
        "dates": dates,
    }
    revision_id = hashlib.sha256(
        json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    raw_snapshot = ElementTree.tostring(entry, encoding="utf-8")
    if len(raw_snapshot) > _MAX_RAW_BYTES:
        raw_snapshot = None
    published_at = _date_text(dates, {"pubdate", "published", "date"})
    updated_at = _date_text(dates, {"updated", "modified"})
    return {
        "stable_id": identifier,
        "revision_id": revision_id,
        "candidate_time": candidate_time,
        "resource_lane": "entry",
        "locator": link,
        "title": title,
        "content": content,
        "raw_snapshot": raw_snapshot,
        "feed_url": feed_url,
        "feed_title": feed_title,
        "entry_id": raw_identifier or identifier,
        "author": author,
        "published_at": published_at,
        "updated_at": updated_at,
        "content_type": feed_type,
        "content_truncated": content_truncated or len(content) >= _MAX_CONTENT_CHARS,
    }


def _change_item(candidate: Mapping[str, object]) -> RawChangeItem:
    stable_id = _required_text(candidate, "stable_id")
    title = _bounded_text(candidate.get("title"), fallback=stable_id)
    content = _required_text(candidate, "content")
    locator = _required_text(candidate, "locator")
    feed_url = _required_text(candidate, "feed_url")
    metadata: dict[str, object] = {
        "platform": "rss",
        "feed_url": feed_url,
        "feed_title": _bounded_text(candidate.get("feed_title"), fallback=feed_url),
        "entry_id": _bounded_text(candidate.get("entry_id"), fallback=stable_id),
        "content_type": _bounded_text(candidate.get("content_type"), fallback="rss"),
        "author": _bounded_text(candidate.get("author"), fallback="") or None,
        "published_at": candidate.get("published_at"),
        "updated_at": candidate.get("updated_at"),
        "content_truncated": bool(candidate.get("content_truncated", False)),
        "raw_snapshot_omitted": candidate.get("raw_snapshot") is None,
    }
    return RawChangeItem(
        logical_id=f"rss_feed:entry:{stable_id}",
        revision_id=_required_text(candidate, "revision_id"),
        operation="upsert",
        title=title,
        content=content,
        original_ref=locator,
        metadata=metadata,
        raw_snapshot=candidate.get("raw_snapshot"),
    )


def _first_child(element: Element | None, name: str) -> Element | None:
    if element is None:
        return None
    return next((child for child in list(element) if _local_name(child.tag) == name), None)


def _child_text(element: Element | None, *names: str) -> str:
    if element is None:
        return ""
    for name in names:
        wanted = name.casefold()
        for child in list(element):
            if _local_name(child.tag) != wanted:
                continue
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _entry_link(entry: Element, feed_url: str) -> str:
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "")
        value = href or "".join(child.itertext()).strip()
        if not value:
            continue
        candidate = urljoin(feed_url, value)
        parsed = urlsplit(candidate)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            continue
        if parsed.username is not None or parsed.password is not None:
            continue
        return urlunsplit((parsed.scheme.casefold(), parsed.netloc, parsed.path or "/", parsed.query, ""))
    return feed_url


def _date_values(entry: Element) -> list[tuple[str, str, datetime]]:
    result: list[tuple[str, str, datetime]] = []
    for child in list(entry):
        name = _local_name(child.tag)
        if name not in {"pubdate", "published", "updated", "modified", "date"}:
            continue
        value = "".join(child.itertext()).strip()
        parsed = _parse_datetime(value)
        if parsed is not None:
            result.append((name, value, parsed))
    return result


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _date_text(values: list[tuple[str, str, datetime]], names: set[str]) -> str | None:
    for name, value, _parsed in values:
        if name in names:
            return value[:_MAX_TEXT_FIELD_CHARS]
    return None


def _html_to_text(value: str) -> tuple[str, bool]:
    if not value:
        return "", False
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()
    lines = [" ".join(line.split()) for line in soup.get_text("\n", strip=True).splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    return text[:_MAX_CONTENT_CHARS], len(text) > _MAX_CONTENT_CHARS


def _fallback_content(title: str, link: str, feed_url: str) -> str:
    return f"# {title}\n\n- Source: {link}\n- Feed: {feed_url}\n"


def _stable_identifier(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        return hashlib.sha256(b"empty-rss-entry").hexdigest()
    if len(text) <= 256:
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _required_text(candidate: Mapping[str, object], name: str) -> str:
    value = candidate.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"RSS candidate {name} is invalid")
    return value.strip()


def _bounded_text(value: object, *, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:_MAX_TEXT_FIELD_CHARS]


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1].casefold()


def _time_value(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.timestamp()
