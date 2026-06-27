# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Awaitable, Callable, ClassVar, Dict, List, Optional, Sequence, Tuple, TypeVar

import httpx

logger = logging.getLogger(__name__)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.result import (
    DownloadFileChunkData,
    DownloadFileData,
    DownloadFileResult,
    DownloadFileStreamResult,
    ExecuteCmdChunkData,
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
    ExecuteCodeChunkData,
    ExecuteCodeData,
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
    FileSystemData,
    FileSystemItem,
    ListDirsResult,
    ListFilesResult,
    ReadFileChunkData,
    ReadFileData,
    ReadFileResult,
    ReadFileStreamResult,
    SearchFilesData,
    SearchFilesResult,
    UploadFileChunkData,
    UploadFileData,
    UploadFileResult,
    UploadFileStreamResult,
    WriteFileData,
    WriteFileResult,
)
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import (
    BaseCodeProvider,
    BaseFSProvider,
    BaseShellProvider,
)
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry


def _invoke_lifecycle_hook(
    hook: Optional[Callable[[str, dict], None]],
    event_name: str,
    context: dict,
) -> None:
    """Invoke lifecycle hook in strict mode; passes a shallow copy of context."""
    if hook is None:
        return
    if not callable(hook):
        logger.warning(
            "[jiuwenbox] lifecycle_hook is not callable (got %s), skip event %s",
            type(hook).__name__, event_name,
        )
        return
    hook(event_name, dict(context))


def _build_fs_error_result(execution: str, error_msg: str, result_cls: Any, data: Any = None):
    return build_operation_error_result(
        error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
        msg_format_kwargs={"execution": execution, "error_msg": error_msg},
        result_cls=result_cls,
        data=data,
    )


def _build_shell_error_result(execution: str, error_msg: str, result_cls: Any, data: Any = None):
    return build_operation_error_result(
        error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
        msg_format_kwargs={"execution": execution, "error_msg": error_msg},
        result_cls=result_cls,
        data=data,
    )


def _build_code_error_result(execution: str, error_msg: str, result_cls: Any, data: Any = None):
    return build_operation_error_result(
        error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
        msg_format_kwargs={"execution": execution, "error_msg": error_msg},
        result_cls=result_cls,
        data=data,
    )


def _quote_shell_value(value: str) -> str:
    return shlex.quote(value)


def _normalize_exec_timeout(timeout: Optional[int]) -> Optional[int]:
    if timeout is None:
        return None
    normalized = int(timeout)
    return normalized if normalized > 0 else None


def _normalize_read_params(
    *,
    head: Optional[int],
    tail: Optional[int],
    line_range: Optional[Tuple[int, int]],
) -> Tuple[Optional[int], Optional[int], Optional[Tuple[int, int]]]:
    if head == 0:
        head = None
    if tail == 0:
        tail = None
    return head, tail, line_range


def _validate_read_params(
    *,
    mode: str,
    head: Optional[int],
    tail: Optional[int],
    line_range: Optional[Tuple[int, int]],
) -> Optional[str]:
    if mode == "bytes" and any(item is not None for item in (head, tail, line_range)):
        return "Parameters 'head', 'tail', and 'line_range' are only supported in text mode"
    specified = [
        name for name, value in [("head", head), ("tail", tail), ("line_range", line_range)]
        if value is not None
    ]
    if len(specified) > 1:
        return f"{' and '.join(specified)} cannot be specified simultaneously"
    return None


def _select_text_lines(
    content: str,
    *,
    head: Optional[int],
    tail: Optional[int],
    line_range: Optional[Tuple[int, int]],
) -> Tuple[List[str], bool]:
    lines = content.splitlines(keepends=True)
    if tail is not None:
        if tail < 0:
            return [], True
        return lines[-tail:] if tail > 0 else lines, False
    if head is not None:
        if head < 0:
            return [], True
        return lines[:head], False
    if line_range is not None:
        start, end = line_range
        if start <= 0 or end <= 0 or start > end:
            return [], True
        if not lines:
            return [], False
        start_idx = start - 1
        end_idx = min(len(lines), end)
        if start_idx >= len(lines) or end_idx <= start_idx:
            return [], False
        return lines[start_idx:end_idx], False
    return lines, False


def _sort_fs_items(items: List[FileSystemItem], sort_by: str, sort_descending: bool) -> List[FileSystemItem]:
    def key_fn(item: FileSystemItem) -> Any:
        if sort_by == "modified_time":
            return item.modified_time
        if sort_by == "size":
            return item.size
        return item.name

    return sorted(items, key=key_fn, reverse=sort_descending)


def _endpoint_value(endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig], attr: str) -> Any:
    value = getattr(endpoint, attr, None)
    if value is not None:
        return value
    launcher_config = getattr(config, "launcher_config", None) if config is not None else None
    return getattr(launcher_config, attr, None)


def _response_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()

    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            value = payload.get(key)
            if value:
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return json.dumps(payload, ensure_ascii=False)

    if payload:
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return response.text.strip()


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return

    detail = _response_error_detail(response)
    message = (
        f"HTTP {response.status_code} {response.reason_phrase}"
        f" for {response.request.method} {response.request.url}"
    )
    if detail:
        message = f"{message}: {detail}"
    raise httpx.HTTPStatusError(message, request=response.request, response=response)


# Match jiuwenbox "Sandbox '<id>' not found" 404 only; not file/dir 404s.
_SANDBOX_NOT_FOUND_RE = re.compile(r"^\s*Sandbox\b.*\bnot found\b", re.IGNORECASE)


