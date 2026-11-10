# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""YuanRong sandbox providers (shell / code / fs).

Talks only to the ADX HTTP API on ``PreDeployLauncherConfig.base_url``:

- ``POST /api/environment/v1/environments``
- ``DELETE /api/environment/v1/environments/{environmentId}``
- ``POST /direct/{environmentId}/invoke``

Create uses the management-plane fields: ``runtime``, ``rootfs``, ``cpu``,
``memory``, ``idleTimeoutSeconds``, and ``extra_config.user``. Invoke uses
``process.exec`` / ``file.read`` / ``file.write`` / ``file.list`` /
``file.search``. ``idle_ttl_seconds`` is unused.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shlex
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Callable, ClassVar, Dict, List, Literal, Optional, Tuple, TypeVar

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
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

DEFAULT_DOCKER_CPU = 1000
DEFAULT_DOCKER_MEMORY = 1024
YUANRONG_RECREATE_RETRIES = 5
YUANRONG_RECREATE_SLEEP_SECONDS = 1
_SDK_LIST_DEPTH = 20
_ADX_ENVIRONMENTS = "/api/environment/v1/environments"
_ADX_INVOKE = "/direct"

_T = TypeVar("_T")


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


def _build_fs_error_result(execution: str, error_msg: str, result_cls: Any, data: Any = None):
    return build_operation_error_result(
        error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
        msg_format_kwargs={"execution": execution, "error_msg": error_msg},
        result_cls=result_cls,
        data=data,
    )


def _quote_shell_value(value: str) -> str:
    return shlex.quote(value)


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


_DIRECTORY_TYPES = frozenset({"dir", "directory"})
_CATEGORY_TYPES = frozenset({"file", "dir", "directory"})


class SandboxSearchLimitError(RuntimeError):
    """``file.search`` exceeded ``max_results`` (contract HTTP 429)."""


def _action_error_message(raw: dict[str, Any]) -> Optional[str]:
    error = raw.get("error")
    if not error:
        return None
    code = raw.get("error_code")
    if code:
        return f"{code}: {error}"
    return str(error)


def _payload_is_directory(item: dict[str, Any]) -> bool:
    if "is_directory" in item:
        return bool(item.get("is_directory"))
    return str(item.get("type") or "") in _DIRECTORY_TYPES


def _item_from_payload(item: dict[str, Any]) -> FileSystemItem:
    raw_type = item.get("type")
    is_directory = _payload_is_directory(item)
    modified = item.get("modified_time")
    if isinstance(modified, bool) or modified is None:
        modified_time = "0"
    else:
        modified_time = str(modified)
    file_type: Optional[str]
    if is_directory or raw_type is None or raw_type in _CATEGORY_TYPES:
        file_type = None
    else:
        file_type = str(raw_type)
    return FileSystemItem(
        name=str(item.get("name") or ""),
        path=str(item.get("path") or ""),
        size=int(item.get("size") or 0),
        is_directory=is_directory,
        modified_time=modified_time,
        type=file_type,
    )


def _normalize_list_items(raw: Any) -> List[dict[str, Any]]:
    if isinstance(raw, dict):
        message = _action_error_message(raw)
        if message:
            raise OSError(message)
        items = raw.get("entries", raw.get("items", []))
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _decode_read_payload(raw: Any, *, binary: bool) -> Any:
    """Accept ``{data, error}`` from ``file.read`` (hex when binary)."""
    if not isinstance(raw, dict):
        return raw
    message = _action_error_message(raw)
    if message:
        if "No such file" in message:
            raise FileNotFoundError(message)
        if "Is a directory" in message:
            raise IsADirectoryError(message)
        raise OSError(message)
    if "data" in raw:
        data = raw.get("data")
    elif "content" in raw:
        data = raw.get("content")
    else:
        return raw
    encoding = str(raw.get("content_encoding") or "")
    if encoding == "base64" and isinstance(data, str):
        return base64.b64decode(data)
    if _is_hex_payload(data, binary=binary, encoding=encoding, raw=raw):
        return bytes.fromhex(data)
    return data


def _is_hex_payload(data: Any, *, binary: bool, encoding: str, raw: dict[str, Any]) -> bool:
    if not isinstance(data, str):
        return False
    if binary or encoding == "hex":
        return True
    return raw.get("binary") is True


