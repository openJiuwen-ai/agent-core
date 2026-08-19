"""Public Zhihu column article provider for the embedded PersonalContext core."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
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

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        try:
            source_url = _source_url(self._config)
            column_id = _column_id(source_url)
            state = _read_cursor(cursor, source_url)
            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS

            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                articles = await _fetch_article_list(session, column_id, max_items=max_items)
                records = _sort_articles(articles)
                selected = _select_articles(records, state, max_items)
                changes: list[RawChangeItem] = []
                next_state = dict(state)
                cursor_states: list[dict[str, object]] = []

                for article, category in selected:
                    article_id = _article_id(article)
                    if not article_id:
                        raise _fetch_error("Zhihu article has no stable ID")
                    article_body, raw_snapshot, updated_value, content_truncated = await _fetch_article_body(
                        session,
                        article,
                    )
                    published_at = _article_timestamp(article, "published")
                    updated_at = _article_timestamp(article, "updated")
                    effective_timestamp = max(published_at, updated_at)
                    _advance_cursor_state(next_state, effective_timestamp, article_id, category)
                    cursor_states.append(deepcopy(next_state))
                    changes.append(
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
                _finalize_history_state(next_state, records, selected)
                if cursor_states:
                    cursor_states[-1] = deepcopy(next_state)

            for batch in _batches(
                changes,
                cursor_states,
                next_state,
            ):
                yield batch
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error("Zhihu public article fetch failed", exc) from None


async def _fetch_article_list(
    session: Any,
    column_id: str,
    *,
    max_items: int,
) -> list[dict[str, Any]]:
    url = f"https://www.zhihu.com/api/v4/columns/{column_id}/articles"
    articles: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        body = await _request_body(
            session,
            url,
            params={"limit": _BATCH_SIZE, "offset": offset},
        )
        payload = _json_payload(body)
        page, is_end = _list_page(payload)
        articles.extend(page)
        # The public endpoint may report an enormous column with
        # ``is_end=false`` for many pages.  The provider only needs enough
        # newest records for this run; continuing past that bound can turn a
        # valid fetch into a false pagination failure.
        if is_end or not page:
            return articles
        offset += len(page)
    raise _fetch_error("Zhihu article list pagination exceeded the limit")


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


def _read_cursor(cursor: dict[str, object] | None, source_url: str) -> dict[str, object]:
    if cursor is None or cursor == {}:
        return {
            "source_url": source_url,
            "latest_timestamp": 0.0,
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        }
    if not isinstance(cursor, Mapping) or set(cursor) != {
        "source_url",
        "latest_timestamp",
        "latest_timestamp_ids",
        "history_before_timestamp",
        "history_boundary_ids",
        "history_complete",
    }:
        raise _fetch_error("Zhihu cursor is invalid")
    if cursor.get("source_url") != source_url:
        raise _fetch_error("Zhihu cursor source is invalid")
    raw_latest = cursor.get("latest_timestamp")
    if isinstance(raw_latest, bool) or not isinstance(raw_latest, (int, float)):
        raise _fetch_error("Zhihu cursor is invalid")
    latest = float(raw_latest)
    if not math.isfinite(latest) or latest < 0:
        raise _fetch_error("Zhihu cursor is invalid")
    latest_ids = _cursor_ids(cursor.get("latest_timestamp_ids"))
    raw_history = cursor.get("history_before_timestamp")
    if raw_history is not None and (isinstance(raw_history, bool) or not isinstance(raw_history, (int, float))):
        raise _fetch_error("Zhihu cursor is invalid")
    history = None if raw_history is None else float(raw_history)
    if history is not None and (not math.isfinite(history) or history < 0):
        raise _fetch_error("Zhihu cursor is invalid")
    boundary_ids = _cursor_ids(cursor.get("history_boundary_ids"))
    history_complete = cursor.get("history_complete")
    if not isinstance(history_complete, bool):
        raise _fetch_error("Zhihu cursor is invalid")
    return {
        "source_url": source_url,
        "latest_timestamp": latest,
        "latest_timestamp_ids": sorted(set(latest_ids)),
        "history_before_timestamp": history,
        "history_boundary_ids": sorted(set(boundary_ids)),
        "history_complete": history_complete,
    }


def _cursor_ids(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 10_000
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise _fetch_error("Zhihu cursor is invalid")
    return sorted(set(value))


def _advance_cursor_state(state: dict[str, object], timestamp: float, article_id: str, category: str) -> None:
    if category == "latest":
        latest = float(state["latest_timestamp"])
        latest_ids = [str(item) for item in state["latest_timestamp_ids"]]
        if timestamp > latest:
            state["latest_timestamp"] = timestamp
            state["latest_timestamp_ids"] = [article_id]
        elif timestamp == latest and article_id not in latest_ids:
            state["latest_timestamp_ids"] = sorted([*latest_ids, article_id])
        return
    history = state["history_before_timestamp"]
    boundary_ids = [str(item) for item in state["history_boundary_ids"]]
    if history is None or timestamp < float(history):
        state["history_before_timestamp"] = timestamp
        state["history_boundary_ids"] = [article_id]
    elif timestamp == float(history) and article_id not in boundary_ids:
        state["history_boundary_ids"] = sorted([*boundary_ids, article_id])


def _finalize_history_state(
    state: dict[str, object],
    records: list[dict[str, Any]],
    selected: list[tuple[dict[str, Any], str]],
) -> None:
    if state["history_before_timestamp"] is None and selected:
        oldest_timestamp = min(_effective_timestamp(article) for article, _ in selected)
        oldest_ids = [
            _article_id(article) for article, _ in selected if _effective_timestamp(article) == oldest_timestamp
        ]
        state["history_before_timestamp"] = oldest_timestamp
        state["history_boundary_ids"] = sorted(set(oldest_ids))
    boundary = state["history_before_timestamp"]
    if boundary is None:
        state["history_complete"] = not records
        return
    boundary_value = float(boundary)
    boundary_ids = {str(item) for item in state["history_boundary_ids"]}
    selected_ids = {_article_id(article) for article, _ in selected}
    state["history_complete"] = not any(
        _article_id(article) not in selected_ids
        and (
            _effective_timestamp(article) < boundary_value
            or (_effective_timestamp(article) == boundary_value and _article_id(article) not in boundary_ids)
        )
        for article in records
    )


def _select_articles(
    records: list[dict[str, Any]],
    state: Mapping[str, object],
    max_items: int,
) -> list[tuple[dict[str, Any], str]]:
    latest_timestamp = float(state["latest_timestamp"])
    latest_ids = {str(item) for item in state["latest_timestamp_ids"]}
    history_before = state["history_before_timestamp"]
    history_timestamp = None if history_before is None else float(history_before)
    history_ids = {str(item) for item in state["history_boundary_ids"]}
    latest_candidates: list[dict[str, Any]] = []
    history_candidates: list[dict[str, Any]] = []
    for record in records:
        article_id = _article_id(record)
        timestamp = _effective_timestamp(record)
        if timestamp > latest_timestamp or (timestamp == latest_timestamp and article_id not in latest_ids):
            latest_candidates.append(record)
            continue
        if history_timestamp is None:
            history_candidates.append(record)
        elif timestamp < history_timestamp or (timestamp == history_timestamp and article_id not in history_ids):
            history_candidates.append(record)
    selected: list[tuple[dict[str, Any], str]] = []
    for record in latest_candidates:
        if len(selected) >= max_items:
            break
        selected.append((record, "latest"))
    if len(selected) < max_items:
        for record in history_candidates:
            if len(selected) >= max_items:
                break
            selected.append((record, "history"))
    return selected


def _sort_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(articles, key=lambda article: (-_effective_timestamp(article), _article_id(article)))


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
        async with session.get(url, params=params, allow_redirects=False) as response:
            status = int(getattr(response, "status", 0))
            if status < 200 or status >= 300:
                raise _fetch_error("Zhihu request failed")
            return await _read_response_body(response)
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


def _batches(
    items: list[RawChangeItem],
    cursor_states: list[dict[str, object]],
    cursor: dict[str, object],
) -> list[FetchBatch]:
    if not items:
        return [FetchBatch(batch_id="batch-0", items=(), next_cursor=cursor)]
    batches: list[FetchBatch] = []
    for index in range(0, len(items), _BATCH_SIZE):
        end = min(index + _BATCH_SIZE, len(items))
        batches.append(
            FetchBatch(
                batch_id=f"batch-{index // _BATCH_SIZE}",
                items=tuple(items[index:end]),
                next_cursor=cursor_states[end - 1] if end - 1 < len(cursor_states) else cursor,
            )
        )
    return batches


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)