def _is_sandbox_not_found_error(exc: BaseException) -> bool:
    """Return True if exc is a jiuwenbox sandbox-not-found 404."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    resp = exc.response
    if resp is None or resp.status_code != 404:
        return False
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text or ""
        return bool(_SANDBOX_NOT_FOUND_RE.match(text))
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and _SANDBOX_NOT_FOUND_RE.match(value):
                return True
    return False


# Match jiuwenbox process.py daemon IPC unavailable exec stderr (full message).
_DAEMON_IPC_UNAVAILABLE_RE = re.compile(
    r"^sandbox\s+(?P<q>['\"])(?P<id>.+?)(?P=q)\s+"
    r"daemon IPC channel unavailable;\s+"
    r"the daemon is not running or its control socket is gone\s*$",
)


def _parse_daemon_ipc_unavailable_sandbox_id(stderr: str) -> Optional[str]:
    """Return sandbox_id from stderr when the full daemon-unavailable message matches."""
    match = _DAEMON_IPC_UNAVAILABLE_RE.match((stderr or "").strip())
    return match.group("id") if match else None


def _is_daemon_ipc_unavailable_for_sandbox(
    result: dict[str, Any],
    *,
    sandbox_id: str,
) -> bool:
    parsed_id = _parse_daemon_ipc_unavailable_sandbox_id(result.get("stderr") or "")
    return parsed_id is not None and parsed_id == sandbox_id


def _is_sandbox_exec_delivered(
    result: dict[str, Any],
    *,
    sandbox_id: str,
) -> bool:
    """True when exec reached the sandbox (command outcome may still be non-zero)."""
    if int(result.get("exit_code") or 0) == 0:
        return True
    return not _is_daemon_ipc_unavailable_for_sandbox(result, sandbox_id=sandbox_id)


_ENV_API_TOKEN = "JIUWENBOX_API_TOKEN"


def _resolve_api_token(api_token: str | None) -> str | None:
    """Resolve Bearer token from explicit arg or ``JIUWENBOX_API_TOKEN`` env."""
    raw = api_token if api_token is not None else os.environ.get(_ENV_API_TOKEN)
    if raw is None:
        return None
    token = raw.strip()
    return token or None


def build_jiuwenbox_http_client(
    base_url: str,
    timeout_seconds: float = 30.0,
    api_token: str | None = None,
) -> httpx.Client:
    """Build an httpx client for the jiuwenbox HTTP API with optional Bearer auth."""
    token = _resolve_api_token(api_token)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        headers=headers,
    )


class _JiuwenBoxClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        api_token: str | None = None,
    ) -> None:
        self._client = build_jiuwenbox_http_client(
            base_url,
            timeout_seconds=timeout_seconds,
            api_token=api_token,
        )

    def create_sandbox(
        self,
        *,
        policy: dict[str, Any] | None = None,
        policy_mode: str | None = None,
    ) -> str:
        body: dict[str, Any] = {}
        if policy is not None:
            body["policy"] = policy
        if policy_mode is not None:
            body["policy_mode"] = policy_mode

        response = self._client.post("/api/v1/sandboxes", json=body)
        _raise_for_status(response)
        return response.json()["id"]

    def set_idle_timeout(
        self,
        *,
        idle_timeout: Optional[int] = None,
        idle_check_interval: Optional[int] = None,
    ) -> None:
        """PUT /api/v1/timeout with partial update semantics; no-op when both args are None."""
        body: dict[str, Any] = {}
        if idle_timeout is not None:
            body["idle_timeout"] = idle_timeout
        if idle_check_interval is not None:
            body["idle_check_interval"] = idle_check_interval
        if not body:
            return
        response = self._client.put("/api/v1/timeout", json=body)
        _raise_for_status(response)

    def delete_sandbox(self, sandbox_id: str) -> None:
        """DELETE /api/v1/sandboxes/{id}; treat 204 and 404 as success."""
        if not sandbox_id:
            return
        response = self._client.delete(f"/api/v1/sandboxes/{sandbox_id}")
        if response.status_code == 404:
            return
        _raise_for_status(response)

    def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        environment: Dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        timeout_seconds = _normalize_exec_timeout(timeout)
        body: dict[str, Any] = {
            "command": command,
            "workdir": cwd,
            "env": environment,
            "stdin": stdin,
            "timeout_seconds": timeout_seconds,
        }
        body = {key: value for key, value in body.items() if value is not None}
        response = self._client.post(
            f"/api/v1/sandboxes/{sandbox_id}/exec",
            json=body,
            timeout=max(timeout_seconds or 30, 30),
        )
        _raise_for_status(response)
        return dict(response.json())

    def upload_bytes(self, sandbox_id: str, sandbox_path: str, content: bytes) -> None:
        response = self._client.post(
            f"/api/v1/sandboxes/{sandbox_id}/upload",
            params={"sandbox_path": sandbox_path},
            files={"file": (Path(sandbox_path).name or "upload.bin", content)},
        )
        _raise_for_status(response)

    def append_bytes(self, sandbox_id: str, sandbox_path: str, content: bytes) -> None:
        encoded_content = base64.b64encode(content).decode("ascii")
        result = self.exec(
            sandbox_id,
            [
                "bash",
                "-lc",
                (
                    "set -euo pipefail; "
                    'target="$1"; '
                    'parent=$(dirname -- "$target"); '
                    'mkdir -p -- "$parent"; '
                    'base64 -d >> "$target"'
                ),
                "jiuwenbox-append",
                sandbox_path,
            ],
            stdin=encoded_content,
        )
        if int(result.get("exit_code") or 0) != 0:
            raise RuntimeError(result.get("stderr") or result.get("stdout") or "append file failed")

    def download_bytes(self, sandbox_id: str, sandbox_path: str) -> bytes:
        response = self._client.get(
            f"/api/v1/sandboxes/{sandbox_id}/download",
            params={"sandbox_path": sandbox_path},
        )
        _raise_for_status(response)
        return response.content

    def list_files(
        self,
        sandbox_id: str,
        path: str,
        *,
        recursive: bool,
        max_depth: Optional[int],
        include_files: bool,
        include_dirs: bool,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "sandbox_path": path,
            "recursive": recursive,
            "include_files": include_files,
            "include_dirs": include_dirs,
        }
        if max_depth is not None:
            params["max_depth"] = max_depth
        response = self._client.get(f"/api/v1/sandboxes/{sandbox_id}/files", params=params)
        _raise_for_status(response)
        return list(response.json().get("items", []))

    def search_files(
        self,
        sandbox_id: str,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]],
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("sandbox_path", path), ("pattern", pattern)]
        for item in exclude_patterns or []:
            params.append(("exclude_patterns", item))
        response = self._client.get(f"/api/v1/sandboxes/{sandbox_id}/search", params=params)
        _raise_for_status(response)
        return list(response.json().get("items", []))

    def path_exists(self, sandbox_id: str, sandbox_path: str) -> bool:
        path = PurePosixPath(sandbox_path)
        parent = path.parent.as_posix()
        try:
            items = self.list_files(
                sandbox_id,
                parent,
                recursive=False,
                max_depth=None,
                include_files=True,
                include_dirs=True,
            )
        except httpx.HTTPStatusError as exc:
            # Re-raise sandbox-not-found 404 for retry wrapper; other 404s mean path missing.
            if exc.response.status_code == 404 and not _is_sandbox_not_found_error(exc):
                return False
            raise
        return any(item.get("path") == sandbox_path for item in items)

    def close(self) -> None:
        """Close the underlying httpx client; swallows errors on shutdown."""
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            logger.warning("[jiuwenbox] close client failed")

    def __enter__(self) -> "_JiuwenBoxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ── Sandbox-lost auto-recreate settings ──
# JIUWENBOX_SANDBOX_RECREATE_RETRIES overrides default; 0 disables retries.
_DEFAULT_SANDBOX_RECREATE_RETRIES = 3
_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS = 1.0


def _resolve_recreate_retries() -> int:
    """Read JIUWENBOX_SANDBOX_RECREATE_RETRIES; invalid/missing falls back to default."""
    raw = os.environ.get("JIUWENBOX_SANDBOX_RECREATE_RETRIES")
    if raw is None or raw.strip() == "":
        return _DEFAULT_SANDBOX_RECREATE_RETRIES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[jiuwenbox] JIUWENBOX_SANDBOX_RECREATE_RETRIES=%r invalid, falling back to default %d",
            raw, _DEFAULT_SANDBOX_RECREATE_RETRIES,
        )
        return _DEFAULT_SANDBOX_RECREATE_RETRIES
    return max(value, 0)


_T = TypeVar("_T")


class _JiuwenBoxProviderMixin:
    _shared_lock = threading.Lock()
    _shared_sandbox_ids: Dict[str, str] = {}
    # Cache base_url -> lifecycle_hook captured at create time, so teardown
    # (delete_jiuwenbox_sandbox) can fire delete hooks without being passed them.
    _lifecycle_hooks: ClassVar[Dict[str, Callable[[str, dict], None]]] = {}
    # Lazy class-level asyncio.Lock serializes sandbox recreate under concurrent ops.
    _recreate_lock: ClassVar[Optional[asyncio.Lock]] = None
    _recreate_lock_init: ClassVar[threading.Lock] = threading.Lock()

    # Dedupe PUT /api/v1/timeout per base_url across provider instances.
    _idle_timeout_cache: ClassVar[Dict[str, Tuple[Optional[int], Optional[int]]]] = {}
    _idle_timeout_cache_lock: ClassVar[threading.Lock] = threading.Lock()

    _client: Optional[_JiuwenBoxClient]
    _sandbox_id: Optional[str]
    _timeout_seconds: int

    def _init_jiuwenbox(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig]) -> None:
        self._client = None
        self._sandbox_id = (
            _endpoint_value(endpoint, config, "sandbox_id")
            or os.environ.get("JIUWENBOX_SANDBOX_ID")
        )
        self._timeout_seconds = int(getattr(config, "timeout_seconds", 30) or 30)

    def _get_client(self) -> _JiuwenBoxClient:
        if self._client is None:
            base_url = _endpoint_value(self.endpoint, self.config, "base_url")
            if not base_url:
                raise ValueError("jiuwenbox provider requires endpoint.base_url")
            self._client = _JiuwenBoxClient(base_url=base_url, timeout_seconds=self._timeout_seconds)
        return self._client

    def _launcher_extra_params(self, *, create: bool = False) -> dict[str, Any]:
        launcher_config = getattr(self.config, "launcher_config", None) if self.config is not None else None
        if launcher_config is None:
            return {}

        extra_params = getattr(launcher_config, "extra_params", None)
        if isinstance(extra_params, dict):
            return extra_params

        if not create:
            return {}

        extra_params = {}
        setattr(launcher_config, "extra_params", extra_params)
        return extra_params

    def _sandbox_create_options_from_launcher_extra_params(self) -> dict[str, Any]:
        extra_params = self._launcher_extra_params()
        options: dict[str, Any] = {}

        policy = extra_params.get("policy")
        if isinstance(policy, dict):
            options["policy"] = policy
        elif hasattr(policy, "model_dump"):
            options["policy"] = policy.model_dump(mode="json")

        policy_mode = extra_params.get("policy_mode")
        if hasattr(policy_mode, "value"):
            policy_mode = policy_mode.value
        if isinstance(policy_mode, str) and policy_mode:
            options["policy_mode"] = policy_mode

        return options

    def _shared_scope_key(self) -> str:
        base_url = _endpoint_value(self.endpoint, self.config, "base_url")
        if not base_url:
            raise ValueError("jiuwenbox provider requires endpoint.base_url")
        # Use base_url as the cross-process sharing key. In practice, different
        # operation providers (fs/shell/code) may get rebuilt with different
        # isolation metadata, but they still need to target the same remote sandbox.
        key = str(base_url).rstrip("/")
        create_options = self._sandbox_create_options_from_launcher_extra_params()
        if not create_options:
            return key
        options_key = json.dumps(create_options, sort_keys=True, separators=(",", ":"))
        return f"{key}|{options_key}"

    def _sandbox_id_from_launcher_extra_params(self) -> Optional[str]:
        extra_params = self._launcher_extra_params()
        value = extra_params.get("sandbox_id")
        return value if isinstance(value, str) and value else None

    def _lifecycle_hook(self) -> Optional[Callable[[str, dict], None]]:
        """Return lifecycle_hook from launcher extra_params, or None."""
        hook = self._launcher_extra_params().get("lifecycle_hook")
        return hook if callable(hook) else None

    def _idle_timeout_from_launcher(self) -> Tuple[Optional[int], Optional[int]]:
        """Return (idle_timeout, idle_check_interval) from launcher config."""
        launcher_config = (
            getattr(self.config, "launcher_config", None)
            if self.config is not None
            else None
        )
        idle_timeout = getattr(launcher_config, "idle_ttl_seconds", None)
        extra_params = self._launcher_extra_params()
        raw_check = extra_params.get("idle_check_interval") if isinstance(extra_params, dict) else None
        idle_check_interval: Optional[int]
        if isinstance(raw_check, bool):
            # Reject bool; bool is a subclass of int.
            idle_check_interval = None
        elif isinstance(raw_check, int):
            idle_check_interval = raw_check
        elif isinstance(raw_check, float):
            idle_check_interval = int(raw_check)
        else:
            idle_check_interval = None
        return idle_timeout, idle_check_interval

    def _configure_server_idle_timeout(self) -> None:
        """Write idle reaper settings to jiuwenbox root policy; failures are logged only."""
        idle_timeout, idle_check_interval = self._idle_timeout_from_launcher()
        if idle_timeout is None and idle_check_interval is None:
            return
        base_url = _endpoint_value(self.endpoint, self.config, "base_url")
        cache_key = str(base_url).rstrip("/") if base_url else ""
        target = (idle_timeout, idle_check_interval)
        with self._idle_timeout_cache_lock:
            if self._idle_timeout_cache.get(cache_key) == target:
                return
        try:
            self._get_client().set_idle_timeout(
                idle_timeout=idle_timeout,
                idle_check_interval=idle_check_interval,
            )
            logger.info(
                "[jiuwenbox] PUT /api/v1/timeout: idle_timeout=%s "
                "idle_check_interval=%s",
                idle_timeout,
                idle_check_interval,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[jiuwenbox] PUT /api/v1/timeout failed (idle_timeout=%s, "
                "idle_check_interval=%s): %s",
                idle_timeout,
                idle_check_interval,
                exc,
            )
            return
        with self._idle_timeout_cache_lock:
            self._idle_timeout_cache[cache_key] = target

    @classmethod
    def register_shared_sandbox_id(cls, shared_key: str, sandbox_id: str) -> None:
        """Register sandbox_id in the cross-instance shared cache under shared_key."""
        with cls._shared_lock:
            cls._shared_sandbox_ids[shared_key] = sandbox_id

    @classmethod
    def register_lifecycle_hook(
        cls, base_url: str, hook: Optional[Callable[[str, dict], None]]
    ) -> None:
        """Cache the lifecycle hook for base_url so teardown can reuse it."""
        if hook is None:
            return
        with cls._shared_lock:
            cls._lifecycle_hooks[base_url.rstrip("/")] = hook

    @classmethod
    def pop_lifecycle_hook(cls, base_url: str) -> Optional[Callable[[str, dict], None]]:
        """Pop the cached lifecycle hook for base_url (None if absent)."""
        with cls._shared_lock:
            return cls._lifecycle_hooks.pop(base_url.rstrip("/"), None)

    @classmethod
    def cached_base_urls(cls) -> list[str]:
        """Return base_urls that currently hold cached sandbox IDs."""
        with cls._shared_lock:
            return list({key.split("|", 1)[0] for key in cls._shared_sandbox_ids})

    @classmethod
    def clear_shared_sandbox(cls, base_url: str) -> list[str]:
        """Remove all cached sandbox IDs for base_url."""
        shared_key = base_url.rstrip("/")
        removed: list[str] = []
        with cls._shared_lock:
            keys_to_delete = [
                key for key in cls._shared_sandbox_ids
                if key.startswith(shared_key)
            ]
            for key in keys_to_delete:
                value = cls._shared_sandbox_ids.pop(key, None)
                if isinstance(value, str) and value and value not in removed:
                    removed.append(value)
        return removed

    def _get_sandbox_id(self) -> str:
        env_sandbox_id = os.environ.get("JIUWENBOX_SANDBOX_ID")
        if env_sandbox_id and self._sandbox_id != env_sandbox_id:
            self._sandbox_id = env_sandbox_id
        # Re-read launcher.extra_params["sandbox_id"] each call (hot-update mutates extra_params).
        extra_sandbox_id = self._sandbox_id_from_launcher_extra_params()
        if extra_sandbox_id and extra_sandbox_id != self._sandbox_id:
            self._sandbox_id = extra_sandbox_id
        if self._sandbox_id is None:
            endpoint_sandbox_id = getattr(self.endpoint, "sandbox_id", None)
            if isinstance(endpoint_sandbox_id, str) and endpoint_sandbox_id:
                self._sandbox_id = endpoint_sandbox_id
        if self._sandbox_id is None:
            lifecycle_hook = self._lifecycle_hook()
            shared_key = self._shared_scope_key()
            self.register_lifecycle_hook(
                _endpoint_value(self.endpoint, self.config, "base_url"), lifecycle_hook
            )
            with self._shared_lock:
                self._sandbox_id = self._shared_sandbox_ids.get(shared_key)
                newly_created = False
                if self._sandbox_id is None:
                    # before_create under _shared_lock before first lazy create.
                    _invoke_lifecycle_hook(
                        lifecycle_hook, "before_create", {"reason": "initial"},
                    )
                    # PUT root idle policy before create (reaper ignores per-sandbox timeout).
                    self._configure_server_idle_timeout()
                    self._sandbox_id = self._get_client().create_sandbox(
                        **self._sandbox_create_options_from_launcher_extra_params(),
                    )
                    newly_created = True
                self._shared_sandbox_ids[shared_key] = self._sandbox_id
                self._launcher_extra_params(create=True)["sandbox_id"] = self._sandbox_id
            # Sync upload preserve_files only when this process just created the sandbox.
            if newly_created:
                _try_upload_preserve_files(
                    self._get_client(),
                    self._sandbox_id,
                    self._launcher_extra_params().get("preserve_files_upload"),
                )
                # after_create after preserve_files upload completes.
                _invoke_lifecycle_hook(
                    lifecycle_hook,
                    "after_create",
                    {"reason": "initial", "sandbox_id": self._sandbox_id},
                )
        else:
            shared_key = self._shared_scope_key()
            with self._shared_lock:
                self._shared_sandbox_ids[shared_key] = self._sandbox_id
                self._launcher_extra_params(create=True)["sandbox_id"] = self._sandbox_id
        return self._sandbox_id

    @classmethod
    def _get_recreate_lock(cls) -> asyncio.Lock:
        """Return lazy-init class-level asyncio.Lock guarded by threading.Lock."""
        if cls._recreate_lock is None:
            with cls._recreate_lock_init:
                if cls._recreate_lock is None:
                    cls._recreate_lock = asyncio.Lock()
        return cls._recreate_lock

    async def _execute_with_sandbox_retry(self, op: Callable[[str], _T]) -> _T:
        """Run op with auto sandbox recreate on sandbox-not-found 404."""
        max_retries = _resolve_recreate_retries()
        last_exc: Optional[httpx.HTTPStatusError] = None
        stale_sandbox_id = self._get_sandbox_id()
        for attempt in range(max_retries + 1):
            if attempt == 0:
                sandbox_id = stale_sandbox_id
            else:
                await asyncio.sleep(_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS)
                logger.info(
                    "[jiuwenbox] sandbox-not-found auto-recreate attempt %d/%d (stale=%s)",
                    attempt, max_retries, stale_sandbox_id,
                )
                try:
                    sandbox_id = await self._recreate_sandbox_after_loss(
                        stale_sandbox_id=stale_sandbox_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Recreate failed; retry after sleep.
                    logger.warning(
                        "[jiuwenbox] sandbox recreate failed (attempt %d/%d): %s",
                        attempt, max_retries, exc,
                    )
                    continue
            try:
                return await asyncio.to_thread(op, sandbox_id)
            except httpx.HTTPStatusError as exc:
                if not _is_sandbox_not_found_error(exc):
                    raise
                last_exc = exc
                # Track current id as stale for the next force_recreate.
                stale_sandbox_id = sandbox_id
                logger.warning(
                    "[jiuwenbox] sandbox %s not found (attempt %d/%d)",
                    sandbox_id, attempt, max_retries,
                )
        raise last_exc

    async def _recreate_sandbox_after_loss(self, *, stale_sandbox_id: str) -> str:
        """Recreate sandbox under lock; double-check launcher/cache before creating."""
        base_url = _endpoint_value(self.endpoint, self.config, "base_url")
        if not base_url:
            raise ValueError("jiuwenbox provider requires endpoint.base_url")
        create_options = self._sandbox_create_options_from_launcher_extra_params()
        extra_params = self._launcher_extra_params()
        preserve_files_upload = (
            extra_params.get("preserve_files_upload") if isinstance(extra_params, dict) else None
        )

        async with self._get_recreate_lock():
            # Double-check launcher.extra_params
            current = self._sandbox_id_from_launcher_extra_params()
            shared_key = self._shared_scope_key()
            # Double-check shared cache
            with self._shared_lock:
                cached = self._shared_sandbox_ids.get(shared_key)
            for candidate in (current, cached):
                if candidate and candidate != stale_sandbox_id:
                    self._sandbox_id = candidate
                    return candidate

            new_id = await force_recreate_jiuwenbox_sandbox(
                base_url,
                **create_options,
                timeout_seconds=float(self._timeout_seconds),
                preserve_files_upload=preserve_files_upload,
                extra_stale_sandbox_ids=[stale_sandbox_id],
                lifecycle_hook=self._lifecycle_hook(),
                reason="sandbox_lost",
            )
            # Sync local _sandbox_id after force_recreate.
            self._sandbox_id = new_id
            self._launcher_extra_params(create=True)["sandbox_id"] = new_id
            return new_id

    async def _run_exec_pipeline(
        self,
        *,
        sandbox_op: Callable[[str], dict[str, Any]],
        local_op: Callable[[], Awaitable[dict[str, Any]]],
        fallback_on_failure: bool,
    ) -> Tuple[dict[str, Any], Optional[str]]:
        """Run sandbox exec through the a→b→c→d pipeline.

        Returns ``(result_dict, error_message)``. ``error_message`` is set only
        when the pipeline failed and local fallback was not used.
        """
        max_retries = _resolve_recreate_retries()
        stale_sandbox_id = self._get_sandbox_id()
        last_error: Optional[str] = None

        for attempt in range(max_retries + 1):
            if attempt == 0:
                sandbox_id = stale_sandbox_id
            else:
                await asyncio.sleep(_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS)
                logger.info(
                    "[jiuwenbox] sandbox-not-found auto-recreate attempt %d/%d (stale=%s)",
                    attempt,
                    max_retries,
                    stale_sandbox_id,
                )
                try:
                    sandbox_id = await self._recreate_sandbox_after_loss(
                        stale_sandbox_id=stale_sandbox_id,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        "[jiuwenbox] sandbox recreate failed (attempt %d/%d): %s",
                        attempt,
                        max_retries,
                        exc,
                    )
                    continue

            try:
                result = await asyncio.to_thread(sandbox_op, sandbox_id)
                if _is_sandbox_exec_delivered(result, sandbox_id=sandbox_id):
                    return result, None
                last_error = str(result.get("stderr") or "sandbox exec not delivered")
                logger.warning(
                    "[jiuwenbox] sandbox %s daemon IPC unavailable (attempt %d/%d)",
                    sandbox_id,
                    attempt,
                    max_retries,
                )
                break
            except httpx.HTTPStatusError as exc:
                if _is_sandbox_not_found_error(exc):
                    last_error = str(exc)
                    stale_sandbox_id = sandbox_id
                    logger.warning(
                        "[jiuwenbox] sandbox %s not found (attempt %d/%d)",
                        sandbox_id,
                        attempt,
                        max_retries,
                    )
                    continue
                last_error = str(exc)
                break
            except Exception as exc:
                last_error = str(exc)
                break

        if fallback_on_failure:
            logger.info(
                "[jiuwenbox] sandbox pipeline failed (%s), falling back to local exec",
                last_error,
            )
            local_result = await local_op()
            return local_result, None
        return {}, last_error or "sandbox exec failed"


def clear_jiuwenbox_shared_sandbox(base_url: str) -> list[str]:
    """Clear in-process sandbox cache for base_url.

    Returns:
        Removed sandbox_id list (deduplicated).
    """
    return _JiuwenBoxProviderMixin.clear_shared_sandbox(base_url)


def _iter_host_files_for_upload(
    upload_entries: Any,
) -> List[Tuple[str, str]]:
    """Expand preserve_files_upload into (host_path, sandbox_path) pairs."""
    pairs: list[tuple[str, str]] = []
    if not isinstance(upload_entries, list):
        return pairs

    for entry in upload_entries:
        if not isinstance(entry, dict):
            continue
        host_path = str(entry.get("host_path") or "").strip()
        sandbox_path = str(entry.get("sandbox_path") or "").strip()
        kind = str(entry.get("kind") or "").strip().lower()
        if not host_path or not sandbox_path:
            continue
        host_root = Path(host_path)
        if kind == "directory" or host_root.is_dir():
            if not host_root.is_dir():
                continue
            sandbox_root = PurePosixPath(sandbox_path)
            for sub in host_root.rglob("*"):
                try:
                    if not sub.is_file():
                        continue
                except OSError:
                    continue
                try:
                    rel = sub.relative_to(host_root)
                except ValueError:
                    continue
                sub_sandbox_path = str(sandbox_root.joinpath(rel.as_posix()))
                pairs.append((str(sub), sub_sandbox_path))
        else:
            if host_root.is_file():
                pairs.append((str(host_root), sandbox_path))
    return pairs


async def _upload_preserve_files(
    client: "_JiuwenBoxClient",
    sandbox_id: str,
    upload_entries: Any,
) -> int:
    """Upload preserve_files entries to sandbox; return count of successful uploads."""
    pairs = _iter_host_files_for_upload(upload_entries)
    if not pairs:
        return 0

    uploaded = 0
    for host_path, sandbox_path in pairs:
        try:
            content = await asyncio.to_thread(Path(host_path).read_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[jiuwenbox] preserve_files read host file failed (%s): %s",
                host_path,
                exc,
            )
            continue
        try:
            await asyncio.to_thread(
                client.upload_bytes, sandbox_id, sandbox_path, content
            )
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[jiuwenbox] preserve_files upload failed (%s -> %s): %s",
                host_path,
                sandbox_path,
                exc,
            )
    logger.info(
        "[jiuwenbox] preserve_files uploaded %d/%d files to sandbox=%s",
        uploaded,
        len(pairs),
        sandbox_id,
    )
    return uploaded


def _upload_preserve_files_sync(
    client: "_JiuwenBoxClient",
    sandbox_id: str,
    upload_entries: Any,
) -> int:
    """Sync variant of _upload_preserve_files."""
    pairs = _iter_host_files_for_upload(upload_entries)
    if not pairs:
        return 0
    uploaded = 0
    for host_path, sandbox_path in pairs:
        try:
            content = Path(host_path).read_bytes()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[jiuwenbox] preserve_files read host file failed (%s): %s",
                host_path,
                exc,
            )
            continue
        try:
            client.upload_bytes(sandbox_id, sandbox_path, content)
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[jiuwenbox] preserve_files upload failed (%s -> %s): %s",
                host_path,
                sandbox_path,
                exc,
            )
    logger.info(
        "[jiuwenbox] preserve_files uploaded %d/%d files to sandbox=%s",
        uploaded,
        len(pairs),
        sandbox_id,
    )
    return uploaded


def _try_upload_preserve_files(
    client: "_JiuwenBoxClient",
    sandbox_id: str,
    upload_entries: Any,
) -> int:
    """Upload preserve_files via sync or fire-and-forget async depending on event loop."""
    if not upload_entries:
        return 0
    try:
        # Check if current thread has a running event loop
        asyncio.get_running_loop()
        running_in_loop = True
    except RuntimeError:
        running_in_loop = False
    if running_in_loop:
        # Fire-and-forget async upload when already in a loop (e.g. async tests)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _upload_preserve_files(client, sandbox_id, upload_entries)
            )
            return -1
        except Exception:
            logger.warning("[jiuwenbox] preserve_files upload failed")
    return _upload_preserve_files_sync(client, sandbox_id, upload_entries)


async def force_recreate_jiuwenbox_sandbox(
    base_url: str,
    *,
    policy: dict | None = None,
    policy_mode: str | None = None,
    timeout_seconds: float = 30.0,
    preserve_files_upload: Any = None,
    extra_stale_sandbox_ids: Sequence[str] | None = None,
    lifecycle_hook: Optional[Callable[[str, dict], None]] = None,
    reason: str = "sandbox_lost",
) -> str:
    """Clear cache and create a new remote sandbox for base_url.

    Used for policy hot-update (/sandbox files allow|deny) and sandbox-lost retry.

    Args:
        base_url: jiuwenbox service base URL.
        policy / policy_mode: Security policy for the new sandbox.
        timeout_seconds: HTTP client timeout.
        preserve_files_upload: Files/dirs to re-upload in copy mode.
        extra_stale_sandbox_ids: Additional stale IDs to delete after create.
        lifecycle_hook: Strict lifecycle hook for before/after_recreate events.
        reason: Recreate reason in hook context ("sandbox_lost" or "policy_changed").

    Returns:
        New sandbox_id to write back to launcher_config.extra_params["sandbox_id"].
    """
    # Pop stale IDs from shared cache before create; server-side cleanup follows.
    stale_sandbox_ids = clear_jiuwenbox_shared_sandbox(base_url)
    if extra_stale_sandbox_ids:
        seen = set(stale_sandbox_ids)
        for extra in extra_stale_sandbox_ids:
            if isinstance(extra, str) and extra and extra not in seen:
                stale_sandbox_ids.append(extra)
                seen.add(extra)

    # Register new id in shared cache before deleting stale sandboxes.
    shared_key = base_url.rstrip("/")
    create_options: dict[str, Any] = {}
    if policy is not None:
        create_options["policy"] = policy
    if policy_mode is not None:
        create_options["policy_mode"] = policy_mode
    if create_options:
        options_key = json.dumps(create_options, sort_keys=True, separators=(",", ":"))
        shared_key = f"{shared_key}|{options_key}"

    for old_id in stale_sandbox_ids:
        _invoke_lifecycle_hook(
            lifecycle_hook,
            "before_recreate",
            {"reason": reason, "old_sandbox_id": old_id},
        )

    with _JiuwenBoxClient(base_url=base_url, timeout_seconds=timeout_seconds) as client:
        sandbox_id = await asyncio.to_thread(
            client.create_sandbox, policy=policy, policy_mode=policy_mode,
        )
        if preserve_files_upload:
            await _upload_preserve_files(client, sandbox_id, preserve_files_upload)

        _JiuwenBoxProviderMixin.register_shared_sandbox_id(shared_key, sandbox_id)
        _JiuwenBoxProviderMixin.register_lifecycle_hook(base_url, lifecycle_hook)
        for old_id in stale_sandbox_ids:
            _invoke_lifecycle_hook(
                lifecycle_hook,
                "after_recreate",
                {"reason": reason, "sandbox_id": sandbox_id, "old_sandbox_id": old_id},
            )

        # Create before delete so a failed create leaves the stale sandbox in place.
        for old_id in stale_sandbox_ids:
            if old_id == sandbox_id:
                # Skip delete if stale id equals the new sandbox id.
                continue
            try:
                await asyncio.to_thread(client.delete_sandbox, old_id)
                logger.info(
                    "[jiuwenbox] force_recreate_jiuwenbox_sandbox: "
                    "deleted stale sandbox %s", old_id,
                )
            except Exception as exc:
                logger.warning(
                    "[jiuwenbox] force_recreate_jiuwenbox_sandbox: "
                    "stale sandbox %s cleanup failed: %s", old_id, exc,
                )

    logger.info(
        "[jiuwenbox] force_recreate_jiuwenbox_sandbox: new sandbox_id=%s for %s",
        sandbox_id,
        base_url,
    )
    return sandbox_id


async def delete_jiuwenbox_sandbox(
    *,
    reason: str = "teardown",
    timeout_seconds: float = 30.0,
) -> list[str]:
    """Delete all cached remote jiuwenbox sandboxes on sysoperation teardown.

    base_url and lifecycle_hook are taken from the process-wide cache populated
    when sandboxes were created (``register_lifecycle_hook`` /
    ``register_shared_sandbox_id``), so callers only pass the teardown reason.
    Each cached base_url's sandboxes are deleted remotely and fire ``before_delete``
    then ``after_delete`` (strict hooks; ctx ``{"reason", "sandbox_id"}``).

    Args:
        reason: Passed to delete hook context; default "teardown".
        timeout_seconds: HTTP client timeout.

    Returns:
        sandbox_ids successfully deleted, in order.
    """
    deleted: list[str] = []
    for base_url in _JiuwenBoxProviderMixin.cached_base_urls():
        lifecycle_hook = _JiuwenBoxProviderMixin.pop_lifecycle_hook(base_url)
        sandbox_ids = clear_jiuwenbox_shared_sandbox(base_url)
        if not sandbox_ids:
            continue
        with _JiuwenBoxClient(base_url=base_url, timeout_seconds=timeout_seconds) as client:
            for sandbox_id in sandbox_ids:
                if not sandbox_id:
                    continue
                _invoke_lifecycle_hook(
                    lifecycle_hook,
                    "before_delete",
                    {"reason": reason, "sandbox_id": sandbox_id},
                )
                try:
                    await asyncio.to_thread(client.delete_sandbox, sandbox_id)
                except Exception as exc:
                    logger.warning(
                        "[jiuwenbox] delete_jiuwenbox_sandbox: "
                        "sandbox %s cleanup failed: %s", sandbox_id, exc,
                    )
                    continue
                deleted.append(sandbox_id)
                logger.info(
                    "[jiuwenbox] delete_jiuwenbox_sandbox: deleted sandbox %s", sandbox_id,
                )
                _invoke_lifecycle_hook(
                    lifecycle_hook,
                    "after_delete",
                    {"reason": reason, "sandbox_id": sandbox_id},
                )
    return deleted


def _decode_subprocess_stream(value: Any) -> str:
    """Normalize subprocess stdout/stderr bytes or None to str."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value or ""