def _keep_listed_item(
    item: dict[str, Any],
    *,
    include_files: bool,
    include_dirs: bool,
) -> bool:
    if _payload_is_directory(item):
        return include_dirs
    return include_files


def _normalize_exec_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "returncode": -1,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"unexpected result type: {type(result)!r}",
        }
    exit_code = result.get("exit_code", result.get("returncode", -1))
    try:
        exit_code = int(exit_code)
    except (TypeError, ValueError):
        exit_code = -1
    return {
        "returncode": exit_code,
        "exit_code": exit_code,
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
    }


class AdxHttpError(RuntimeError):
    """ADX HTTP error. ``status == 404`` means the environment is gone."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _normalize_rootfs(extra: dict[str, Any]) -> str:
    """Build the rootfs JSON the YuanRong create path always sent.

    A string is kept. A dict aliases ``image`` to ``imageurl``. Otherwise
    ``image`` / ``workdir`` / ``mounts`` are assembled into one document.
    """
    rootfs = extra.get("rootfs")
    if isinstance(rootfs, str) and rootfs.strip():
        return rootfs
    if isinstance(rootfs, dict):
        payload = dict(rootfs)
        if "imageurl" not in payload and "image" in payload:
            payload["imageurl"] = payload.pop("image")
        return json.dumps(payload)
    return json.dumps(
        {
            "type": "image",
            "imageurl": extra.get("image"),
            "workdir": extra.get("workdir"),
            "mounts": extra.get("mounts"),
        }
    )


def _normalize_executor(value: Any) -> str:
    """Accept supervisor / docker / conch, dropping a stray wrapping quote."""
    text = str(value or "supervisor").strip().lower()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    text = text.strip("'\"")
    if text not in {"supervisor", "docker", "conch"}:
        return "supervisor"
    return text


def _adx_create_body(extra: dict[str, Any], executor: str) -> dict[str, Any]:
    """HTTP body for the same fields ``yr.sandbox.Sandbox`` used to receive."""
    executor = _normalize_executor(executor)
    body: dict[str, Any] = {
        "rootfs": _normalize_rootfs(extra),
        "cpu": int(extra.get("cpu", DEFAULT_DOCKER_CPU)),
        "memory": int(extra.get("memory", DEFAULT_DOCKER_MEMORY)),
        "idleTimeoutSeconds": _idle_timeout_seconds(extra),
    }
    if executor in {"supervisor", "docker", "conch"}:
        body["runtime"] = executor
    user = extra.get("user")
    if isinstance(user, str) and user.strip():
        body["extra_config"] = {"user": user.strip()}
    return body


def _idle_timeout_seconds(extra: dict[str, Any]) -> int:
    """Contract field. ``0`` disables idle eviction; negatives are rejected."""
    raw = extra.get("idleTimeoutSeconds")
    if raw is None:
        raw = extra.get("idle_timeout", 0)
    if isinstance(raw, bool):
        raise TypeError("idleTimeoutSeconds must be an integer")
    idle_timeout = int(raw)
    if idle_timeout < 0:
        return 0
    return idle_timeout


def _exec_args(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    environment: Optional[Dict[str, str]] = None,
) -> dict[str, Any]:
    """``process.exec`` args. The server already runs a string ``cmd`` with ``/bin/sh -c``."""
    cmd = command
    if environment:
        env_prefix = " ".join(
            f"{key}={_quote_shell_value(value)}" for key, value in environment.items()
        )
        cmd = f"export {env_prefix} && {command}"
    args: dict[str, Any] = {"cmd": cmd}
    if isinstance(cwd, str) and cwd and cwd != ".":
        args["cwd"] = cwd
    if _positive_timeout(timeout):
        args["timeout"] = timeout
    return args


def _positive_timeout(timeout: Optional[int]) -> bool:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return False
    return timeout > 0


def _stderr_reports_timeout(stderr: str, exit_code: int) -> bool:
    if exit_code == 0:
        return False
    return "timed out" in stderr.lower()


def _http_error_message(body: Any) -> str:
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if isinstance(message, dict):
            return str(message.get("message") or message)
        if message:
            return str(message)
    return "ADX request failed"


def _decode_mgmt_data(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RuntimeError("ADX management response is not an object")
    data = body.get("data")
    if isinstance(data, str):
        decoded = json.loads(base64.b64decode(data))
        if isinstance(decoded, dict):
            return decoded
    if isinstance(data, dict):
        return data
    raise RuntimeError("ADX management response has no data")


def _log_payload(payload: Optional[dict[str, Any]]) -> str:
    if payload is None:
        return ""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= 2000:
        return text
    return text[:2000] + "...(truncated)"


def _adx_request(
    method: str,
    url: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30,
) -> tuple[int, Any]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    logger.info(
        "[yuanrong] request method=%s url=%s timeout=%s payload=%s",
        method,
        url,
        timeout,
        _log_payload(payload),
    )
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    if not raw:
        return status, {}
    return status, json.loads(raw.decode("utf-8"))


def _invoke_url(base_url: str, environment_id: str) -> str:
    quoted = urllib.parse.quote(environment_id, safe="")
    return f"{base_url}{_ADX_INVOKE}/{quoted}/invoke"


def _environment_url(base_url: str, environment_id: str) -> str:
    quoted = urllib.parse.quote(environment_id, safe="")
    return f"{base_url}{_ADX_ENVIRONMENTS}/{quoted}"


def _unlimited_list_depth(max_depth: Optional[int]) -> bool:
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        return True
    return max_depth <= 0


def _list_depth(recursive: bool, max_depth: Optional[int]) -> int:
    """Map SDK ``list_files`` depth onto ADX ``file.list`` depth.

    The server calls the SDK with ``max_depth=depth-1`` and recurses only when
    ``depth > 1``. Non-recursive listing is depth 1. Unlimited recursion keeps
    the SDK cap of 20.
    """
    if not recursive:
        return 1
    if _unlimited_list_depth(max_depth):
        return _SDK_LIST_DEPTH + 1
    return min(int(max_depth) + 1, 64)


def _write_file_data(data: Any, *, binary: bool) -> str:
    if binary:
        raw = data if isinstance(data, (bytes, bytearray, memoryview)) else str(data).encode("utf-8")
        return bytes(raw).hex()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("utf-8")
    return str(data)


def _coerce_bytes(value: Any) -> bytes:
    """Normalize bytes / bytearray / memoryview to bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"expected bytes-like content, got {type(value)!r}")


