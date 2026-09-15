"""Public Zhihu column article provider for the embedded PersonalContext core."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp

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
_MAX_PAGES = 100
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_RAW_BYTES = 2 * 1024 * 1024
_MAX_CONTENT_CHARS = 2_000_000
_MAX_TEXT_FIELD_CHARS = 4_096
_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "openjiuwen-personal-context/1.0"


class ZhihuReaderFetchService(ContextFetchService):
    """Read public articles from one Zhihu column without login or cookies."""

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
            source_url = _source_url(self._config)
            column_id = _column_id(source_url)
            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS

            def selected(articles: list[dict[str, Any]]) -> tuple[dict[str, object], ...]:
                candidates = tuple(
                    _candidate(
                        article,
                        column_id=column_id,
                        source_url=source_url,
                        time_range=self._config.time_range,
                        run_started_at=run_started_at,
                    )
                    for article in articles
                )
                filtered = tuple(candidate for candidate in candidates if candidate is not None)
                return select_latest_candidates(filtered, cursor, max_items)

            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                articles = await _fetch_article_list(
                    session,
                    column_id,
                    stop_when=lambda records: len(selected(records)) >= max_items,
                )
            filtered = selected(articles)
            return filtered
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("Zhihu article preparation failed", exc) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        try:
            source_url = _source_url(self._config)
            column_id = _column_id(source_url)
            next_cursor = dict(cursor) if cursor is not None else {}

            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                if not candidates:
                    yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                    return
                for index in range(0, len(candidates), _BATCH_SIZE):
                    items: list[RawChangeItem] = []
                    end = index + _BATCH_SIZE
                    for candidate in candidates[index:end]:
                        raw_article = candidate.get("article")
                        if not isinstance(raw_article, Mapping):
                            raise _fetch_error("Zhihu candidate has no article metadata")
                        article = dict(raw_article)
                        article_id = _article_id(article)
                        if not article_id or article_id != candidate.get("stable_id"):
                            raise _fetch_error("Zhihu candidate has no stable ID")
                        article_body, raw_snapshot, updated_value, content_truncated = await _fetch_article_body(
                            session, article
                        )
                        items.append(
                            _change_item(
                                article,
                                article_id=article_id,
                                column_id=column_id,
                                source_url=source_url,
                                body=article_body,
                                raw_snapshot=raw_snapshot,
                                updated_value=updated_value,
                                content_truncated=content_truncated,
                            )
                        )
                    yield FetchBatch(
                        batch_id=f"batch-{index // _BATCH_SIZE}",
                        items=tuple(items),
                        next_cursor=next_cursor,
                    )
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("Zhihu public article fetch failed", exc) from None


async def _fetch_article_list(
    session: Any,
    column_id: str,
    *,
    stop_when: Callable[[list[dict[str, Any]]], bool],
) -> list[dict[str, Any]]:
    url = f"https://www.zhihu.com/api/v4/columns/{column_id}/articles"
    articles: list[dict[str, Any]] = []
    article_ids: set[str] = set()
    offset = 0
    for _ in range(_MAX_PAGES):
        payload = await _request_json(
            session,
            url,
            params={"limit": _BATCH_SIZE, "offset": offset},
        )
        page, is_end = _list_page(payload)
        if not page:
            return articles
        for article in page:
            article_id = _article_id(article)
            if article_id and article_id in article_ids:
                continue
            if article_id:
                article_ids.add(article_id)
            articles.append(article)
        if stop_when(articles):
            return articles
        if is_end:
            return articles
        offset += len(page)
    raise _fetch_error("Zhihu article list pagination exceeded the limit")


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise ValueError("Zhihu cursor contains unsupported fields")


def _candidate(
    article: Mapping[str, object],
    *,
    column_id: str,
    source_url: str,
    time_range: Mapping[str, object],
    run_started_at: datetime,
) -> dict[str, object] | None:
    article_id = _article_id(article)
    if not article_id:
        raise _fetch_error("Zhihu article has no stable ID")
    timestamp = _effective_timestamp(article)
    if timestamp <= 0:
        if time_range.get("mode") != "all":
            raise _fetch_error("Zhihu article has no usable published or updated time")
        candidate_time = "1970-01-01T00:00:00Z"
    else:
        candidate_time = datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
    if not candidate_in_time_range(candidate_time, time_range, run_started_at):
        return None
    updated_value = _updated_value(article)
    updated_timestamp = _timestamp_number(updated_value)
    revision_id = (
        str(updated_value)
        if updated_value is not None and updated_timestamp > 0
        else hashlib.sha256(
            json.dumps(dict(article), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    )
    return {
        "stable_id": article_id,
        "revision_id": revision_id,
        "candidate_time": candidate_time,
        "resource_lane": "article",
        "locator": _article_url(article, article_id),
        "article": dict(article),
        "column_id": column_id,
        "source_url": source_url,
    }


async def _fetch_article_body(
    session: Any,
    article: Mapping[str, object],
) -> tuple[str, bytes | None, object | None, bool]:
    article_id = _article_id(article)
    inline = _content_value(article)
    if inline:
        raw = json.dumps(dict(article), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        content, truncated = _bounded_content(inline)
        return content, raw if len(raw) <= _MAX_RAW_BYTES else None, _updated_value(article), truncated

    url = f"https://www.zhihu.com/api/v4/articles/{article_id}"
    body = await _request_body(session, url)
    payload = _json_payload(body)
    record = _unwrap_record(payload)
    content = _content_value(record)
    if not content:
        raise _fetch_error("Zhihu article body is unavailable")
    bounded, truncated = _bounded_content(content)
    return (
        bounded,
        body if len(body) <= _MAX_RAW_BYTES else None,
        _updated_value(record) or _updated_value(article),
        truncated,
    )


def _source_url(config: PersonalContextFetchServiceConfig) -> str:
    value = config.source.get("column_url")
    if not isinstance(value, str) or not value:
        raise _fetch_error("Zhihu column URL is invalid")
    _column_id(value)
    return value


def _column_id(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme.casefold() != "https" or not _is_allowed_host(host, "zhihu.com"):
        raise _fetch_error("Zhihu column URL is invalid")
    if len(parts) != 2 or parts[0] != "column":
        raise _fetch_error("Zhihu column URL is invalid")
    column_id = parts[1]
    if len(column_id) > 128 or not re.fullmatch(r"[A-Za-z0-9._-]+", column_id):
        raise _fetch_error("Zhihu column URL is invalid")
    return column_id


def _list_page(payload: object) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)], True
    if not isinstance(payload, Mapping):
        raise _fetch_error("Zhihu article list response is invalid")
    raw_data = payload.get("data")
    if isinstance(raw_data, list):
        page = [dict(item) for item in raw_data if isinstance(item, Mapping)]
    elif isinstance(raw_data, Mapping) and isinstance(raw_data.get("items"), list):
        page = [dict(item) for item in raw_data["items"] if isinstance(item, Mapping)]
    else:
        raise _fetch_error("Zhihu article list response is invalid")
    paging = payload.get("paging")
    if isinstance(paging, Mapping):
        return page, bool(paging.get("is_end", True))
    return page, True


def _unwrap_record(payload: object) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, Mapping):
            return dict(data)
        return dict(payload)
    raise _fetch_error("Zhihu article body response is invalid")


def _content_value(record: Mapping[str, object]) -> str:
    for key in ("content", "body", "html", "text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return _html_to_text(value)
    return ""


def _bounded_content(value: str) -> tuple[str, bool]:
    text = _html_to_text(value)
    if not text:
        raise _fetch_error("Zhihu article body is empty")
    return text[:_MAX_CONTENT_CHARS], len(text) > _MAX_CONTENT_CHARS


def _html_to_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<(?:br\s*/?|/p|/div|/h[1-6]|/li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _change_item(
    article: Mapping[str, object],
    *,
    article_id: str,
    column_id: str,
    source_url: str,
    body: str,
    raw_snapshot: bytes | None,
    updated_value: object | None,
    content_truncated: bool,
) -> RawChangeItem:
    title = _bounded_text(article.get("title"), fallback=article_id)
    article_url = _article_url(article, article_id)
    updated_timestamp = _timestamp_number(updated_value)
    revision = _revision_id(body, updated_value, updated_timestamp)
    metadata: dict[str, object] = {
        "platform": "zhihu",
        "column_id": column_id,
        "article_id": article_id,
        "column_url": source_url,
        "published_at": _timestamp_text(article.get("published")),
        "updated_at": _timestamp_text(updated_value),
        "content_truncated": content_truncated,
        "raw_snapshot_omitted": raw_snapshot is None,
    }
    return RawChangeItem(
        logical_id=f"zhihu_reader:article:{article_id}",
        revision_id=revision,
        operation="upsert",
        title=title,
        content=body,
        original_ref=article_url,
        metadata=metadata,
        raw_snapshot=raw_snapshot,
    )


def _article_url(article: Mapping[str, object], article_id: str) -> str:
    value = article.get("url") or article.get("article_url")
    if isinstance(value, str) and value.startswith("https://"):
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        if not parsed.username and not parsed.password and _is_allowed_host(host, "zhihu.com"):
            return value.split("?", 1)[0].split("#", 1)[0]
    return f"https://zhuanlan.zhihu.com/p/{article_id}"


def _article_id(article: Mapping[str, object]) -> str:
    for key in ("id", "article_id", "articleId"):
        value = article.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            text = str(value).strip()
            if len(text) <= 256 and re.fullmatch(r"[A-Za-z0-9._-]+", text):
                return text
    return ""


def _updated_value(article: Mapping[str, object]) -> object | None:
    for key in ("updated", "updated_at", "updated_time", "update_time"):
        if article.get(key) is not None:
            return article[key]
    return None


def _article_timestamp(article: Mapping[str, object], kind: str) -> float:
    keys = (
        ("published", "published_at", "created", "created_at", "created_time")
        if kind == "published"
        else ("updated", "updated_at", "updated_time", "update_time")
    )
    for key in keys:
        value = _timestamp_number(article.get(key))
        if value > 0:
            return value
    return 0.0


def _effective_timestamp(article: Mapping[str, object]) -> float:
    return max(_article_timestamp(article, "published"), _article_timestamp(article, "updated"))


def _timestamp_number(value: object) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            result = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return 0.0
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            result = parsed.timestamp()
    else:
        return 0.0
    if not math.isfinite(result):
        return 0.0
    for _ in range(6):
        if abs(result) < 100_000_000_000:
            break
        result /= 1000
    return result if math.isfinite(result) and 0 <= result < 100_000_000_000 else 0.0


def _is_allowed_host(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _timestamp_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _revision_id(body: str, updated_value: object | None, updated_timestamp: float) -> str:
    if updated_value is not None and updated_timestamp > 0:
        return str(updated_value)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _bounded_text(value: object, *, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:_MAX_TEXT_FIELD_CHARS]


async def _request_body(session: Any, url: str, *, params: dict[str, object] | None = None) -> bytes:
    try:
        return await retry_provider_read(
            lambda: _request_body_once(session, url, params=params),
            provider="zhihu_reader",
            operation_name="body_http_read",
            classify=_reader_retry_reason,
        )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _fetch_error("Zhihu request failed", exc) from None


async def _request_json(session: Any, url: str, *, params: dict[str, object] | None = None) -> object:
    async def read_and_parse_once() -> object:
        body = await _request_body_once(session, url, params=params)
        return _json_payload(body)

    try:
        return await retry_provider_read(
            read_and_parse_once,
            provider="zhihu_reader",
            operation_name="list_json_http_read",
            classify=_reader_retry_reason,
        )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _fetch_error("Zhihu request failed", exc) from None


def _reader_retry_reason(exc: BaseException) -> str | None:
    return classify_transport_error(exc) or classify_payload_error(exc)


async def _request_body_once(session: Any, url: str, *, params: dict[str, object] | None = None) -> bytes:
    try:
        async with session.get(url, params=params, allow_redirects=False) as response:
            status = int(getattr(response, "status", 0))
            if status < 200 or status >= 300:
                response.raise_for_status()
                raise RuntimeError("Zhihu request returned an unsuccessful HTTP status")
            body = await _read_response_body(response)
            if not body:
                raise EOFError("Zhihu response body is empty")
            return body
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _fetch_error("Zhihu request failed", exc) from None


async def _read_response_body(response: Any) -> bytes:
    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if hasattr(headers, "get") else None
    if content_length is not None:
        try:
            if int(content_length) > _MAX_RESPONSE_BYTES:
                raise _fetch_error("Zhihu response exceeds the size limit")
        except ValueError as exc:
            raise _fetch_error("Zhihu response size is invalid", exc) from None
    stream = getattr(response, "content", None)
    if stream is not None and hasattr(stream, "iter_chunked"):
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream.iter_chunked(64 * 1024):
            if not isinstance(chunk, bytes):
                raise _fetch_error("Zhihu response body is invalid")
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise _fetch_error("Zhihu response exceeds the size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    read = getattr(response, "read", None)
    if callable(read):
        body = await read()
        if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
            raise _fetch_error("Zhihu response body is invalid or too large")
        return body
    json_method = getattr(response, "json", None)
    if callable(json_method):
        payload = await json_method()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > _MAX_RESPONSE_BYTES:
            raise _fetch_error("Zhihu response exceeds the size limit")
        return body
    raise _fetch_error("Zhihu response body is unavailable")


def _json_payload(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fetch_error("Zhihu response is not valid JSON", exc) from None


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)