async def _run_local_subprocess(
    argv: list[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
) -> dict[str, Any]:
    """Run argv locally; return dict matching jiuwenbox exec shape."""
    def _run() -> dict[str, Any]:
        merged_env = None
        if env is not None:
            merged_env = dict(os.environ)
            merged_env.update({str(k): str(v) for k, v in env.items()})
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=merged_env,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout if (timeout and timeout > 0) else None,
                check=False,
            )
            return {
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "exit_code": int(completed.returncode),
                "local": True,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "stdout": _decode_subprocess_stream(exc.stdout),
                "stderr": _decode_subprocess_stream(exc.stderr)
                + f"\n[local timeout after {timeout}s]",
                "exit_code": 124,
                "local": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "stdout": "",
                "stderr": f"local subprocess error: {exc}",
                "exit_code": 1,
                "local": True,
            }

    return await asyncio.to_thread(_run)


def _read_excluded_commands(extra: Any) -> list[str] | None:
    """Read excluded_commands from launcher extra_params; None if unset."""
    if not isinstance(extra, dict):
        return None
    raw = extra.get("excluded_commands")
    if isinstance(raw, list):
        return raw
    return None


def _command_matches_exclude(command: str, patterns: list[str] | None) -> bool:
    """Return True if command matches any fnmatch exclude pattern."""
    if not command or not patterns:
        return False
    stripped = command.strip()
    if not stripped:
        return False
    try:
        first_token = shlex.split(stripped, posix=True)[0]
    except ValueError:
        first_token = stripped.split()[0] if stripped.split() else stripped
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            continue
        if fnmatch.fnmatchcase(stripped, pattern):
            return True
        if fnmatch.fnmatchcase(first_token, pattern):
            return True
    return False


