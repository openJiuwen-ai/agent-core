"""Feishu ``lark-cli`` provider for the embedded personal-context core.

The provider intentionally keeps the implementation in one production class.
CLI execution, JSON decoding, pagination, bounded markdown conversion and
cursor helpers are module-private functions so the public PersonalContext class inventory
stays closed.  The provider never receives or stores a Feishu access token.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
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
_FEISHU_SCOPE_BY_RESOURCE = {
    "docs": "docs:document.content:read",
    "search": "search:docs:read",
    "tasks": "task:task:read",
    "calendar": "calendar:calendar.event:read",
    "wiki": "wiki:node:retrieve",
    "wiki_docs": "docs:document.content:read",
    "wiki_files": "drive:file:download",
}


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


def _payload_data(payload: Mapping[str, object]) -> object:
    data = payload.get("data")
    return data if data is not None else payload


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
    result: list[dict[str, object]] = []
    for item in values:
        if isinstance(item, Mapping):
            result.append(dict(item))
    return result


def _page_info(payload: Mapping[str, object]) -> tuple[bool, str | None]:
    data = _payload_data(payload)
    if not isinstance(data, Mapping):
        return False, None
    has_more = bool(data.get("has_more", data.get("hasMore", False)))
    token = data.get("page_token") or data.get("next_page_token") or data.get("nextPageToken")
    return has_more, token.strip() if isinstance(token, str) and token.strip() else None


def _parse_cli_json(stdout: str) -> object:
    text = stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value
    raise _fetch_error("lark-cli did not return JSON")


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


async def _run_lark_cli(
    argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS, cwd: Path | None = None
) -> tuple[str, str]:
    binary = shutil.which("lark-cli")
    if binary is None:
        raise _fetch_error("lark-cli is not installed in the deployment environment")
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
        raise _fetch_error("lark-cli command timed out") from exc
    except FileNotFoundError as exc:
        raise _fetch_error("lark-cli is not installed in the deployment environment", exc) from None
    except OSError as exc:
        raise _fetch_error("lark-cli could not be started", exc) from None
    stdout_text = bytes(stdout or b"").decode("utf-8", errors="replace")
    stderr_text = bytes(stderr or b"").decode("utf-8", errors="replace")
    if len(stdout_text.encode("utf-8")) > _CLI_OUTPUT_BYTES or len(stderr_text.encode("utf-8")) > _CLI_OUTPUT_BYTES:
        raise _fetch_error("lark-cli output exceeds the size limit")
    if process is None or process.returncode != 0:
        raise _fetch_error(_cli_error_message(stdout_text, stderr_text))
    return stdout_text, stderr_text


async def _run_lark_cli_json(argv: list[str], *, timeout_seconds: float = _CLI_TIMEOUT_SECONDS) -> object:
    stdout, stderr = await _run_lark_cli(argv, timeout_seconds=timeout_seconds)
    del stderr
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
    for resource in resources:
        name = str(resource).casefold()
        if name in _FEISHU_SCOPE_BY_RESOURCE:
            result.add(_FEISHU_SCOPE_BY_RESOURCE[name])
    if "docs" in {str(item).casefold() for item in resources} and "document_ids" not in source:
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


async def _lark_cli_auth_status(required_scopes: tuple[str, ...]) -> tuple[bool, set[str]]:
    payload = await _run_lark_cli_json(["auth", "status", "--json", "--verify"])
    ready, granted = _user_auth_status(payload)
    return ready and set(required_scopes).issubset(granted), granted


async def _lark_cli_begin_authorization(required_scopes: tuple[str, ...]) -> tuple[str, str, str]:
    if not required_scopes:
        raise _fetch_error("no Feishu read scope is required by the current configuration")
    payload = await _run_lark_cli_json(["auth", "login", "--scope", " ".join(required_scopes), "--no-wait", "--json"])
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
    for _ in range(_MAX_PAGES):
        payload = await _run_lark_cli_json(next_args)
        if not isinstance(payload, Mapping):
            raise _fetch_error(f"Feishu {name} response is not an object")
        result.extend(_as_items(payload, name=name))
        has_more, page_token = _page_info(payload)
        if not has_more:
            return result
        if not page_token:
            raise _fetch_error(f"Feishu {name} page is missing its continuation token")
        next_args = [*argv, page_token_flag, page_token]
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
    if identifier is None:
        digest = hashlib.sha256(_canonical_json(item).encode("utf-8")).hexdigest()[:16]
        return f"{fallback}:{digest}"
    return identifier


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
    """Convert common Feishu text/block payloads into bounded Markdown text."""

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


def _bounded_content(value: object) -> tuple[str, bool]:
    text = _markdown(value)
    truncated = len(text) > _MAX_CONTENT_CHARS
    return text[:_MAX_CONTENT_CHARS], truncated


def _item_metadata(
    *,
    resource: str,
    payload: Mapping[str, object],
    content_truncated: bool,
    raw_snapshot: bytes | None,
    **extra: object,
) -> dict[str, object]:
    return {
        "resource": resource,
        "content_truncated": content_truncated,
        "raw_snapshot_omitted": raw_snapshot is None,
        **extra,
    }


def _make_upsert(
    *,
    logical_id: str,
    resource: str,
    payload: Mapping[str, object],
    title: str,
    content: object,
    original_ref: str,
    revision_id: str | None = None,
    **metadata: object,
) -> RawChangeItem:
    full_markdown = _markdown(content)
    markdown, truncated = _bounded_content(full_markdown)
    if not markdown.strip():
        markdown = title
    snapshot = _json_snapshot(payload)
    return RawChangeItem(
        logical_id=logical_id,
        revision_id=revision_id or _revision(payload, full_markdown),
        operation="upsert",
        title=title,
        content=markdown,
        original_ref=original_ref,
        metadata=_item_metadata(
            resource=resource,
            payload=payload,
            content_truncated=truncated,
            raw_snapshot=snapshot,
            **metadata,
        ),
        raw_snapshot=snapshot,
    )


def _make_delete(
    *,
    logical_id: str,
    resource: str,
    title: str,
    original_ref: str,
    revision_id: str | None,
    **metadata: object,
) -> RawChangeItem:
    return RawChangeItem(
        logical_id=logical_id,
        revision_id=f"deleted:{revision_id or 'unknown'}",
        operation="delete",
        title=title,
        original_ref=original_ref,
        metadata={"resource": resource, **metadata},
    )


def _merge_cursor(target: dict[str, object], update: Mapping[str, object]) -> None:
    for key, value in update.items():
        target[key] = deepcopy(value)


def _batches(
    pending: list[tuple[RawChangeItem, Mapping[str, object]]],
    cursor: Mapping[str, object],
) -> list[FetchBatch]:
    if not pending:
        return [FetchBatch(batch_id="batch-0", items=(), next_cursor=deepcopy(dict(cursor)))]
    result: list[FetchBatch] = []
    current = deepcopy(dict(cursor))
    for index in range(0, len(pending), _BATCH_SIZE):
        end = index + _BATCH_SIZE
        chunk = pending[index:end]
        for _, update in chunk:
            _merge_cursor(current, update)
        result.append(
            FetchBatch(
                batch_id=f"batch-{index // _BATCH_SIZE}",
                items=tuple(item for item, _ in chunk),
                next_cursor=deepcopy(current),
            )
        )
    return result


def _resource_cursor(cursor: Mapping[str, object], key: str) -> dict[str, object]:
    value = cursor.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _item_revision_map(cursor: Mapping[str, object]) -> dict[str, object]:
    value = cursor.get("items")
    if value is None:
        value = cursor.get("documents")
    return dict(value) if isinstance(value, Mapping) else {}


def _validate_cursor(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _fetch_error("Feishu cursor must be an object")
    allowed = {"docs", "tasks", "calendar", "wiki_space"}
    if set(value) - allowed:
        raise _fetch_error("Feishu cursor contains unsupported fields")
    result = deepcopy(dict(value))
    for key, raw_resource in result.items():
        if not isinstance(raw_resource, Mapping):
            raise _fetch_error(f"Feishu {key} cursor is invalid")
        resource = dict(raw_resource)
        allowed_resource_keys = {
            "docs": {"items", "documents", "document_ids", "known_ids", "query"},
            "tasks": {"items", "known_ids"},
            "calendar": {"items", "known_ids", "start", "end"},
            "wiki_space": {"nodes", "known_ids"},
        }[key]
        if set(resource) - allowed_resource_keys:
            raise _fetch_error(f"Feishu {key} cursor contains unsupported fields")
        for item_key in ("items", "documents"):
            nested = resource.get(item_key)
            if nested is not None and not isinstance(nested, Mapping):
                raise _fetch_error(f"Feishu {key} cursor is invalid")
            if isinstance(nested, Mapping):
                for item_value in nested.values():
                    if not isinstance(item_value, Mapping):
                        raise _fetch_error(f"Feishu {key} cursor is invalid")
                    revision = item_value.get("revision_id")
                    if revision is not None and not isinstance(revision, str):
                        raise _fetch_error(f"Feishu {key} cursor is invalid")
        if key == "docs":
            document_ids = resource.get("document_ids")
            if document_ids is not None:
                if not isinstance(document_ids, list) or any(
                    not isinstance(document_id, str) or not document_id.strip() for document_id in document_ids
                ):
                    raise _fetch_error("Feishu docs cursor is invalid")
        known_ids = resource.get("known_ids")
        if known_ids is not None:
            if not isinstance(known_ids, list) or any(
                not isinstance(identifier, str) or not identifier.strip() for identifier in known_ids
            ):
                raise _fetch_error(f"Feishu {key} cursor is invalid")
        if key == "wiki_space":
            nodes = resource.get("nodes")
            if nodes is not None and not isinstance(nodes, Mapping):
                raise _fetch_error("Feishu wiki_space cursor is invalid")
            if isinstance(nodes, Mapping):
                for node in nodes.values():
                    if not isinstance(node, Mapping):
                        raise _fetch_error("Feishu wiki_space cursor is invalid")
        result[key] = resource
    return result


def _wiki_node(raw: Mapping[str, object], *, parent: str | None, path: list[str]) -> dict[str, object]:
    token = _stable_identifier(raw, "node_token", "token", "id", fallback="node")
    title = _title(raw, token)
    node_path = [*path, title][:_MAX_WIKI_PATH_PARTS]
    return {
        "node_token": token,
        "obj_token": _string(raw, "obj_token", "object_token", "token") or token,
        "obj_type": _string(raw, "obj_type", "object_type", "type") or "docx",
        "title": title,
        "parent_node_token": parent,
        "has_child": bool(raw.get("has_child", raw.get("has_children", False))),
        "update_time": _string(raw, "update_time", "updated_at", "obj_edit_time", "revision"),
        "path": node_path,
    }


def _wiki_change_sort_value(value: object) -> str:
    node = value if isinstance(value, Mapping) else {}
    return _string(node, "update_time", "updated_at", "obj_edit_time", "title") or ""


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
    args = _cli_args(
        "wiki",
        "+node-list",
        "--space-id",
        space_id,
        "--page-size",
        str(min(50, max_nodes)),
    )
    if parent:
        args.extend(["--parent-node-token", parent])
    raw_nodes = await _paged_lark_cli(
        args,
        name="wiki nodes",
    )
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
        with tempfile.TemporaryDirectory(prefix="feishu-", dir=sandbox_root) as temporary:
            output_path = Path(temporary) / "download"
            stdout, _ = await _run_lark_cli(
                _cli_args(
                    "drive",
                    "+download",
                    "--file-token",
                    obj_token,
                    "--output",
                    "./download",
                    "--overwrite",
                ),
                cwd=Path(temporary),
            )
            try:
                raw = output_path.read_bytes()
            except OSError as exc:
                raise _fetch_error("Feishu Wiki file download did not produce a readable file", exc) from None
            content = raw.decode("utf-8", errors="replace")
            return {"node": dict(node), "cli_result": _safe_cli_output(stdout)}, content
    return {"node": dict(node)}, _markdown(node.get("title") or obj_token)


class FeishuFetchService(ContextFetchService):
    """Fetch Feishu docs, tasks, calendar events or Wiki nodes."""

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        try:
            await _ensure_lark_cli_authorized(self._config)
            batches = await self._fetch_impl(cursor=cursor)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise _fetch_error(f"Feishu fetch failed: {_safe_detail(exc)}", exc) from None
        for batch in batches:
            yield batch

    async def _fetch_impl(self, *, cursor: dict[str, object] | None) -> list[FetchBatch]:
        old_cursor = _validate_cursor(cursor)
        source = dict(self._config.source)
        mode = str(source.get("mode") or "").casefold()
        max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
        if mode == "wiki_space":
            return await self._fetch_wiki(source=source, cursor=old_cursor, max_items=max_items)
        return await self._fetch_account(source=source, cursor=old_cursor, max_items=max_items)

    async def _fetch_account(
        self,
        *,
        source: dict[str, object],
        cursor: dict[str, object],
        max_items: int,
    ) -> list[FetchBatch]:
        resources_value = source.get("resources")
        resources = [str(item) for item in resources_value] if isinstance(resources_value, (list, tuple)) else []
        latest: list[tuple[RawChangeItem, str, str, object]] = []
        history: list[tuple[RawChangeItem, str, str, object]] = []
        states: dict[str, dict[str, object]] = {}
        if "docs" in resources:
            doc_cursor = _resource_cursor(cursor, "docs")
            previous_items = _item_revision_map(doc_cursor)
            known_ids = {str(item) for item in doc_cursor.get("known_ids", []) if isinstance(item, str)}
            document_ids = source.get("document_ids")
            if isinstance(document_ids, (list, tuple)):
                ids = [str(item) for item in document_ids]
                query = None
                found = [{"document_id": identifier} for identifier in ids]
            else:
                query = source.get("query")
                search_args = _cli_args("docs", "+search", "--page-size", "20")
                if isinstance(query, str):
                    search_args.extend(["--query", query])
                found = await _paged_lark_cli(
                    search_args,
                    name="document search",
                )
            current_ids = [
                _stable_identifier(result, "document_id", "doc_id", "token", "id", fallback="doc") for result in found
            ]
            states["docs"] = {
                "items": deepcopy(previous_items),
                "document_ids": current_ids,
                "known_ids": sorted(known_ids | set(current_ids)),
                "query": query,
            }
            new_candidates: list[tuple[RawChangeItem, str, str, object]] = []
            changed_candidates: list[tuple[RawChangeItem, str, str, object]] = []
            history_candidates: list[tuple[RawChangeItem, str, str, object]] = []
            for result, doc_id in zip(found, current_ids, strict=False):
                content = result.get("content") or result.get("summary") or result.get("description")
                payload: Mapping[str, object] = result
                if not content:
                    raw_value = await _run_lark_cli_json(
                        _cli_args("docs", "+fetch", "--doc", doc_id, "--doc-format", "markdown")
                    )
                    payload = _as_object(raw_value, name="document")
                    payload_data, content = _content_from_payload(payload)
                else:
                    payload_data, _ = _content_from_payload(payload)
                if content is None:
                    content = ""
                title = _title(result, f"Feishu doc {doc_id}")
                if isinstance(payload_data, Mapping):
                    title = _title(payload_data, title)
                item = _make_upsert(
                    logical_id=f"feishu:doc:{doc_id}",
                    resource="docs",
                    payload=payload,
                    title=title,
                    content=content,
                    original_ref=_original_ref(result, f"feishu://doc/{doc_id}"),
                    revision_id=_revision(result, _markdown(content)),
                    query=query,
                )
                previous = previous_items.get(doc_id)
                entry = {"revision_id": item.revision_id, "title": title}
                if isinstance(previous, Mapping) and previous.get("revision_id") == item.revision_id:
                    continue
                candidate = (item, "docs", doc_id, entry)
                if isinstance(previous, Mapping):
                    changed_candidates.append(candidate)
                elif doc_id not in known_ids:
                    new_candidates.append(candidate)
                else:
                    history_candidates.append(candidate)
            if known_ids:
                new_candidates.sort(key=lambda value: value[2], reverse=True)
            latest.extend([*new_candidates, *changed_candidates])
            history.extend(history_candidates)
        if "tasks" in resources:
            task_latest, task_history, task_state = await self._fetch_task_candidates(cursor=cursor)
            states["tasks"] = task_state
            latest.extend(task_latest)
            history.extend(task_history)
        if "calendar" in resources:
            calendar_latest, calendar_history, calendar_state = await self._fetch_calendar_candidates(
                source=source, cursor=cursor
            )
            states["calendar"] = calendar_state
            latest.extend(calendar_latest)
            history.extend(calendar_history)

        selected = [*latest, *history][:max_items]
        running = deepcopy(states)
        pending: list[tuple[RawChangeItem, Mapping[str, object]]] = []
        for item, resource, identifier, entry in selected:
            state = running[resource]
            items = state.setdefault("items", {})
            if not isinstance(items, dict):
                items = {}
                state["items"] = items
            items[identifier] = deepcopy(entry)
            pending.append((item, {resource: deepcopy(state)}))
        return _batches(pending, cursor)

    async def _fetch_task_candidates(
        self, *, cursor: Mapping[str, object]
    ) -> tuple[
        list[tuple[RawChangeItem, str, str, object]], list[tuple[RawChangeItem, str, str, object]], dict[str, object]
    ]:
        values = await _paged_lark_cli(_cli_args("task", "+get-my-tasks"), name="tasks")
        resource = _resource_cursor(cursor, "tasks")
        previous = _item_revision_map(resource)
        known_ids = {str(item) for item in resource.get("known_ids", []) if isinstance(item, str)}
        current_ids: list[str] = []
        latest: list[tuple[RawChangeItem, str, str, object]] = []
        history: list[tuple[RawChangeItem, str, str, object]] = []
        state: dict[str, object] = {"items": deepcopy(previous), "known_ids": []}
        for value in values:
            identifier = _stable_identifier(value, "guid", "task_id", "id", "url", fallback="task")
            current_ids.append(identifier)
            content = (
                value.get("notes") or value.get("description") or value.get("content") or _title(value, identifier)
            )
            item = _make_upsert(
                logical_id=f"feishu:task:{identifier}",
                resource="tasks",
                payload=value,
                title=_title(value, f"Feishu task {identifier}"),
                content=content,
                original_ref=_original_ref(value, f"feishu://task/{identifier}"),
                revision_id=_revision(value, _markdown(content)),
                task_id=identifier,
            )
            previous_item = previous.get(identifier)
            if isinstance(previous_item, Mapping) and previous_item.get("revision_id") == item.revision_id:
                continue
            candidate = (item, "tasks", identifier, {"revision_id": item.revision_id, "title": item.title})
            if isinstance(previous_item, Mapping) or identifier not in known_ids:
                latest.append(candidate)
            else:
                history.append(candidate)
        state["known_ids"] = sorted(known_ids | set(current_ids))
        return latest, history, state

    async def _fetch_calendar_candidates(
        self, *, source: Mapping[str, object], cursor: Mapping[str, object]
    ) -> tuple[
        list[tuple[RawChangeItem, str, str, object]], list[tuple[RawChangeItem, str, str, object]], dict[str, object]
    ]:
        args = _cli_args("calendar", "+agenda")
        if isinstance(source.get("start"), str):
            args.extend(["--start", str(source["start"])])
        if isinstance(source.get("end"), str):
            args.extend(["--end", str(source["end"])])
        payload = await _run_lark_cli_json(args)
        values = _as_items(payload, name="calendar")
        resource = _resource_cursor(cursor, "calendar")
        previous = _item_revision_map(resource)
        known_ids = {str(item) for item in resource.get("known_ids", []) if isinstance(item, str)}
        current_ids: list[str] = []
        latest: list[tuple[RawChangeItem, str, str, object]] = []
        history: list[tuple[RawChangeItem, str, str, object]] = []
        state: dict[str, object] = {
            "items": deepcopy(previous),
            "known_ids": [],
            "start": source.get("start"),
            "end": source.get("end"),
        }
        for value in values:
            identifier = _stable_identifier(value, "event_id", "id", "uid", "url", fallback="event")
            current_ids.append(identifier)
            content = value.get("description") or value.get("content") or _title(value, identifier)
            item = _make_upsert(
                logical_id=f"feishu:calendar:{identifier}",
                resource="calendar",
                payload=value,
                title=_title(value, f"Feishu event {identifier}"),
                content=content,
                original_ref=_original_ref(value, f"feishu://calendar/{identifier}"),
                revision_id=_revision(value, _markdown(content)),
                event_id=identifier,
                start=value.get("start") or value.get("start_time"),
                end=value.get("end") or value.get("end_time"),
            )
            previous_item = previous.get(identifier)
            if isinstance(previous_item, Mapping) and previous_item.get("revision_id") == item.revision_id:
                continue
            candidate = (item, "calendar", identifier, {"revision_id": item.revision_id, "title": item.title})
            if isinstance(previous_item, Mapping) or identifier not in known_ids:
                latest.append(candidate)
            else:
                history.append(candidate)
        state["known_ids"] = sorted(known_ids | set(current_ids))
        return latest, history, state

    async def _fetch_selected_docs(
        self,
        *,
        document_ids: list[str],
        cursor: Mapping[str, object],
        limit: int,
    ) -> list[tuple[RawChangeItem, Mapping[str, object]]]:
        doc_cursor = _resource_cursor(cursor, "docs")
        previous_items = _item_revision_map(doc_cursor)
        current_items = dict(previous_items)
        pending: list[tuple[RawChangeItem, Mapping[str, object]]] = []
        for document_id in document_ids:
            if len(pending) >= limit:
                break
            raw_value = await _run_lark_cli_json(
                _cli_args("docs", "+fetch", "--doc", document_id, "--doc-format", "markdown")
            )
            payload = _as_object(raw_value, name="document")
            previous = previous_items.get(document_id)
            data_map, content = _content_from_payload(payload)
            title = _title(data_map, f"Feishu doc {document_id}")
            item = _make_upsert(
                logical_id=f"feishu:doc:{document_id}",
                resource="docs",
                payload=payload,
                title=title,
                content=content,
                original_ref=_original_ref(data_map, f"feishu://doc/{document_id}"),
                revision_id=_revision(data_map, _markdown(content)),
                document_id=document_id,
            )
            current_items[document_id] = {"revision_id": item.revision_id, "title": title}
            if not isinstance(previous, Mapping) or previous.get("revision_id") != item.revision_id:
                pending.append((item, {"docs": {"items": deepcopy(current_items)}}))
        return pending

    async def _fetch_tasks(
        self,
        *,
        cursor: Mapping[str, object],
    ) -> list[tuple[RawChangeItem, Mapping[str, object]]]:
        values = await _paged_lark_cli(
            _cli_args("task", "+get-my-tasks"),
            name="tasks",
        )
        previous = _item_revision_map(_resource_cursor(cursor, "tasks"))
        current = dict(previous)
        pending: list[tuple[RawChangeItem, Mapping[str, object]]] = []
        for value in values:
            identifier = _stable_identifier(value, "guid", "task_id", "id", "url", fallback="task")
            content = (
                value.get("notes") or value.get("description") or value.get("content") or _title(value, identifier)
            )
            item = _make_upsert(
                logical_id=f"feishu:task:{identifier}",
                resource="tasks",
                payload=value,
                title=_title(value, f"Feishu task {identifier}"),
                content=content,
                original_ref=_original_ref(value, f"feishu://task/{identifier}"),
                revision_id=_revision(value, _markdown(content)),
                task_id=identifier,
            )
            current[identifier] = {"revision_id": item.revision_id, "title": item.title}
            previous_item = previous.get(identifier)
            if not isinstance(previous_item, Mapping) or previous_item.get("revision_id") != item.revision_id:
                pending.append((item, {"tasks": {"items": deepcopy(current)}}))
        return pending

    async def _fetch_calendar(
        self,
        *,
        source: Mapping[str, object],
        cursor: Mapping[str, object],
    ) -> list[tuple[RawChangeItem, Mapping[str, object]]]:
        args = _cli_args("calendar", "+agenda")
        if isinstance(source.get("start"), str):
            args.extend(["--start", str(source["start"])])
        if isinstance(source.get("end"), str):
            args.extend(["--end", str(source["end"])])
        payload = await _run_lark_cli_json(args)
        values = _as_items(payload, name="calendar")
        previous = _item_revision_map(_resource_cursor(cursor, "calendar"))
        current = dict(previous)
        pending: list[tuple[RawChangeItem, Mapping[str, object]]] = []
        for value in values:
            identifier = _stable_identifier(value, "event_id", "id", "uid", "url", fallback="event")
            start = value.get("start") or value.get("start_time")
            end = value.get("end") or value.get("end_time")
            content = value.get("description") or value.get("content") or _title(value, identifier)
            item = _make_upsert(
                logical_id=f"feishu:calendar:{identifier}",
                resource="calendar",
                payload=value,
                title=_title(value, f"Feishu event {identifier}"),
                content=content,
                original_ref=_original_ref(value, f"feishu://calendar/{identifier}"),
                revision_id=_revision(value, _markdown(content)),
                event_id=identifier,
                start=start,
                end=end,
            )
            current[identifier] = {"revision_id": item.revision_id, "title": item.title}
            previous_item = previous.get(identifier)
            if not isinstance(previous_item, Mapping) or previous_item.get("revision_id") != item.revision_id:
                pending.append(
                    (
                        item,
                        {
                            "calendar": {
                                "items": deepcopy(current),
                                "start": source.get("start"),
                                "end": source.get("end"),
                            }
                        },
                    )
                )
        return pending

    async def _fetch_wiki(
        self,
        *,
        source: Mapping[str, object],
        cursor: dict[str, object],
        max_items: int,
    ) -> list[FetchBatch]:
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
        current = {str(node["node_token"]): node for node in nodes}
        complete_scan = len(nodes) < max_nodes
        resource = _resource_cursor(cursor, "wiki_space")
        previous = resource.get("nodes")
        previous_nodes = dict(previous) if isinstance(previous, Mapping) else {}
        known_ids = {str(item) for item in resource.get("known_ids", []) if isinstance(item, str)}
        latest_nodes: list[tuple[str, Mapping[str, object] | None, Mapping[str, object] | None]] = []
        history_nodes: list[tuple[str, Mapping[str, object], Mapping[str, object] | None]] = []
        for node_token, node in current.items():
            previous_node = previous_nodes.get(node_token)
            if isinstance(previous_node, Mapping) and _revision(previous_node) == _revision(node):
                continue
            if isinstance(previous_node, Mapping) or node_token not in known_ids:
                latest_nodes.append((node_token, node, previous_node if isinstance(previous_node, Mapping) else None))
            else:
                history_nodes.append((node_token, node, previous_node if isinstance(previous_node, Mapping) else None))
        latest: list[tuple[RawChangeItem, str, object]] = []
        history: list[tuple[RawChangeItem, str, object]] = []
        if complete_scan:
            for node_token, old_node in previous_nodes.items():
                if node_token in current:
                    continue
                old_map = old_node if isinstance(old_node, Mapping) else {}
                latest_nodes.append((node_token, None, old_map))
        latest_nodes.sort(
            key=lambda candidate: (_wiki_change_sort_value(candidate[1] or candidate[2]), candidate[0]), reverse=True
        )
        history_nodes.sort(
            key=lambda candidate: (_wiki_change_sort_value(candidate[1] or candidate[2]), candidate[0]), reverse=True
        )
        selected_nodes = [*latest_nodes, *history_nodes][:max_items]
        for node_token, node, previous_node in selected_nodes:
            old_map = previous_node if isinstance(previous_node, Mapping) else {}
            if node is None:
                latest.append(
                    (
                        _make_delete(
                            logical_id=f"feishu:wiki:{space_id}:{node_token}",
                            resource="wiki",
                            title=_title(old_map, str(node_token)),
                            original_ref=f"feishu://wiki/{space_id}/{node_token}",
                            revision_id=_revision(old_map),
                            space_id=space_id,
                            node_token=node_token,
                        ),
                        node_token,
                        None,
                    )
                )
                continue
            payload, content = await _fetch_wiki_content(node, home=self._home)
            item = _make_upsert(
                logical_id=f"feishu:wiki:{space_id}:{node_token}",
                resource="wiki",
                payload=payload,
                title=_title(node, node_token),
                content=content,
                original_ref=f"feishu://wiki/{space_id}/{node_token}",
                revision_id=_revision(node, content),
                space_id=space_id,
                node_token=node_token,
                wiki_path=node.get("path"),
                obj_type=node.get("obj_type"),
            )
            (latest if isinstance(previous_node, Mapping) or node_token not in known_ids else history).append(
                (item, node_token, deepcopy(node))
            )
        latest.sort(key=lambda candidate: (_wiki_change_sort_value(candidate[2]), candidate[1]), reverse=True)
        history.sort(key=lambda candidate: (_wiki_change_sort_value(candidate[2]), candidate[1]), reverse=True)
        selected = [*latest, *history]
        running_nodes = deepcopy(previous_nodes)
        known = sorted(known_ids | set(current))
        changed: list[tuple[RawChangeItem, Mapping[str, object]]] = []
        for item, node_token, node in selected:
            if node is None:
                running_nodes.pop(node_token, None)
            else:
                running_nodes[node_token] = deepcopy(node)
            changed.append(
                (
                    item,
                    {"wiki_space": {"nodes": deepcopy(running_nodes), "known_ids": known}},
                )
            )
        return _batches(changed, cursor)