def _coerce_text(value: Any, encoding: str = "utf-8") -> str:
    if isinstance(value, str):
        return value
    return _coerce_bytes(value).decode(encoding)


def _endpoint_value(endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig], attr: str) -> Any:
    value = getattr(endpoint, attr, None)
    if value is not None:
        return value
    launcher_config = getattr(config, "launcher_config", None) if config is not None else None
    return getattr(launcher_config, attr, None)


def build_yuanrong_shared_scope_key(base_url: str, isolation_key: Optional[str] = None) -> str:
    """Build the provider shared-cache key for a base_url + optional isolation_key pair."""
    parts = [str(base_url).rstrip("/")]
    if isinstance(isolation_key, str) and isolation_key:
        parts.append(isolation_key)
    return "|".join(parts)


class _YuanrongProviderMixin:
    """Shared ADX environment lifecycle for shell/code/fs providers."""

    _shared_lock: ClassVar[threading.Lock] = threading.Lock()
    _lifecycle_locks_guard: ClassVar[threading.Lock] = threading.Lock()
    # shared_key -> RLock serializing create / invalidate / recreate for that key
    _lifecycle_locks: ClassVar[Dict[str, threading.RLock]] = {}
    # shared_key -> (environment_id, runtime)
    _shared_instances: ClassVar[Dict[str, Tuple[str, str]]] = {}

    _instance: Optional[str]
    _executor: str
    _timeout_seconds: int

    def _init_yuanrong(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig]) -> None:
        self._instance = None
        self._timeout_seconds = int(getattr(config, "timeout_seconds", 30) or 30)
        extra = self._launcher_extra_params()
        self._executor = _normalize_executor(extra.get("executor"))

    def _base_url(self) -> str:
        base_url = _endpoint_value(self.endpoint, self.config, "base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("YuanRong ADX base_url is required")
        return base_url.rstrip("/")

    def _request_timeout(self, command_timeout: Optional[float] = None) -> float:
        base = float(self._timeout_seconds or 30)
        if isinstance(command_timeout, bool) or not isinstance(command_timeout, (int, float)):
            return base
        return max(base, float(command_timeout) + 5)

    def _launcher_extra_params(self) -> dict[str, Any]:
        launcher_config = getattr(self.config, "launcher_config", None) if self.config is not None else None
        if launcher_config is None:
            return {}
        extra_params = getattr(launcher_config, "extra_params", None)
        return extra_params if isinstance(extra_params, dict) else {}

    def _shared_scope_key(self) -> str:
        isolation_key = getattr(self.endpoint, "isolation_key", None)
        return build_yuanrong_shared_scope_key(
            self._base_url(),
            isolation_key if isinstance(isolation_key, str) else None,
        )

    @classmethod
    def _lifecycle_lock_for(cls, shared_key: str) -> threading.RLock:
        with cls._lifecycle_locks_guard:
            lock = cls._lifecycle_locks.get(shared_key)
            if lock is None:
                lock = threading.RLock()
                cls._lifecycle_locks[shared_key] = lock
            return lock

    def _create_instance(self) -> Tuple[str, str]:
        body = _adx_create_body(self._launcher_extra_params(), self._executor)
        status, response = _adx_request(
            "POST",
            f"{self._base_url()}{_ADX_ENVIRONMENTS}",
            payload=body,
            timeout=self._request_timeout(),
        )
        if status != 200:
            raise AdxHttpError(status, _http_error_message(response))
        environment_id = _decode_mgmt_data(response).get("environmentId")
        if not isinstance(environment_id, str) or not environment_id:
            raise RuntimeError("ADX create response has no environmentId")
        runtime = body.get("runtime")
        executor = runtime if isinstance(runtime, str) and runtime else self._executor
        return environment_id, executor

    def _bind_instance(self, environment_id: str, executor: str) -> Tuple[str, str]:
        self._instance = environment_id
        self._executor = executor
        return environment_id, executor

    def _invoke(self, environment_id: str, action: str, args: dict[str, Any], *, timeout: float) -> Any:
        status, body = _adx_request(
            "POST",
            _invoke_url(self._base_url(), environment_id),
            payload={"action": action, "args": args},
            timeout=timeout,
        )
        if status == 429:
            message = "file search max_results exceeded; narrow the search"
            if isinstance(body, dict) and body.get("error"):
                message = str(body["error"])
            raise SandboxSearchLimitError(message)
        if status >= 400:
            raise AdxHttpError(status, _http_error_message(body))
        return body

    def _ensure_shared_instance(self, *, sleep_before_create: bool = False) -> Tuple[str, str]:
        """Return the cached environment id, creating one if needed.

        Per-key lifecycle lock serializes create for the same shared_key.
        ``_shared_lock`` only guards dict access; create/sleep/delete stay outside it.
        """
        shared_key = self._shared_scope_key()
        with self._lifecycle_lock_for(shared_key):
            with self._shared_lock:
                cached = self._shared_instances.get(shared_key)
                if cached is not None:
                    return self._bind_instance(cached[0], cached[1])
            if sleep_before_create:
                time.sleep(YUANRONG_RECREATE_SLEEP_SECONDS)
                with self._shared_lock:
                    cached = self._shared_instances.get(shared_key)
                    if cached is not None:
                        return self._bind_instance(cached[0], cached[1])
            environment_id, executor = self._create_instance()
            duplicate: Optional[str] = None
            with self._shared_lock:
                cached = self._shared_instances.get(shared_key)
                if cached is not None:
                    duplicate = environment_id
                    environment_id, executor = cached
                else:
                    self._shared_instances[shared_key] = (environment_id, executor)
                    logger.info(
                        "[yuanrong] created environment executor=%s shared_key=%s",
                        executor,
                        shared_key,
                    )
            if duplicate is not None:
                logger.info("[yuanrong] discarding duplicate environment shared_key=%s", shared_key)
                _delete_environment(self._base_url(), duplicate)
            return self._bind_instance(environment_id, executor)

    def _get_instance(self) -> Tuple[str, str]:
        return self._ensure_shared_instance(sleep_before_create=False)

    def _invalidate_cached_instance(self, stale_environment_id: str) -> None:
        """Drop cache only when it still points at ``stale_environment_id``."""
        shared_key = self._shared_scope_key()
        to_delete: Optional[str] = None
        with self._lifecycle_lock_for(shared_key):
            with self._shared_lock:
                cached = self._shared_instances.get(shared_key)
                if cached is None:
                    self._instance = None
                elif cached[0] == stale_environment_id:
                    popped = self._shared_instances.pop(shared_key, None)
                    to_delete = popped[0] if popped is not None else None
                    self._instance = None
                else:
                    self._bind_instance(cached[0], cached[1])
        if to_delete is not None:
            _delete_environment(shared_key.split("|", 1)[0], to_delete)

    def _call_with_recreate(self, fn: Callable[[str], _T]) -> _T:
        shared_key = self._shared_scope_key()
        last_exc: Optional[BaseException] = None
        for attempt in range(YUANRONG_RECREATE_RETRIES):
            environment_id, _ = self._ensure_shared_instance(sleep_before_create=attempt > 0)
            try:
                return fn(environment_id)
            except AdxHttpError as exc:
                last_exc = exc
                if exc.status != 404:
                    raise
                logger.warning(
                    "[yuanrong] recreate status=404 attempt=%d/%d shared_key=%s",
                    attempt + 1,
                    YUANRONG_RECREATE_RETRIES,
                    shared_key,
                )
                self._invalidate_cached_instance(stale_environment_id=environment_id)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("yuanrong sandbox recreate exhausted without an error")

    @classmethod
    def pop_cached_instance(cls, shared_key: str) -> Optional[Tuple[str, str]]:
        with cls._lifecycle_lock_for(shared_key):
            with cls._shared_lock:
                return cls._shared_instances.pop(shared_key, None)

    @classmethod
    def clear_shared_for_base_url(cls, base_url: str) -> List[Tuple[str, str, str]]:
        prefix = f"{str(base_url).rstrip('/')}|"
        normalized = str(base_url).rstrip("/")
        with cls._shared_lock:
            keys = [k for k in cls._shared_instances if k == normalized or k.startswith(prefix)]
        removed: List[Tuple[str, str, str]] = []
        for key in sorted(keys):
            with cls._lifecycle_lock_for(key):
                with cls._shared_lock:
                    cached = cls._shared_instances.pop(key, None)
                if cached is not None:
                    removed.append((key, cached[0], cached[1]))
        return removed

    def _exec_sync(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        args = _exec_args(command, cwd=cwd, timeout=timeout, environment=environment)

        def _do(environment_id: str) -> Dict[str, Any]:
            result = self._invoke(
                environment_id,
                "process.exec",
                args,
                timeout=self._request_timeout(timeout),
            )
            return _normalize_exec_result(result)

        return self._call_with_recreate(_do)

    async def _run_command(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self._exec_sync, command, cwd, timeout, environment)

    def _fs_read_sync(self, path: str, *, mode: str = "rb") -> Any:
        binary = "b" in mode

        def _do(environment_id: str) -> Any:
            raw = self._invoke(
                environment_id,
                "file.read",
                {"path": path, "binary": binary},
                timeout=self._request_timeout(),
            )
            return _decode_read_payload(raw, binary=binary)

        return self._call_with_recreate(_do)

    def _fs_write_sync(self, path: str, data: Any, *, mode: str = "wb") -> None:
        binary = "b" in mode
        payload = _write_file_data(data, binary=binary)

        def _do(environment_id: str) -> None:
            result = self._invoke(
                environment_id,
                "file.write",
                {"path": path, "data": payload, "binary": binary, "append": "a" in mode},
                timeout=self._request_timeout(),
            )
            if isinstance(result, dict):
                message = _action_error_message(result)
                if message:
                    raise OSError(message)

        self._call_with_recreate(_do)

    def _fs_list_sync(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_depth: Optional[int] = None,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> List[dict[str, Any]]:
        depth = _list_depth(recursive, max_depth)

        def _do(environment_id: str) -> List[dict[str, Any]]:
            raw = self._invoke(
                environment_id,
                "file.list",
                {"path": path, "depth": depth},
                timeout=self._request_timeout(),
            )
            items = _normalize_list_items(raw)
            return [
                item for item in items
                if _keep_listed_item(item, include_files=include_files, include_dirs=include_dirs)
            ]

        return self._call_with_recreate(_do)

    def _fs_search_sync(
        self,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[dict[str, Any]]:
        args: dict[str, Any] = {"path": path}
        if isinstance(pattern, str):
            args["pattern"] = pattern
        if exclude_patterns is not None:
            args["exclude_patterns"] = exclude_patterns

        def _do(environment_id: str) -> List[dict[str, Any]]:
            raw = self._invoke(
                environment_id,
                "file.search",
                args,
                timeout=self._request_timeout(),
            )
            return _normalize_list_items(raw)

        return self._call_with_recreate(_do)

    def _fs_path_exists_sync(self, path: str) -> bool:
        try:
            self._fs_read_sync(path, mode="rb")
            return True
        except FileNotFoundError:
            return False
        except IsADirectoryError:
            return True
        except OSError as exc:
            # Some runtimes raise ENOENT-style errors instead of FileNotFoundError.
            if getattr(exc, "errno", None) == 2 or "No such file" in str(exc):
                return False
            raise


def _delete_environment(base_url: str, environment_id: str) -> None:
    try:
        status, body = _adx_request(
            "DELETE",
            _environment_url(base_url, environment_id),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[yuanrong] delete environment failed: %s", exc)
        return
    if status in (200, 404):
        return
    logger.warning(
        "[yuanrong] delete environment status=%s message=%s",
        status,
        _http_error_message(body),
    )


async def delete_yuanrong_sandbox(
    *,
    shared_key: Optional[str] = None,
    base_url: Optional[str] = None,
    reason: str = "teardown",
) -> list[str]:
    """Delete cached ADX environments on sysoperation teardown."""
    entries: List[Tuple[str, str]] = []
    if shared_key:
        popped = _YuanrongProviderMixin.pop_cached_instance(shared_key)
        if popped is not None:
            entries.append((shared_key, popped[0]))
    elif base_url:
        entries.extend(
            (key, environment_id)
            for key, environment_id, _runtime in _YuanrongProviderMixin.clear_shared_for_base_url(base_url)
        )

    deleted: list[str] = []
    for key, environment_id in entries:
        await asyncio.to_thread(_delete_environment, key.split("|", 1)[0], environment_id)
        deleted.append(key)
        logger.info("[yuanrong] deleted environment shared_key=%s reason=%s", key, reason)
    return deleted


@SandboxRegistry.provider("yuanrong", "fs")
class YuanrongFSProvider(_YuanrongProviderMixin, BaseFSProvider):
    """YuanRong FS provider via ADX ``file.read`` / ``file.write`` / ``file.list`` / ``file.search``."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_yuanrong(endpoint, config)

    async def read_file(self, path: str, mode: str = "text", **kwargs) -> ReadFileResult:
        head = kwargs.pop("head", None)
        tail = kwargs.pop("tail", None)
        line_range = kwargs.pop("line_range", None)
        encoding = kwargs.get("encoding", "utf-8")
        head, tail, line_range = _normalize_read_params(head=head, tail=tail, line_range=line_range)
        validation_error = _validate_read_params(mode=mode, head=head, tail=tail, line_range=line_range)
        if validation_error:
            return _build_fs_error_result("read_file", validation_error, ReadFileResult)
        try:
            if mode == "bytes":
                content: str | bytes = _coerce_bytes(
                    await asyncio.to_thread(self._fs_read_sync, path, mode="rb")
                )
            else:
                text = _coerce_text(
                    await asyncio.to_thread(self._fs_read_sync, path, mode="r"),
                    encoding=encoding,
                )
                lines, _ = _select_text_lines(text, head=head, tail=tail, line_range=line_range)
                content = "".join(lines)
            return ReadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileData(path=path, content=content, mode=mode or "text"),
            )
        except Exception as exc:  # noqa: BLE001
            return _build_fs_error_result("read_file", str(exc), ReadFileResult)

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

        result = await self.read_file(
            path, mode=mode, head=head, tail=tail, line_range=line_range, encoding=encoding
        )
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
        if not selected_lines:
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

    async def write_file(self, path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult:
        append = bool(kwargs.get("append", False))
        prepend_newline = kwargs.get("prepend_newline", True)
        append_newline = kwargs.get("append_newline", False)
        create_if_not_exist = kwargs.get("create_if_not_exist", True)
        encoding = kwargs.get("encoding", "utf-8")
        try:
            if not create_if_not_exist:
                exists = await asyncio.to_thread(self._fs_path_exists_sync, path)
                if not exists:
                    raise FileNotFoundError(f"File does not exist: {path}")

            if mode == "bytes":
                raw: str | bytes = content if isinstance(content, bytes) else bytes(content)
                open_mode = "ab" if append else "wb"
            else:
                text = content.decode(encoding) if isinstance(content, bytes) else str(content)
                if prepend_newline:
                    text = "\n" + text
                if append_newline:
                    text += "\n"
                raw = text
                open_mode = "a" if append else "w"

            await asyncio.to_thread(self._fs_write_sync, path, raw, mode=open_mode)
            size = len(raw) if isinstance(raw, bytes) else len(raw.encode(encoding))
            return WriteFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=WriteFileData(path=path, size=size, mode=mode or "text"),
            )
        except Exception as exc:  # noqa: BLE001
            return _build_fs_error_result("write_file", str(exc), WriteFileResult)

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
        del create_parent_dirs, preserve_permissions, chunk_size  # parents created by file.write
        try:
            if not overwrite:
                exists = await asyncio.to_thread(self._fs_path_exists_sync, target_path)
                if exists:
                    raise FileExistsError(f"File already exists: {target_path}")
            raw = await asyncio.to_thread(Path(local_path).read_bytes)
            await asyncio.to_thread(self._fs_write_sync, target_path, raw, mode="wb")
            return UploadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=UploadFileData(local_path=local_path, target_path=target_path, size=len(raw)),
            )
        except Exception as exc:  # noqa: BLE001
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
        del preserve_permissions, chunk_size
        try:
            target = Path(local_path)
            if create_parent_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not overwrite:
                raise FileExistsError(f"File already exists: {local_path}")
            raw = _coerce_bytes(await asyncio.to_thread(self._fs_read_sync, source_path, mode="rb"))
            await asyncio.to_thread(target.write_bytes, raw)
            return DownloadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=DownloadFileData(source_path=source_path, local_path=local_path, size=len(raw)),
            )
        except Exception as exc:  # noqa: BLE001
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
            raw_items = await asyncio.to_thread(
                self._fs_list_sync,
                path,
                recursive=recursive,
                max_depth=max_depth,
                include_files=True,
                include_dirs=False,
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
        except Exception as exc:  # noqa: BLE001
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
            raw_items = await asyncio.to_thread(
                self._fs_list_sync,
                path,
                recursive=recursive,
                max_depth=max_depth,
                include_files=False,
                include_dirs=True,
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
        except Exception as exc:  # noqa: BLE001
            return _build_fs_error_result("list_directories", str(exc), ListDirsResult)

    async def search_files(
        self,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> SearchFilesResult:
        try:
            raw_items = await asyncio.to_thread(
                self._fs_search_sync,
                path,
                pattern,
                exclude_patterns,
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
        except Exception as exc:  # noqa: BLE001
            return _build_fs_error_result("search_files", str(exc), SearchFilesResult)


@SandboxRegistry.provider("yuanrong", "shell")
class YuanrongShellProvider(_YuanrongProviderMixin, BaseShellProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_yuanrong(endpoint, config)

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

        try:
            result = await self._run_command(
                command,
                cwd=cwd,
                timeout=timeout,
                environment=environment,
            )
        except Exception as exc:  # noqa: BLE001
            return _build_shell_error_result("execute_cmd", str(exc), ExecuteCmdResult)

        exit_code = int(result.get("exit_code", result.get("returncode", -1)))
        stderr = result.get("stderr") or ""
        data = ExecuteCmdData(
            command=command,
            cwd=cwd or ".",
            stdout=result.get("stdout") or "",
            stderr=stderr,
            exit_code=exit_code,
        )
        if exit_code == 124 or _stderr_reports_timeout(stderr, exit_code):
            return _build_shell_error_result(
                "execute_cmd",
                f"execution timeout after {timeout} seconds",
                ExecuteCmdResult,
                data=data,
            )
        return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

    async def execute_cmd_stream(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        if not command or not command.strip():
            yield _build_shell_error_result(
                "execute_cmd_stream",
                "command can not be empty",
                ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return

        result = await self.execute_cmd(command, cwd=cwd, timeout=timeout, environment=environment)
        if result.code != StatusCode.SUCCESS.code:
            yield _build_shell_error_result(
                "execute_cmd_stream",
                result.message.split("reason: ", 1)[-1] if "reason: " in result.message else result.message,
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


@SandboxRegistry.provider("yuanrong", "code")
class YuanrongCodeProvider(_YuanrongProviderMixin, BaseCodeProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._init_yuanrong(endpoint, config)
        self._shell_provider = YuanrongShellProvider(endpoint, config)

    @staticmethod
    def _build_code_command(code: str, language: str, *, force_file: bool) -> Optional[str]:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        if language == "python":
            if force_file:
                return (
                    "tmp=$(mktemp /tmp/ojw_code_XXXXXX.py) && "
                    f"printf %s {_quote_shell_value(encoded)} | base64 -d > \"$tmp\" && "
                    "python3 \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )
            return (
                "python3 -c "
                + _quote_shell_value(
                    f"import base64; exec(base64.b64decode('{encoded}').decode('utf-8'))"
                )
            )
        if language == "javascript":
            if force_file:
                return (
                    "tmp=$(mktemp /tmp/ojw_code_XXXXXX.js) && "
                    f"printf %s {_quote_shell_value(encoded)} | base64 -d > \"$tmp\" && "
                    "node \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )
            return "node -e " + _quote_shell_value(
                f"eval(Buffer.from('{encoded}','base64').toString('utf8'))"
            )
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
        language: Literal["python", "javascript"] = "python",
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
            return _build_code_error_result(
                "execute_code",
                f"{language} is not supported",
                ExecuteCodeResult,
                data=data,
            )

        force_file = bool((options or {}).get("force_file", False))
        command = self._build_code_command(code, language, force_file=force_file)
        if command is None:
            return _build_code_error_result(
                "execute_code",
                "subprocess cmd can not be none",
                ExecuteCodeResult,
                data=data,
            )

        # Reuse shell provider so both share the same cached YuanRong instance.
        self._shell_provider.endpoint = self.endpoint
        self._shell_provider.config = self.config
        shell_result = await self._shell_provider.execute_cmd(
            command=command,
            cwd=cwd or "/tmp",
            timeout=timeout,
            environment=self._prepare_code_environment(language, environment),
        )
        result_data = ExecuteCodeData(
            code_content=code,
            language=language,
            stdout=shell_result.data.stdout if shell_result.data else "",
            stderr=shell_result.data.stderr if shell_result.data else "",
            exit_code=shell_result.data.exit_code if shell_result.data else -1,
        )
        if shell_result.code != StatusCode.SUCCESS.code:
            if "timeout" in shell_result.message.lower():
                return _build_code_error_result(
                    "execute_code",
                    f"execution timeout after {timeout} seconds",
                    ExecuteCodeResult,
                    data=result_data,
                )
            return _build_code_error_result(
                "execute_code",
                shell_result.message.split("reason: ", 1)[-1]
                if "reason: " in shell_result.message
                else shell_result.message,
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
        language: Literal["python", "javascript"] = "python",
        timeout: int = 300,
        environment: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        data = ExecuteCodeChunkData(chunk_index=0, exit_code=-1)
        if not code or not code.strip():
            yield _build_code_error_result(
                "execute_code_stream", "code can not be empty", ExecuteCodeStreamResult, data
            )
            return
        if language not in {"python", "javascript"}:
            yield _build_code_error_result(
                "execute_code_stream",
                f"{language} is not supported",
                ExecuteCodeStreamResult,
                data,
            )
            return

        result = await self.execute_code(
            code,
            language=language,
            timeout=timeout,
            environment=environment,
            cwd=cwd,
            options=options,
        )
        if result.code != StatusCode.SUCCESS.code:
            yield _build_code_error_result(
                "execute_code_stream",
                result.message.split("reason: ", 1)[-1] if "reason: " in result.message else result.message,
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