def _item_from_payload(item: dict[str, Any]) -> FileSystemItem:
    return FileSystemItem(
        name=item.get("name", ""),
        path=item.get("path", ""),
        size=item.get("size") or 0,
        is_directory=bool(item.get("is_directory", False)),
        modified_time=item.get("modified_time") or "0",
        type=item.get("type"),
    )


@SandboxRegistry.provider("jiuwenbox", "fs")
class JiuwenBoxFSProvider(_JiuwenBoxProviderMixin, BaseFSProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_jiuwenbox(endpoint, config)

    async def read_file(self, path: str, mode: str = "text", **kwargs) -> ReadFileResult:
        tail = kwargs.pop("tail", None)
        head = kwargs.pop("head", None)
        line_range = kwargs.pop("line_range", None)
        head, tail, line_range = _normalize_read_params(head=head, tail=tail, line_range=line_range)
        validation_error = _validate_read_params(mode=mode, head=head, tail=tail, line_range=line_range)
        if validation_error:
            return _build_fs_error_result("read_file", validation_error, ReadFileResult)
        try:
            raw = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().download_bytes(sid, path)
            )
            if mode == "bytes":
                content: str | bytes = raw
            else:
                text = raw.decode(kwargs.get("encoding", "utf-8"))
                lines, _ = _select_text_lines(text, head=head, tail=tail, line_range=line_range)
                content = "".join(lines)
            return ReadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileData(path=path, content=content, mode=mode or "text"),
            )
        except Exception as exc:
            return _build_fs_error_result("read_file", str(exc), ReadFileResult)

    async def write_file(self, path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult:
        append = bool(kwargs.get("append", False))
        prepend_newline = kwargs.get("prepend_newline", True)
        append_newline = kwargs.get("append_newline", False)
        try:
            if mode == "bytes":
                raw = content if isinstance(content, bytes) else bytes(content)
            else:
                text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
                if prepend_newline:
                    text = "\n" + text
                if append_newline:
                    text += "\n"
                raw = text.encode("utf-8")
            if append:
                await self._execute_with_sandbox_retry(
                    lambda sid: self._get_client().append_bytes(sid, path, raw)
                )
            else:
                await self._execute_with_sandbox_retry(
                    lambda sid: self._get_client().upload_bytes(sid, path, raw)
                )
            return WriteFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=WriteFileData(path=path, size=len(raw), mode=mode or "text"),
            )
        except Exception as exc:
            return _build_fs_error_result("write_file", str(exc), WriteFileResult)

    async def list_files(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_depth: Optional[int] = None,
        sort_by: str = "name",
        sort_descending: bool = False,
        file_types: Optional[List[str]] = None,
        **kwargs,
    ) -> ListFilesResult:
        try:
            raw_items = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().list_files(
                    sid,
                    path,
                    recursive=recursive,
                    max_depth=max_depth,
                    include_files=True,
                    include_dirs=False,
                )
            )
            items = [_item_from_payload(item) for item in raw_items]
            if file_types:
                items = [item for item in items if item.type in file_types]
            items = _sort_fs_items(items, sort_by, sort_descending)
            return ListFilesResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=FileSystemData(
                    total_count=len(items),
                    list_items=items,
                    root_path=path,
                    recursive=recursive,
                    max_depth=max_depth,
                ),
            )
        except Exception as exc:
            return _build_fs_error_result("list_files", str(exc), ListFilesResult)

    async def list_directories(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_depth: Optional[int] = None,
        sort_by: str = "name",
        sort_descending: bool = False,
        **kwargs,
    ) -> ListDirsResult:
        try:
            raw_items = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().list_files(
                    sid,
                    path,
                    recursive=recursive,
                    max_depth=max_depth,
                    include_files=False,
                    include_dirs=True,
                )
            )
            items = _sort_fs_items([_item_from_payload(item) for item in raw_items], sort_by, sort_descending)
            return ListDirsResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=FileSystemData(
                    total_count=len(items),
                    list_items=items,
                    root_path=path,
                    recursive=recursive,
                    max_depth=max_depth,
                ),
            )
        except Exception as exc:
            return _build_fs_error_result("list_directories", str(exc), ListDirsResult)

    async def read_file_stream(
        self,
        path: str,
        *,
        mode: str = "text",
        head: Optional[int] = None,
        tail: Optional[int] = None,
        line_range: Optional[Tuple[int, int]] = None,
        encoding: str = "utf-8",
        chunk_size: int = 8192,
        **kwargs,
    ) -> AsyncIterator[ReadFileStreamResult]:
        head, tail, line_range = _normalize_read_params(head=head, tail=tail, line_range=line_range)
        validation_error = _validate_read_params(mode=mode, head=head, tail=tail, line_range=line_range)
        if validation_error:
            yield _build_fs_error_result("read_file_stream", validation_error, ReadFileStreamResult)
            return

        result = await self.read_file(path, mode=mode, head=head,
                                      tail=tail, line_range=line_range, encoding=encoding)
        if result.code != StatusCode.SUCCESS.code:
            yield ReadFileStreamResult(code=result.code, message=result.message, data=None)
            return
        content = result.data.content
        if mode == "bytes":
            raw = content if isinstance(content, bytes) else str(content).encode(encoding)
            if chunk_size <= 0:
                chunk_size = 8192
            if not raw:
                return
            pieces = [raw[start:start + chunk_size] for start in range(0, len(raw), max(chunk_size, 1))]
            for index, piece in enumerate(pieces):
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=path,
                        chunk_content=piece,
                        mode="bytes",
                        chunk_size=len(piece),
                        chunk_index=index,
                        is_last_chunk=index == len(pieces) - 1,
                    ),
                )
            return

        text = content if isinstance(content, str) else content.decode(encoding)
        selected_lines = text.splitlines(keepends=True)
        emit_empty_chunk = False
        if head is not None and head < 0:
            emit_empty_chunk = True
        if tail is not None and tail < 0:
            emit_empty_chunk = True
        if line_range is not None:
            start, end = line_range
            if start <= 0 or end <= 0 or start > end:
                emit_empty_chunk = True
        if not selected_lines:
            if emit_empty_chunk:
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=path,
                        chunk_content="",
                        mode="text",
                        chunk_size=0,
                        chunk_index=0,
                        is_last_chunk=True,
                    ),
                )
            return

        for index, line in enumerate(selected_lines):
            yield ReadFileStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileChunkData(
                    path=path,
                    chunk_content=line,
                    mode="text",
                    chunk_size=len(line.encode(encoding)),
                    chunk_index=index,
                    is_last_chunk=index == len(selected_lines) - 1,
                ),
            )

    async def upload_file(
        self,
        local_path: str,
        target_path: str,
        *,
        overwrite: bool = False,
        create_parent_dirs: bool = True,
        preserve_permissions: bool = True,
        chunk_size: int = 0,
        **kwargs,
    ) -> UploadFileResult:
        try:
            if not overwrite:
                exists = await self._execute_with_sandbox_retry(
                    lambda sid: self._get_client().path_exists(sid, target_path)
                )
                if exists:
                    raise FileExistsError(f"File already exists: {target_path}")
            raw = Path(local_path).read_bytes()
            await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().upload_bytes(sid, target_path, raw)
            )
            return UploadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=UploadFileData(local_path=local_path, target_path=target_path, size=len(raw)),
            )
        except Exception as exc:
            return _build_fs_error_result("upload_file", str(exc), UploadFileResult)

    async def upload_file_stream(
        self,
        local_path: str,
        target_path: str,
        *,
        overwrite: bool = False,
        chunk_size: int = 1048576,
        **kwargs,
    ) -> AsyncIterator[UploadFileStreamResult]:
        result = await self.upload_file(local_path, target_path, overwrite=overwrite)
        if result.code != StatusCode.SUCCESS.code:
            yield UploadFileStreamResult(code=result.code, message=result.message, data=None)
            return
        size = os.path.getsize(local_path)
        yield UploadFileStreamResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=UploadFileChunkData(
                local_path=local_path,
                target_path=target_path,
                chunk_size=size,
                chunk_index=0,
                is_last_chunk=True,
            ),
        )

    async def download_file(
        self,
        source_path: str,
        local_path: str,
        *,
        overwrite: bool = False,
        create_parent_dirs: bool = True,
        preserve_permissions: bool = True,
        chunk_size: int = 0,
        **kwargs,
    ) -> DownloadFileResult:
        try:
            target = Path(local_path)
            if create_parent_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not overwrite:
                raise FileExistsError(f"File already exists: {local_path}")
            raw = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().download_bytes(sid, source_path)
            )
            target.write_bytes(raw)
            return DownloadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=DownloadFileData(source_path=source_path, local_path=local_path, size=len(raw)),
            )
        except Exception as exc:
            return _build_fs_error_result("download_file", str(exc), DownloadFileResult)

    async def download_file_stream(
        self,
        source_path: str,
        local_path: str,
        *,
        overwrite: bool = False,
        chunk_size: int = 1048576,
        **kwargs,
    ) -> AsyncIterator[DownloadFileStreamResult]:
        result = await self.download_file(source_path, local_path, overwrite=overwrite)
        if result.code != StatusCode.SUCCESS.code:
            yield DownloadFileStreamResult(code=result.code, message=result.message, data=None)
            return
        size = os.path.getsize(local_path)
        yield DownloadFileStreamResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=DownloadFileChunkData(
                source_path=source_path,
                local_path=local_path,
                chunk_size=size,
                chunk_index=0,
                is_last_chunk=True,
            ),
        )

    async def search_files(
        self,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> SearchFilesResult:
        try:
            raw_items = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().search_files(
                    sid,
                    path,
                    pattern,
                    exclude_patterns,
                )
            )
            items = _sort_fs_items([_item_from_payload(item) for item in raw_items], "name", False)
            return SearchFilesResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=SearchFilesData(
                    total_matches=len(items),
                    matching_files=items,
                    search_path=path,
                    search_pattern=pattern,
                    exclude_patterns=exclude_patterns,
                ),
            )
        except Exception as exc:
            return _build_fs_error_result("search_files", str(exc), SearchFilesResult)


