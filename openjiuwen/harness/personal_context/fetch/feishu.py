"""Feishu ``lark-cli`` provider for the embedded personal-context core.

The provider uses the user-authorized ``lark-cli`` identity and never receives
or stores an access token.  Every run first enumerates metadata and selects a
bounded latest-first candidate list.  Content reads and downloads happen only
for that selected list, except for explicitly configured document IDs where the
document endpoint is also the only available discovery endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    candidate_in_time_range,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.retry import (
    classify_payload_error,
    classify_transport_error,
    retry_provider_read,
    retry_reason_from_http_status,
)
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 100
_MAX_PAGES = 100
_CLI_TIMEOUT_SECONDS = 30.0
_CLI_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_CONTENT_CHARS = 2_000_000
_MAX_RAW_BYTES = 2 * 1024 * 1024
_MAX_WIKI_PATH_PARTS = 100
_EPOCH = "1970-01-01T00:00:00Z"
_FEISHU_SCOPE_BY_RESOURCE = {
    "docs": "docs:document.content:read",
    "search": "search:docs:read",
    "tasks": "task:task:read",
    "calendar": "calendar:calendar.event:read",
    "wiki": "wiki:node:retrieve",
    "wiki_docs": "docs:document.content:read",
    "wiki_files": "drive:file:download",
}
_SUPPORTED_FEISHU_READ_SCOPES = tuple(sorted(set(_FEISHU_SCOPE_BY_RESOURCE.values())))


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)


def _safe_detail(exc: BaseException) -> str:
    detail = str(exc).replace("\r", " ").replace("\n", " ")
    detail = re.sub(r"https?://\S+", "<redacted-url>", detail)
    detail = re.sub(
        r"(?i)(access[_-]?token|refresh[_-]?token|authorization|cookie|secret|password|device[_-]?code)"
        r"\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        detail,
    )
    return detail[:512] or exc.__class__.__name__


def _payload_data(payload: Mapping[str, object]) -> object:
    data = payload.get("data")
    return data if data is not None else payload


def _content_from_payload(payload: Mapping[str, object]) -> tuple[Mapping[str, object], object]:
    data = _payload_data(payload)
    if isinstance(data, Mapping):
        document = data.get("document")
        if isinstance(document, Mapping):
            for key in ("content", "raw_content", "markdown"):
                if key in document:
                    return document, document[key]
            return document, document
        for key in ("content", "raw_content", "markdown"):
            if key in data:
                return data, data[key]
        return data, data
    return payload, data


def _as_object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fetch_error(f"Feishu {name} response is not an object")
    return dict(value)


def _as_items(payload: object, *, name: str) -> list[dict[str, object]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, Mapping):
        data = _payload_data(payload)
        if isinstance(data, list):
            values = data
        elif isinstance(data, Mapping):
            values = None
            for key in ("items", "docs", "documents", "tasks", "events", "nodes", "results"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    values = candidate
                    break
            if values is None:
                return []
        else:
            return []
    else:
        raise _fetch_error(f"Feishu {name} response has invalid items")
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _page_token(payload: Mapping[str, object]) -> str | None:
    data = _payload_data(payload)
    if not isinstance(data, Mapping):
        return None
    token = data.get("page_token") or data.get("next_page_token") or data.get("nextPageToken")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _parse_cli_json(stdout: str) -> object:
    text = stdout.strip()
    last_error: json.JSONDecodeError | None = None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        last_error = exc
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError as decode_error:
                last_error = decode_error
                continue
            return value
    raise _fetch_error("lark-cli did not return JSON", last_error)


def _safe_cli_output(value: str) -> str:
    compact = " ".join(value.split())
    compact = re.sub(r"https?://\S+", "<redacted-url>", compact)
    compact = re.sub(
        r"(?i)(access[_-]?token|refresh[_-]?token|authorization|cookie|secret|password|device[_-]?code)"
        r"\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        compact,
    )
    return compact[:512]


def _cli_error_message(stdout: str, stderr: str) -> str:
    try:
        value = _parse_cli_json(stdout or stderr)
    except BaseError:
        return _safe_cli_output(stdout or stderr or "lark-cli command failed")
    if isinstance(value, Mapping):
        error = value.get("error")
        if isinstance(error, Mapping):
            message = error.get("message") or error.get("hint")
            if isinstance(message, str) and message.strip():
                return _safe_cli_output(message)
        message = value.get("message") or value.get("msg")
        if isinstance(message, str) and message.strip():
            return _safe_cli_output(message)
    return _safe_cli_output(stdout or stderr or "lark-cli command failed")


def _decoded_process_output(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _structured_cli_retry_reason(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in {"status", "status_code", "code"}:
                if isinstance(nested, int) and not isinstance(nested, bool):
                    reason = retry_reason_from_http_status(nested)
                    if reason is not None:
                        return reason
                if isinstance(nested, str):
                    normalized = nested.strip().casefold().replace("-", "_").replace(" ", "_")
                    if normalized.isdigit():
                        reason = retry_reason_from_http_status(int(normalized))
                        if reason is not None:
                            return reason
                    if normalized in {
                        "rate_limited",
                        "server_error",
                        "service_unavailable",
                        "temporarily_unavailable",
                        "upstream_timeout",
                        "upstream_unavailable",
                    }:
                        return "cli_transient"
            reason = _structured_cli_retry_reason(nested)
            if reason is not None:
                return reason
    elif isinstance(value, (list, tuple)):
        for nested in value:
            reason = _structured_cli_retry_reason(nested)
            if reason is not None:
                return reason
    return None


def _lark_cli_retry_reason(exc: BaseException) -> str | None:
    reason = classify_transport_error(exc) or classify_payload_error(exc)
    if reason is not None:
        return reason
    if isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError):
        if exc.errno in {
            errno.EAGAIN,
            errno.EBUSY,
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ETIMEDOUT,
        }:
            return "cli_transient"
    if not isinstance(exc, subprocess.CalledProcessError):
        return None
    for raw_output in (exc.output, exc.stderr):
        text = _decoded_process_output(raw_output).strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        reason = _structured_cli_retry_reason(payload)
        if reason is not None:
            return reason
    return None


def _coerce_lark_cli_error(exc: Exception) -> BaseError:
    if isinstance(exc, BaseError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return _fetch_error("lark-cli command timed out", exc)
    if isinstance(exc, FileNotFoundError):
        return _fetch_error("lark-cli is not installed in the deployment environment", exc)
    if isinstance(exc, subprocess.CalledProcessError):
        stdout = _decoded_process_output(exc.output)
        stderr = _decoded_process_output(exc.stderr)
        return _fetch_error(_cli_error_message(stdout, stderr), exc)
    if isinstance(exc, OSError):
        return _fetch_error("lark-cli could not be started", exc)
    return _fetch_error("lark-cli read failed", exc)


async def _run_lark_cli_once(
    argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS, cwd: Path | None = None
) -> tuple[str, str]:
    binary = shutil.which("lark-cli")
    if binary is None:
        raise FileNotFoundError("lark-cli is not installed in the deployment environment")
    kwargs: dict[str, object] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(binary, *argv, **kwargs)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        if process is not None:
            with contextlib.suppress(Exception):
                process.kill()
        raise
    except asyncio.TimeoutError as exc:
        if process is not None:
            with contextlib.suppress(Exception):
                process.kill()
        raise TimeoutError("lark-cli command timed out") from exc
    stdout_text = bytes(stdout or b"").decode("utf-8", errors="replace")
    stderr_text = bytes(stderr or b"").decode("utf-8", errors="replace")
    if len(stdout_text.encode("utf-8")) > _CLI_OUTPUT_BYTES or len(stderr_text.encode("utf-8")) > _CLI_OUTPUT_BYTES:
        raise _fetch_error("lark-cli output exceeds the size limit")
    if process is None or process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode if process is not None else 1,
            [binary, *argv],
            output=bytes(stdout or b""),
            stderr=bytes(stderr or b""),
        )
    return stdout_text, stderr_text


async def _run_lark_cli(
    argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS, cwd: Path | None = None
) -> tuple[str, str]:
    try:
        return await _run_lark_cli_once(argv, timeout_seconds=timeout_seconds, cwd=cwd)
    except Exception as exc:
        raise _coerce_lark_cli_error(exc) from None


async def _run_lark_cli_read(
    argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS, cwd: Path | None = None
) -> tuple[str, str]:
    try:
        return await retry_provider_read(
            lambda: _run_lark_cli_once(argv, timeout_seconds=timeout_seconds, cwd=cwd),
            provider="feishu",
            operation_name="cli_read",
            classify=_lark_cli_retry_reason,
        )
    except Exception as exc:
        raise _coerce_lark_cli_error(exc) from None


async def _run_lark_cli_read_json(argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS) -> object:
    async def read_and_parse_once() -> object:
        stdout, _ = await _run_lark_cli_once(argv, timeout_seconds=timeout_seconds)
        return _parse_cli_json(stdout)

    try:
        return await retry_provider_read(
            read_and_parse_once,
            provider="feishu",
            operation_name="cli_json_read",
            classify=_lark_cli_retry_reason,
        )
    except Exception as exc:
        raise _coerce_lark_cli_error(exc) from None


async def _run_lark_cli_json(argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS) -> object:
    return await _run_lark_cli_read_json(argv, timeout_seconds=timeout_seconds)


async def _run_lark_cli_json_without_retry(argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS) -> object:
    stdout, _ = await _run_lark_cli(argv, timeout_seconds=timeout_seconds)
    return _parse_cli_json(stdout)


def _cli_args(*parts: str) -> list[str]:
    return [*parts, "--as", "user", "--format", "json"]


def _user_auth_status(payload: object) -> tuple[bool, set[str]]:
    if not isinstance(payload, Mapping):
        return False, set()
    identities = payload.get("identities")
    user = identities.get("user") if isinstance(identities, Mapping) else None
    if not isinstance(user, Mapping):
        user = payload
    token_status = user.get("tokenStatus")
    ready = bool(user.get("available") is True and token_status in {"valid", "needs_refresh"})
    raw_scope = user.get("scope")
    if isinstance(raw_scope, str):
        scopes = {item for item in raw_scope.split() if item}
    elif isinstance(raw_scope, (list, tuple, set)):
        scopes = {str(item) for item in raw_scope if str(item).strip()}
    else:
        scopes = set()
    return ready, scopes


def _required_scopes_for_service(config: object) -> tuple[str, ...]:
    source = getattr(config, "source", {})
    if not isinstance(source, Mapping):
        return ()
    mode = str(source.get("mode") or "").casefold()
    result: set[str] = set()
    if mode == "wiki_space":
        result.update({_FEISHU_SCOPE_BY_RESOURCE["wiki"], _FEISHU_SCOPE_BY_RESOURCE["wiki_docs"]})
        return tuple(sorted(result))
    resources = source.get("resources")
    if not isinstance(resources, (list, tuple)):
        return ()
    normalized = {str(item).casefold() for item in resources}
    for resource in normalized:
        if resource in _FEISHU_SCOPE_BY_RESOURCE:
            result.add(_FEISHU_SCOPE_BY_RESOURCE[resource])
    if "docs" in normalized and "document_ids" not in source:
        result.add(_FEISHU_SCOPE_BY_RESOURCE["search"])
    return tuple(sorted(result))


def required_scopes_for_config(config: object) -> tuple[str, ...]:
    services = getattr(config, "fetch_services", ())
    result: set[str] = set()
    if isinstance(services, (list, tuple)):
        for service in services:
            if getattr(service, "provider", None) == "feishu":
                result.update(_required_scopes_for_service(service))
    return tuple(sorted(result))


def supported_read_scopes() -> tuple[str, ...]:
    """Return the complete PCS-supported Feishu read-only scope set."""

    return _SUPPORTED_FEISHU_READ_SCOPES


async def _lark_cli_auth_status(required_scopes: tuple[str, ...]) -> tuple[bool, set[str]]:
    payload = await _run_lark_cli_json(["auth", "status", "--json", "--verify"])
    ready, granted = _user_auth_status(payload)
    return ready and set(required_scopes).issubset(granted), granted


async def _lark_cli_begin_authorization(required_scopes: tuple[str, ...]) -> tuple[str, str, str]:
    if not required_scopes:
        raise _fetch_error("no Feishu read scope is required by the current configuration")
    payload = await _run_lark_cli_json_without_retry(
        ["auth", "login", "--scope", " ".join(required_scopes), "--no-wait", "--json"]
    )
    if not isinstance(payload, Mapping):
        raise _fetch_error("lark-cli authorization response is invalid")
    device_code = _find_nested_text(payload, "device_code")
    verification_url = _find_nested_text(payload, "verification_url")
    if not device_code or not verification_url or urlsplit(verification_url).scheme != "https":
        raise _fetch_error("lark-cli authorization response is missing a valid verification URL")
    expires_raw = _find_nested_text(payload, "expires_at")
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError:
            expires_at = datetime.now(UTC) + timedelta(minutes=10)
    else:
        expires_in = _find_nested_number(payload, "expires_in") or 600
        expires_at = datetime.now(UTC) + timedelta(seconds=max(1, expires_in))
    return device_code, verification_url, expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _lark_cli_finish_authorization(device_code: str, *, timeout_seconds: float) -> None:
    await _run_lark_cli(["auth", "login", "--device-code", device_code], timeout_seconds=timeout_seconds)


def _find_nested_text(value: object, key: str) -> str | None:
    if isinstance(value, Mapping):
        current = value.get(key)
        if isinstance(current, str) and current.strip():
            return current.strip()
        for nested in value.values():
            found = _find_nested_text(nested, key)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_nested_text(nested, key)
            if found:
                return found
    return None


def _find_nested_number(value: object, key: str) -> float | None:
    if isinstance(value, Mapping):
        current = value.get(key)
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            return float(current)
        for nested in value.values():
            found = _find_nested_number(nested, key)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_nested_number(nested, key)
            if found is not None:
                return found
    return None


async def _ensure_lark_cli_authorized(config: object) -> None:
    required = _required_scopes_for_service(config)
    ready, granted = await _lark_cli_auth_status(required)
    if not ready:
        if not granted:
            raise _fetch_error("Feishu lark-cli user authorization is required; call authorize_provider")
        missing = ", ".join(sorted(set(required) - granted))
        raise _fetch_error(f"Feishu lark-cli authorization lacks required scopes: {missing}")


async def _paged_lark_cli(
    argv: list[str],
    *,
    page_token_flag: str = "--page-token",
    name: str,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    next_args = list(argv)
    seen_tokens: set[str] = set()
    for _ in range(_MAX_PAGES):
        payload = await _run_lark_cli_json(next_args)
        if not isinstance(payload, Mapping):
            raise _fetch_error(f"Feishu {name} response is not an object")
        page_items = _as_items(payload, name=name)
        if not page_items:
            return result
        result.extend(page_items)
        token = _page_token(payload)
        if not token:
            return result
        if token in seen_tokens:
            raise _fetch_error(f"Feishu {name} pagination token did not advance")
        seen_tokens.add(token)
        next_args = [*argv, page_token_flag, token]
    raise _fetch_error(f"Feishu {name} pagination exceeds the page limit")


def _string(value: object, *keys: str) -> str | None:
    if isinstance(value, Mapping):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return str(item)
    elif isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stable_identifier(item: Mapping[str, object], *keys: str, fallback: str) -> str:
    identifier = _string(item, *keys)
    if identifier is not None:
        return identifier
    digest = hashlib.sha256(_canonical_json(item).encode("utf-8")).hexdigest()[:16]
    return f"{fallback}:{digest}"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return repr(value)


def _revision(item: Mapping[str, object], content: str | None = None) -> str:
    for key in (
        "revision_id",
        "revision",
        "version",
        "version_token",
        "update_time",
        "updated_at",
        "updated_time",
        "modify_time",
        "modified_time",
        "last_update_time",
    ):
        value = _string(item, key)
        if value:
            return value
    return hashlib.sha256((content or _canonical_json(item)).encode("utf-8")).hexdigest()


def _title(item: Mapping[str, object], fallback: str) -> str:
    return _string(item, "title", "name", "summary", "subject", "title_highlighted") or fallback


def _original_ref(item: Mapping[str, object], fallback: str) -> str:
    return _string(item, "url", "link", "html_url", "web_url", "html_link") or fallback


def _markdown(value: object) -> str:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for part in (_markdown(item) for item in value) if part)
    if not isinstance(value, Mapping):
        return ""
    text = _string(value, "text", "content", "plain_text", "markdown", "description", "notes")
    if text is not None:
        return text.replace("\r\n", "\n").replace("\r", "\n")
    for key in ("elements", "children", "blocks", "paragraphs", "items", "content"):
        nested = value.get(key)
        if isinstance(nested, (list, Mapping)):
            return _markdown(nested)
    return ""


def _json_snapshot(value: object) -> bytes | None:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return raw if len(raw) <= _MAX_RAW_BYTES else None


def _make_upsert(
    *,
    logical_id: str,
    resource: str,
    payload: Mapping[str, object],
    title: str,
    content: object,
    original_ref: str,
    revision_id: str,
    **metadata: object,
) -> RawChangeItem:
    full_markdown = _markdown(content)
    markdown = full_markdown[:_MAX_CONTENT_CHARS]
    if not markdown.strip():
        markdown = title
    snapshot = _json_snapshot(payload)
    return RawChangeItem(
        logical_id=logical_id,
        revision_id=revision_id,
        operation="upsert",
        title=title,
        content=markdown,
        original_ref=original_ref,
        metadata={
            "resource": resource,
            "content_truncated": len(full_markdown) > _MAX_CONTENT_CHARS,
            "raw_snapshot_omitted": snapshot is None,
            **metadata,
        },
        raw_snapshot=snapshot,
    )


def _parse_time(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key in ("date_time", "timestamp", "time", "start_time", "value"):
            if key in value:
                parsed = _parse_time(value[key])
                if parsed is not None:
                    return parsed
        return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            timestamp = float(text)
        except ValueError:
            try:
                parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed_datetime.tzinfo is None:
                parsed_datetime = parsed_datetime.replace(tzinfo=UTC)
            return parsed_datetime.astimezone(UTC).isoformat().replace("+00:00", "Z")
    else:
        return None
    while abs(timestamp) >= 100_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _first_time(payload: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in payload:
            value = _parse_time(payload[key])
            if value is not None:
                return value
    return None


def _resource_time(payload: Mapping[str, object], resource: str) -> str | None:
    if resource == "docs":
        return _first_time(
            payload,
            (
                "update_time",
                "updated_at",
                "updated_time",
                "modify_time",
                "modified_time",
                "last_update_time",
                "create_time",
                "created_at",
                "created_time",
            ),
        )
    if resource == "tasks":
        return _first_time(
            payload,
            ("update_time", "updated_at", "updated_time", "create_time", "created_at", "created_time"),
        )
    if resource == "calendar":
        return _first_time(payload, ("start", "start_time", "event_time"))
    if resource == "wiki":
        return _first_time(payload, ("update_time", "updated_at", "updated_time", "obj_edit_time"))
    return None


def _candidate(
    *,
    resource: str,
    stable_id: str,
    revision_id: str,
    locator: str,
    metadata: Mapping[str, object],
    time_range: Mapping[str, object],
    run_started_at: datetime,
    extra: Mapping[str, object],
) -> dict[str, object] | None:
    candidate_time = _resource_time(metadata, resource)
    if candidate_time is None:
        if time_range.get("mode") != "all":
            raise _fetch_error(f"Feishu {resource} candidate has no usable time")
        candidate_time = _EPOCH
    if not candidate_in_time_range(candidate_time, time_range, run_started_at):
        return None
    return {
        "resource_lane": resource,
        "stable_id": stable_id,
        "revision_id": revision_id,
        "candidate_time": candidate_time,
        "locator": locator,
        **dict(extra),
    }


def _wiki_node(raw: Mapping[str, object], *, parent: str | None, path: list[str]) -> dict[str, object]:
    token = _stable_identifier(raw, "node_token", "token", "id", fallback="node")
    title = _title(raw, token)
    return {
        "node_token": token,
        "obj_token": _string(raw, "obj_token", "object_token", "token") or token,
        "obj_type": _string(raw, "obj_type", "object_type", "type") or "docx",
        "title": title,
        "parent_node_token": parent,
        "has_child": bool(raw.get("has_child", raw.get("has_children", False))),
        "update_time": _string(raw, "update_time", "updated_at", "obj_edit_time", "revision"),
        "path": [*path, title][:_MAX_WIKI_PATH_PARTS],
    }


async def _scan_wiki(
    *,
    space_id: str,
    parent: str | None,
    path: list[str],
    depth: int,
    max_depth: int,
    max_nodes: int,
) -> list[dict[str, object]]:
    if len(path) > max_depth + 1 or depth > max_depth or max_nodes <= 0:
        return []
    args = _cli_args("wiki", "+node-list", "--space-id", space_id, "--page-size", str(min(50, max_nodes)))
    if parent:
        args.extend(["--parent-node-token", parent])
    raw_nodes = await _paged_lark_cli(args, name="wiki nodes")
    result: list[dict[str, object]] = []
    for raw in raw_nodes:
        node = _wiki_node(raw, parent=parent, path=path)
        result.append(node)
        if len(result) >= max_nodes:
            break
        if node["has_child"] is True and depth < max_depth:
            children = await _scan_wiki(
                space_id=space_id,
                parent=str(node["node_token"]),
                path=list(node["path"]) if isinstance(node["path"], list) else [],
                depth=depth + 1,
                max_depth=max_depth,
                max_nodes=max_nodes - len(result),
            )
            result.extend(children)
            if len(result) >= max_nodes:
                break
    return result


async def _fetch_wiki_content(
    node: Mapping[str, object],
    *,
    home: Path,
) -> tuple[Mapping[str, object], str]:
    obj_token = str(node.get("obj_token") or node.get("node_token"))
    obj_type = str(node.get("obj_type") or "docx")
    if obj_type == "docx":
        payload_value = await _run_lark_cli_json(
            _cli_args("docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown")
        )
        payload = _as_object(payload_value, name="wiki document")
        _, content = _content_from_payload(payload)
        return payload, _markdown(content)
    if obj_type == "file":
        sandbox_root = home / "workspace" / "sandboxes"
        sandbox_root.mkdir(parents=True, exist_ok=True)
        stdout, raw = await _download_wiki_file(
            _cli_args(
                "drive",
                "+download",
                "--file-token",
                obj_token,
                "--output",
                "./download",
                "--overwrite",
            ),
            sandbox_root=sandbox_root,
        )
        return {"node": dict(node), "cli_result": _safe_cli_output(stdout)}, raw.decode("utf-8", errors="replace")
    return {"node": dict(node)}, _markdown(node.get("title") or obj_token)


async def _download_wiki_file(argv: list[str], *, sandbox_root: Path) -> tuple[str, bytes]:
    async def download_once() -> tuple[str, bytes]:
        with tempfile.TemporaryDirectory(prefix="feishu-", dir=sandbox_root) as temporary:
            attempt_root = Path(temporary)
            output_path = attempt_root / "download"
            stdout, _ = await _run_lark_cli_once(argv, cwd=attempt_root)
            try:
                raw = output_path.read_bytes()
            except OSError as exc:
                raise _fetch_error("Feishu Wiki file download did not produce a readable file", exc) from None
            if not raw:
                raise EOFError("Feishu Wiki file download is empty")
            return stdout, raw

    try:
        return await retry_provider_read(
            download_once,
            provider="feishu",
            operation_name="cli_file_download",
            classify=_lark_cli_retry_reason,
        )
    except Exception as exc:
        raise _coerce_lark_cli_error(exc) from None


class FeishuFetchService(ContextFetchService):
    """Fetch Feishu docs, tasks, calendar events or Wiki nodes."""

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id
        try:
            await _ensure_lark_cli_authorized(self._config)
            source = dict(self._config.source)
            mode = str(source.get("mode") or "").casefold()
            candidates = (
                await self._discover_wiki(source=source, run_started_at=run_started_at)
                if mode == "wiki_space"
                else await self._discover_account(source=source, run_started_at=run_started_at)
            )
            return select_latest_candidates(
                tuple(candidates),
                cursor,
                self._config.max_items_per_run or _DEFAULT_MAX_ITEMS,
            )
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error(f"Feishu preparation failed: {_safe_detail(exc)}", exc) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        try:
            next_cursor = dict(cursor) if cursor is not None else {}
            if not candidates:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                return
            for index in range(0, len(candidates), _BATCH_SIZE):
                end = index + _BATCH_SIZE
                items = [await self._read_candidate(candidate) for candidate in candidates[index:end]]
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
            raise _fetch_error(f"Feishu fetch failed: {_safe_detail(exc)}", exc) from None

    async def _discover_account(
        self,
        *,
        source: Mapping[str, object],
        run_started_at: datetime,
    ) -> list[dict[str, object]]:
        raw_resources = source.get("resources")
        resources = [str(item) for item in raw_resources] if isinstance(raw_resources, (list, tuple)) else []
        candidates: list[dict[str, object]] = []
        if "docs" in resources:
            candidates.extend(await self._discover_docs(source=source, run_started_at=run_started_at))
        if "tasks" in resources:
            values = await _paged_lark_cli(_cli_args("task", "+get-my-tasks"), name="tasks")
            for value in values:
                identifier = _stable_identifier(value, "guid", "task_id", "id", "url", fallback="task")
                logical_id = f"feishu:task:{identifier}"
                candidate = _candidate(
                    resource="tasks",
                    stable_id=logical_id,
                    revision_id=_revision(value),
                    locator=_original_ref(value, f"feishu://task/{identifier}"),
                    metadata=value,
                    time_range=self._config.time_range,
                    run_started_at=run_started_at,
                    extra={"kind": "task", "payload": value, "identifier": identifier},
                )
                if candidate is not None:
                    candidates.append(candidate)
        if "calendar" in resources:
            args = _cli_args("calendar", "+agenda")
            if isinstance(source.get("start"), str):
                args.extend(["--start", str(source["start"])])
            if isinstance(source.get("end"), str):
                args.extend(["--end", str(source["end"])])
            payload = await _run_lark_cli_json(args)
            for value in _as_items(payload, name="calendar"):
                identifier = _stable_identifier(value, "event_id", "id", "uid", "url", fallback="event")
                logical_id = f"feishu:calendar:{identifier}"
                candidate = _candidate(
                    resource="calendar",
                    stable_id=logical_id,
                    revision_id=_revision(value),
                    locator=_original_ref(value, f"feishu://calendar/{identifier}"),
                    metadata=value,
                    time_range=self._config.time_range,
                    run_started_at=run_started_at,
                    extra={"kind": "calendar", "payload": value, "identifier": identifier},
                )
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    async def _discover_docs(
        self,
        *,
        source: Mapping[str, object],
        run_started_at: datetime,
    ) -> list[dict[str, object]]:
        raw_ids = source.get("document_ids")
        if isinstance(raw_ids, (list, tuple)):
            found: list[dict[str, object]] = []
            for raw_id in raw_ids:
                document_id = str(raw_id)
                payload = _as_object(
                    await _run_lark_cli_json(
                        _cli_args("docs", "+fetch", "--doc", document_id, "--doc-format", "markdown")
                    ),
                    name="document",
                )
                metadata, content = _content_from_payload(payload)
                found.append(
                    {
                        "document_id": document_id,
                        "metadata": dict(metadata),
                        "payload": payload,
                        "content": content,
                    }
                )
        else:
            query = source.get("query")
            args = _cli_args("docs", "+search", "--page-size", "20")
            if isinstance(query, str):
                args.extend(["--query", query])
            found = []
            for value in await _paged_lark_cli(args, name="document search"):
                content = value.get("content") or value.get("summary") or value.get("description")
                found.append(
                    {
                        "document_id": _stable_identifier(
                            value,
                            "document_id",
                            "doc_id",
                            "token",
                            "id",
                            fallback="doc",
                        ),
                        "metadata": value,
                        "payload": value if content is not None else None,
                        "content": content,
                    }
                )
        result: list[dict[str, object]] = []
        for discovered in found:
            discovered_metadata = discovered.get("metadata")
            if not isinstance(discovered_metadata, Mapping):
                raise _fetch_error("Feishu document metadata is invalid")
            document_id = str(discovered["document_id"])
            logical_id = f"feishu:doc:{document_id}"
            content = discovered.get("content")
            revision = _revision(
                discovered_metadata,
                _markdown(content) if content is not None else None,
            )
            candidate = _candidate(
                resource="docs",
                stable_id=logical_id,
                revision_id=revision,
                locator=_original_ref(discovered_metadata, f"feishu://doc/{document_id}"),
                metadata=discovered_metadata,
                time_range=self._config.time_range,
                run_started_at=run_started_at,
                extra={
                    "kind": "doc",
                    "document_id": document_id,
                    "metadata": dict(discovered_metadata),
                    "payload": discovered.get("payload"),
                    "content": content,
                    "query": source.get("query"),
                },
            )
            if candidate is not None:
                result.append(candidate)
        return result

    async def _discover_wiki(
        self,
        *,
        source: Mapping[str, object],
        run_started_at: datetime,
    ) -> list[dict[str, object]]:
        space_id = str(source.get("wiki_space_id") or "").strip()
        if not space_id:
            raise _fetch_error("Feishu wiki_space requires wiki_space_id")
        raw_depth = source.get("max_depth", 3)
        raw_nodes = source.get("max_nodes", 200)
        max_depth = raw_depth if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else 3
        max_nodes = raw_nodes if isinstance(raw_nodes, int) and not isinstance(raw_nodes, bool) else 200
        nodes = await _scan_wiki(
            space_id=space_id,
            parent=str(source["root_node_token"]) if source.get("root_node_token") else None,
            path=[],
            depth=0,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        result: list[dict[str, object]] = []
        for node in nodes:
            node_token = str(node["node_token"])
            logical_id = f"feishu:wiki:{space_id}:{node_token}"
            candidate = _candidate(
                resource="wiki",
                stable_id=logical_id,
                revision_id=_revision(node),
                locator=f"feishu://wiki/{space_id}/{node_token}",
                metadata=node,
                time_range=self._config.time_range,
                run_started_at=run_started_at,
                extra={"kind": "wiki", "space_id": space_id, "node": node},
            )
            if candidate is not None:
                result.append(candidate)
        return result

    async def _read_candidate(self, candidate: Mapping[str, object]) -> RawChangeItem:
        kind = candidate.get("kind")
        revision_id = str(candidate.get("revision_id") or "")
        logical_id = str(candidate.get("stable_id") or "")
        locator = str(candidate.get("locator") or "")
        if not revision_id or not logical_id or not locator:
            raise _fetch_error("Feishu candidate is invalid")
        if kind == "doc":
            document_id = str(candidate.get("document_id") or "")
            payload = candidate.get("payload")
            content = candidate.get("content")
            metadata = candidate.get("metadata")
            if not isinstance(metadata, Mapping):
                raise _fetch_error("Feishu document candidate is invalid")
            if not isinstance(payload, Mapping):
                payload = _as_object(
                    await _run_lark_cli_json(
                        _cli_args("docs", "+fetch", "--doc", document_id, "--doc-format", "markdown")
                    ),
                    name="document",
                )
                fetched_metadata, content = _content_from_payload(payload)
                metadata = fetched_metadata
            return _make_upsert(
                logical_id=logical_id,
                resource="docs",
                payload=payload,
                title=_title(metadata, f"Feishu doc {document_id}"),
                content=content,
                original_ref=locator,
                revision_id=revision_id,
                document_id=document_id,
                query=candidate.get("query"),
            )
        if kind in {"task", "calendar"}:
            payload = candidate.get("payload")
            if not isinstance(payload, Mapping):
                raise _fetch_error("Feishu list candidate is invalid")
            identifier = str(candidate.get("identifier") or "")
            if kind == "task":
                content = payload.get("notes") or payload.get("description") or payload.get("content")
                title = _title(payload, f"Feishu task {identifier}")
                metadata = {"task_id": identifier}
                resource = "tasks"
            else:
                content = payload.get("description") or payload.get("content")
                title = _title(payload, f"Feishu event {identifier}")
                metadata = {
                    "event_id": identifier,
                    "start": payload.get("start") or payload.get("start_time"),
                    "end": payload.get("end") or payload.get("end_time"),
                }
                resource = "calendar"
            return _make_upsert(
                logical_id=logical_id,
                resource=resource,
                payload=payload,
                title=title,
                content=content or title,
                original_ref=locator,
                revision_id=revision_id,
                **metadata,
            )
        if kind == "wiki":
            node = candidate.get("node")
            if not isinstance(node, Mapping):
                raise _fetch_error("Feishu Wiki candidate is invalid")
            payload, content = await _fetch_wiki_content(node, home=self._home)
            return _make_upsert(
                logical_id=logical_id,
                resource="wiki",
                payload=payload,
                title=_title(node, str(node.get("node_token") or "Wiki")),
                content=content,
                original_ref=locator,
                revision_id=revision_id,
                space_id=candidate.get("space_id"),
                node_token=node.get("node_token"),
                wiki_path=node.get("path"),
                obj_type=node.get("obj_type"),
            )
        raise _fetch_error("Feishu candidate kind is invalid")