@SandboxRegistry.provider("jiuwenbox", "shell")
class JiuwenBoxShellProvider(_JiuwenBoxProviderMixin, BaseShellProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_jiuwenbox(endpoint, config)

    async def execute_cmd(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> ExecuteCmdResult:
        if not command or not command.strip():
            return _build_shell_error_result("execute_cmd", "command can not be empty", ExecuteCmdResult)
        exec_timeout = _normalize_exec_timeout(timeout)
        workdir = None if not cwd or cwd == "." else cwd

        extra = self._launcher_extra_params()
        exclude_patterns = _read_excluded_commands(extra)
        fallback_on_failure = bool(extra.get("fallback_on_failure", False)) if isinstance(extra, dict) else False

        # (a) Pre-route excluded commands to local execution
        if _command_matches_exclude(command, exclude_patterns):
            logger.info(
                "[jiuwenbox] shell pre-routed to local (exclude pattern hit): %s",
                command,
            )
            local_result = await _run_local_subprocess(
                ["bash", "-lc", command],
                cwd=workdir,
                env=environment,
                timeout=exec_timeout,
            )
            return self._wrap_shell_local_result(command, cwd, timeout, local_result)

        result, pipeline_error = await self._run_exec_pipeline(
            sandbox_op=lambda sid: self._get_client().exec(
                sid,
                ["bash", "-lc", command],
                cwd=workdir,
                timeout=exec_timeout,
                environment=environment,
            ),
            local_op=lambda: _run_local_subprocess(
                ["bash", "-lc", command],
                cwd=workdir,
                env=environment,
                timeout=exec_timeout,
            ),
            fallback_on_failure=fallback_on_failure,
        )
        if pipeline_error:
            return _build_shell_error_result("execute_cmd", pipeline_error, ExecuteCmdResult)
        if result.get("local"):
            return self._wrap_shell_local_result(command, cwd, timeout, result)

        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        exit_code = int(result.get("exit_code") or 0)
        data = ExecuteCmdData(
            command=command,
            cwd=cwd or ".",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
        if exit_code == 124:
            return _build_shell_error_result(
                "execute_cmd",
                f"execution timeout after {timeout} seconds",
                ExecuteCmdResult,
                data=data,
            )
        return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

    @staticmethod
    def _wrap_shell_local_result(
        command: str,
        cwd: Optional[str],
        timeout: Optional[int],
        local_result: dict[str, Any],
    ) -> ExecuteCmdResult:
        data = ExecuteCmdData(
            command=command,
            cwd=cwd or ".",
            stdout=local_result.get("stdout") or "",
            stderr=local_result.get("stderr") or "",
            exit_code=int(local_result.get("exit_code") or 0),
        )
        if int(local_result.get("exit_code") or 0) == 124:
            return _build_shell_error_result(
                "execute_cmd",
                f"execution timeout after {timeout} seconds (local fallback)",
                ExecuteCmdResult,
                data=data,
            )
        return ExecuteCmdResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=data,
        )

    async def execute_cmd_stream(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        result = await self.execute_cmd(command, cwd=cwd, timeout=timeout, environment=environment)
        if result.code != StatusCode.SUCCESS.code:
            yield _build_shell_error_result(
                "execute_cmd_stream",
                result.message,
                ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return
        chunks: list[tuple[str, str]] = []
        for line in (result.data.stdout or "").splitlines(keepends=True):
            chunks.append((line, "stdout"))
        for line in (result.data.stderr or "").splitlines(keepends=True):
            chunks.append((line, "stderr"))
        for index, (text, kind) in enumerate(chunks):
            yield ExecuteCmdStreamResult(
                code=StatusCode.SUCCESS.code,
                message=f"Get {kind} stream successfully",
                data=ExecuteCmdChunkData(text=text, type=kind, chunk_index=index),
            )
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message="Command executed successfully",
            data=ExecuteCmdChunkData(chunk_index=len(chunks), exit_code=result.data.exit_code),
        )


@SandboxRegistry.provider("jiuwenbox", "code")
class JiuwenBoxCodeProvider(_JiuwenBoxProviderMixin, BaseCodeProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_jiuwenbox(endpoint, config)

    @staticmethod
    def _build_code_command(code: str, language: str, *, force_file: bool) -> Optional[list[str]]:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        if language == "python":
            if force_file:
                return ["bash", "-lc", (
                    "tmp=$(mktemp /tmp/ojw_code_XXXXXX.py) && "
                    f"printf %s {_quote_shell_value(encoded)} | base64 -d > \"$tmp\" && "
                    "python3 \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )]
            return ["python3", "-c", code]
        if language == "javascript":
            if force_file:
                return ["bash", "-lc", (
                    "tmp=$(mktemp /tmp/ojw_code_XXXXXX.js) && "
                    f"printf %s {_quote_shell_value(encoded)} | base64 -d > \"$tmp\" && "
                    "node \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )]
            return ["node", "-e", code]
        return None

    @staticmethod
    def _prepare_code_environment(
        language: str,
        environment: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        merged = dict(environment or {})
        if language == "javascript":
            merged.setdefault("NODE_DISABLE_COLORS", "1")
        elif language == "python":
            merged.setdefault("PYTHONIOENCODING", "utf-8")
            merged.setdefault("PYTHONUTF8", "1")
        return merged

    async def execute_code(
        self,
        code: str,
        *,
        language: str = "python",
        timeout: int = 300,
        environment: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ExecuteCodeResult:
        data = ExecuteCodeData(code_content=code, language=language)
        if not code or not code.strip():
            return _build_code_error_result("execute_code", "code can not be empty", ExecuteCodeResult, data=data)
        if language not in {"python", "javascript"}:
            return _build_code_error_result("execute_code", f"{language} is not supported",
                                            ExecuteCodeResult, data=data)
        command = self._build_code_command(code, language, force_file=bool((options or {}).get("force_file", False)))
        if command is None:
            return _build_code_error_result("execute_code", "subprocess cmd can not be none",
                                            ExecuteCodeResult, data=data)
        exec_timeout = _normalize_exec_timeout(timeout)
        merged_env = self._prepare_code_environment(language, environment)

        extra = self._launcher_extra_params()
        exclude_patterns = _read_excluded_commands(extra)
        fallback_on_failure = bool(extra.get("fallback_on_failure", False)) if isinstance(extra, dict) else False

        # (a) Pre-route when first line matches exclude pattern
        first_line = code.splitlines()[0] if code else ""
        if _command_matches_exclude(first_line, exclude_patterns):
            logger.info(
                "[jiuwenbox] code pre-routed to local (exclude pattern hit on first line): %s",
                first_line,
            )
            local_result = await _run_local_subprocess(
                command,
                cwd="/tmp",
                env=merged_env,
                timeout=exec_timeout,
            )
            return self._wrap_code_local_result(code, language, timeout, local_result)

        result, pipeline_error = await self._run_exec_pipeline(
            sandbox_op=lambda sid: self._get_client().exec(
                sid,
                command,
                cwd="/tmp",
                timeout=exec_timeout,
                environment=merged_env,
            ),
            local_op=lambda: _run_local_subprocess(
                command,
                cwd="/tmp",
                env=merged_env,
                timeout=exec_timeout,
            ),
            fallback_on_failure=fallback_on_failure,
        )
        if pipeline_error:
            return _build_code_error_result("execute_code", pipeline_error, ExecuteCodeResult, data=data)
        if result.get("local"):
            return self._wrap_code_local_result(code, language, timeout, result)

        result_data = ExecuteCodeData(
            code_content=code,
            language=language,
            stdout=result.get("stdout") or "",
            stderr=result.get("stderr") or "",
            exit_code=int(result.get("exit_code") or 0),
        )
        if result_data.exit_code == 124:
            return _build_code_error_result(
                "execute_code",
                f"execution timeout after {timeout} seconds",
                ExecuteCodeResult,
                data=result_data,
            )
        return ExecuteCodeResult(
            code=StatusCode.SUCCESS.code,
            message="Code executed successfully",
            data=result_data,
        )

    @staticmethod
    def _wrap_code_local_result(
        code: str,
        language: str,
        timeout: Optional[int],
        local_result: dict[str, Any],
    ) -> ExecuteCodeResult:
        result_data = ExecuteCodeData(
            code_content=code,
            language=language,
            stdout=local_result.get("stdout") or "",
            stderr=local_result.get("stderr") or "",
            exit_code=int(local_result.get("exit_code") or 0),
        )
        if result_data.exit_code == 124:
            return _build_code_error_result(
                "execute_code",
                f"execution timeout after {timeout} seconds (local fallback)",
                ExecuteCodeResult,
                data=result_data,
            )
        return ExecuteCodeResult(
            code=StatusCode.SUCCESS.code,
            message="Code executed successfully",
            data=result_data,
        )

    async def execute_code_stream(
        self,
        code: str,
        *,
        language: str = "python",
        timeout: int = 300,
        environment: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        result = await self.execute_code(
            code,
            language=language,
            timeout=timeout,
            environment=environment,
            options=options,
        )
        if result.code != StatusCode.SUCCESS.code:
            yield _build_code_error_result(
                "execute_code_stream",
                result.message,
                ExecuteCodeStreamResult,
                data=ExecuteCodeChunkData(chunk_index=0, exit_code=-1),
            )
            return
        chunks: list[tuple[str, str]] = []
        for line in (result.data.stdout or "").splitlines(keepends=True):
            chunks.append((line, "stdout"))
        for line in (result.data.stderr or "").splitlines(keepends=True):
            chunks.append((line, "stderr"))
        for index, (text, kind) in enumerate(chunks):
            yield ExecuteCodeStreamResult(
                code=StatusCode.SUCCESS.code,
                message=f"Get {kind} stream successfully",
                data=ExecuteCodeChunkData(text=text, type=kind, chunk_index=index),
            )
        yield ExecuteCodeStreamResult(
            code=StatusCode.SUCCESS.code,
            message="Code executed successfully",
            data=ExecuteCodeChunkData(chunk_index=len(chunks), exit_code=result.data.exit_code),
        )
