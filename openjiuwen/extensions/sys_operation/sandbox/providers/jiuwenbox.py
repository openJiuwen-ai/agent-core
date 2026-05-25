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
from typing import Any, AsyncIterator, Callable, ClassVar, Dict, List, Optional, Sequence, Tuple, TypeVar

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


# jiuwenbox server 端 ``SandboxNotFoundError`` 的 ``error`` 字段恒为
# ``"Sandbox '<id>' not found"``; 其他 404 (例如 ``File not found: <path>``)
# 不应触发沙箱重建。 用前缀 + ``not found`` 双匹配, 大小写不敏感, 抗未来微调。
_SANDBOX_NOT_FOUND_RE = re.compile(r"^\s*Sandbox\b.*\bnot found\b", re.IGNORECASE)


def _is_sandbox_not_found_error(exc: BaseException) -> bool:
    """``True`` 当且仅当 ``exc`` 是 jiuwenbox 服务端报「沙箱不存在」的 404.

    与「文件不存在 / 目录不存在」等 404 严格区分; 后者会原样 reraise 而不
    触发上层自动重建路径。
    """
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


class _JiuwenBoxClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

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
        """``PUT /api/v1/timeout`` —— 配置 jiuwenbox server 的 idle reaper 策略.

        jiuwenbox 的空闲沙箱回收由 ``SandboxManager.policy.timeout`` (root policy)
        驱动, 而非 per-sandbox policy 的 ``timeout`` 子段 (后者仅供 ``GET
        /policies/{id}`` 配置回显, 不影响 reaping 行为)。 因此要让客户端配置的
        ``idle_ttl_seconds`` / ``idle_check_interval`` 真正生效, 必须显式调用
        ``PUT /api/v1/timeout``。

        本接口走 ``UpdateTimeoutRequest`` 的 partial update 语义:
        ``model_dump(exclude_unset=True)`` 决定哪些字段实际生效 —— 只有 body
        里出现的字段会被写回 server, 没传的字段保留之前的值。 因此:

        - 两个参数都为 ``None`` 时直接 no-op (不发请求, 不破坏服务端默认值).
        - 任一非 ``None`` 时只把非 ``None`` 的字段塞进 body; ``idle_check_
          interval=None`` 不会被强制写成 null (server 端会拒绝 ``null``).
        - ``idle_timeout=0`` / 负数透传给 server, 由 ``TimeoutPolicy`` 统一
          归一化为 ``None`` (即"禁用 reaper")。
        """
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
        """``DELETE /api/v1/sandboxes/{sandbox_id}``; 204 视为成功, 404 当作幂等成功。

        ``force_recreate_jiuwenbox_sandbox`` 会在创建新 sandbox 后主动调用本方法
        把旧 sandbox 从 jiuwenbox server 上回收, 避免每次 ``/sandbox files
        allow|deny`` 都让服务端的活跃 sandbox 数 +1 (jiuwenbox 端**没有**
        idle-TTL 自动清理: 见 ``sandbox_manager.py`` 注释"sandbox registry 视为
        ephemeral across restarts", 只在进程重启时才丢弃 sandbox 描述符)。

        404 也吞掉是因为正常的并发清理路径 (例如 server 重启 + 老 ID 提前过期,
        或两条 ``/sandbox files``命令在短时间内串行触发了两次 force_recreate)
        会让我们尝试删除一个 server 已经不认识的 ID, 这种情况等价于"我们想要
        的最终状态已达成", 不应当成错误向上传播。
        """
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
            # 「沙箱不存在」的 404 必须重新抛出, 让上层 retry 包装感知;
            # 仅「文件 / 目录不存在」的 404 才视为 "path 不存在", 返回 False。
            if exc.response.status_code == 404 and not _is_sandbox_not_found_error(exc):
                return False
            raise
        return any(item.get("path") == sandbox_path for item in items)

    def close(self) -> None:
        """关闭底层 httpx.Client, 释放连接池。

        提供给一次性创建的临时 client (例如 ``force_recreate_jiuwenbox_sandbox``
        里那个独立创建的 client) 显式 cleanup 用; 已经在 ``with`` block 中使用
        时无需直接调用本方法。 close 失败时静默吞掉, 因为这通常发生在进程退出
        路径上, 抛错只会扰乱上层的 finally / error reporting。
        """
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            logger.warning("[jiuwenbox] close client failed")

    def __enter__(self) -> "_JiuwenBoxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ─── 沙箱丢失自动重建参数 ─────────────────────────────────────────────────
# 远端 jiuwenbox 重启 / 沙箱被回收时, 所有 fs / shell / code op 会收到
# 「Sandbox '<id>' not found」404; 包装层 ``_execute_with_sandbox_retry``
# 会自动 force_recreate + 重试。 重试次数与退避间隔在这里集中常量化:
# - ``JIUWENBOX_SANDBOX_RECREATE_RETRIES`` 环境变量覆盖默认值; 非法 / 缺失
#   回落 ``_DEFAULT_SANDBOX_RECREATE_RETRIES``; 显式 0 = 禁用重试。
# - 退避间隔目前固定 1.0 秒, 本期不对外暴露。
_DEFAULT_SANDBOX_RECREATE_RETRIES = 3
_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS = 1.0


def _resolve_recreate_retries() -> int:
    """读 ``JIUWENBOX_SANDBOX_RECREATE_RETRIES``; 非法 / 缺失回落默认, ``0`` 禁用重试."""
    raw = os.environ.get("JIUWENBOX_SANDBOX_RECREATE_RETRIES")
    if raw is None or raw.strip() == "":
        return _DEFAULT_SANDBOX_RECREATE_RETRIES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[jiuwenbox] JIUWENBOX_SANDBOX_RECREATE_RETRIES=%r 非法, 回落默认 %d",
            raw, _DEFAULT_SANDBOX_RECREATE_RETRIES,
        )
        return _DEFAULT_SANDBOX_RECREATE_RETRIES
    return max(value, 0)


_T = TypeVar("_T")


class _JiuwenBoxProviderMixin:
    _shared_lock = threading.Lock()
    _shared_sandbox_ids: Dict[str, str] = {}
    # 类级 asyncio.Lock, 串行化沙箱重建; 防止 N 个 op 同时撞 "Sandbox not found"
    # 时各自发起一次 ``create_sandbox``。 ``asyncio.Lock`` 需绑定 event loop,
    # 所以走 lazy init; 初始化本身用 ``_recreate_lock_init`` (threading.Lock)
    # 保护以兼容多线程首次访问的竞争。
    _recreate_lock: ClassVar[Optional[asyncio.Lock]] = None
    _recreate_lock_init: ClassVar[threading.Lock] = threading.Lock()

    # ``_configure_server_idle_timeout`` 里用来跨实例去重 ``PUT /api/v1/timeout``
    # 的进程内缓存: 同一个 ``base_url`` 上一次成功 PUT 过的 (idle_timeout,
    # idle_check_interval) 二元组。 后续 PUT 撞同样的值时跳过, 避免给
    # jiuwenbox server 带来 stop+start reaper 的额外延迟 (服务端虽然已经做了
    # 短路, 客户端再省一次 HTTP RTT 仍然划算)。
    _idle_timeout_cache: ClassVar[Dict[str, Tuple[Optional[int], Optional[int]]]] = {}
    _idle_timeout_cache_lock: ClassVar[threading.Lock] = threading.Lock()

    _client: Optional[_JiuwenBoxClient]
    _sandbox_id: Optional[str]
    _timeout_seconds: int

    def _init_jiuwenbox(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig]) -> None:
        self._client = None
        self._sandbox_id = (
            _endpoint_value(endpoint, config, "sandbox_id")
            or _endpoint_value(endpoint, config, "id")
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

    def _idle_timeout_from_launcher(self) -> Tuple[Optional[int], Optional[int]]:
        """从 ``launcher_config`` 读取 ``idle_ttl_seconds`` / ``idle_check_interval``.

        返回 ``(idle_timeout, idle_check_interval)`` 二元组; 缺失项为 ``None``。

        - ``idle_ttl_seconds`` 是 ``SandboxLauncherConfig`` 的通用字段, 直接从
          ``launcher_config`` 上读。
        - ``idle_check_interval`` 是 jiuwenbox 私有的 reaper 轮询间隔, 故走
          ``launcher_config.extra_params["idle_check_interval"]`` (由
          ``create_sandbox_sysop_card`` 在显式给值时塞入), 避免给通用 schema
          加 jiuwenbox-only 的字段。 缺失或非整数视为 ``None``。
        """
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
            # bool 是 int 子类, 隐式落进 idle_check_interval 几乎肯定是配置误写;
            # 显式拒掉而不是悄悄当 1 / 0 用。
            idle_check_interval = None
        elif isinstance(raw_check, int):
            idle_check_interval = raw_check
        elif isinstance(raw_check, float):
            idle_check_interval = int(raw_check)
        else:
            idle_check_interval = None
        return idle_timeout, idle_check_interval

    def _configure_server_idle_timeout(self) -> None:
        """``PUT /api/v1/timeout`` 把 ``launcher_config`` 上的空闲驱逐配置写回 jiuwenbox.

        该接口影响的是 jiuwenbox 全局根 policy (``SandboxManager.policy.timeout``),
        因此每次首创建沙箱前调用一次即可让本进程后续所有沙箱共享同一份空闲淘汰
        策略。 两个值都为 ``None`` 时 no-op (不动 server 默认状态)。

        去重: 用类级 ``_idle_timeout_cache`` 按 ``base_url`` 记录上一次成功写入
        的二元组, 命中即跳过本次 PUT。 多个 provider 实例 (fs / shell / code)
        同进程并存时, 只有第一次需要真发请求, 后续都走缓存; 服务端虽然也做了
        幂等短路 (``update_timeout_policy`` 比对 ``new_timeout == self.policy.
        timeout``), 但能省一次 HTTP RTT 总归更好。

        失败仅记 warning 且不写缓存, 不抛 —— 主动空闲淘汰只是辅助 hygiene, 不应
        让一次配置写失败把整个沙箱创建链路一起拖垮 (用户至少要能跑起来 sandbox);
        失败不写缓存是为了下一次 ``_get_sandbox_id`` 还能再 retry 一遍。
        """
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
        """把 ``shared_key -> sandbox_id`` 写入跨实例共享缓存。

        ``force_recreate_jiuwenbox_sandbox`` 在新建沙箱成功后通过本方法把新
        ID 写回 ``_shared_sandbox_ids``, 让同一进程里其他 provider 实例 (fs /
        shell / code) 的下一次 ``_get_sandbox_id`` 直接命中新 ID, 不需要再走
        ``create_sandbox`` 网络往返。 内部已加 ``_shared_lock`` 保护并发写,
        外部不必再持锁。

        把这一步封装成公共 classmethod 而不是让外部直接 ``with cls._shared_
        lock: cls._shared_sandbox_ids[k] = v`` 是为了:
        - 集中加锁逻辑, 防止将来扩展共享缓存语义 (如 LRU / TTL) 时漏改
          调用点; 
        - 满足 G.CLS.11: 类外不应访问 ``_shared_lock`` /
          ``_shared_sandbox_ids`` 这类受保护成员。
        """
        with cls._shared_lock:
            cls._shared_sandbox_ids[shared_key] = sandbox_id

    @classmethod
    def clear_shared_sandbox(cls, base_url: str) -> list[str]:
        """从进程内 ``_shared_sandbox_ids`` 移除 ``base_url`` 下的所有缓存项。

        Returns:
            被移除的 sandbox_id 列表 (按移除顺序), 去重后保留——``force_recreate_
            jiuwenbox_sandbox`` 拿到这份列表后会对 jiuwenbox server 主动发
            ``DELETE /api/v1/sandboxes/{id}`` 把旧 sandbox 回收掉, 避免每次
            ``/sandbox files allow|deny`` 都让服务端的活跃 sandbox 数 +1。

            历史调用方 (测试 fixture 等) 没有给返回值赋值, 这里保留 list[str]
            类型不会破坏它们: Python 调用端丢弃返回值是合法的。
        """
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
        # 关键: 每次都重新读 launcher.extra_params["sandbox_id"]。
        # ``force_recreate_jiuwenbox_sandbox`` (热更新路径) 仅 mutate
        # ``extra_params``、不重建 provider 实例; 如果这里只在 ``None`` 时读,
        # 老 sandbox_id 会一直锁在 ``self._sandbox_id`` 上, 直到 jiuwenbox
        # 把它 DELETE 掉之后, 后续 exec 会全部撞 "Sandbox not found"。
        extra_sandbox_id = self._sandbox_id_from_launcher_extra_params()
        if extra_sandbox_id and extra_sandbox_id != self._sandbox_id:
            self._sandbox_id = extra_sandbox_id
        if self._sandbox_id is None:
            endpoint_sandbox_id = getattr(self.endpoint, "sandbox_id", None)
            if isinstance(endpoint_sandbox_id, str) and endpoint_sandbox_id:
                self._sandbox_id = endpoint_sandbox_id
        if self._sandbox_id is None:
            shared_key = self._shared_scope_key()
            with self._shared_lock:
                self._sandbox_id = self._shared_sandbox_ids.get(shared_key)
                newly_created = False
                if self._sandbox_id is None:
                    # 先把 idle reaper 配置 (``idle_ttl_seconds`` /
                    # ``idle_check_interval``) 写到 server 根 policy, 再建沙箱。
                    # jiuwenbox 的 reaper 只看根 policy, per-sandbox policy
                    # 的 timeout 字段不生效, 因此必须显式 ``PUT /timeout``。
                    self._configure_server_idle_timeout()
                    self._sandbox_id = self._get_client().create_sandbox(
                        **self._sandbox_create_options_from_launcher_extra_params(),
                    )
                    newly_created = True
                self._shared_sandbox_ids[shared_key] = self._sandbox_id
                self._launcher_extra_params(create=True)["sandbox_id"] = self._sandbox_id
            # 仅当本进程刚创建沙箱时, 同步上传 preserve_files (copy 模式)。
            if newly_created:
                _upload_preserve_files_best_effort(
                    self._get_client(),
                    self._sandbox_id,
                    self._launcher_extra_params().get("preserve_files_upload"),
                )
        else:
            shared_key = self._shared_scope_key()
            with self._shared_lock:
                self._shared_sandbox_ids[shared_key] = self._sandbox_id
                self._launcher_extra_params(create=True)["sandbox_id"] = self._sandbox_id
        return self._sandbox_id

    @classmethod
    def _get_recreate_lock(cls) -> asyncio.Lock:
        """Lazy-init 的类级 ``asyncio.Lock``; 用 threading.Lock 兜底首次访问竞争.

        ``asyncio.Lock`` 必须在有 running event loop 时构造 (旧版 Python 行为);
        而首次访问可能并发于多个线程 (fs / shell / code provider 同进程内被不同
        协程调度), 因此用 ``_recreate_lock_init`` 作为 fast-double-checked
        sentinel, 确保只 new 一个 ``asyncio.Lock``。
        """
        if cls._recreate_lock is None:
            with cls._recreate_lock_init:
                if cls._recreate_lock is None:
                    cls._recreate_lock = asyncio.Lock()
        return cls._recreate_lock

    async def _execute_with_sandbox_retry(self, op: Callable[[str], _T]) -> _T:
        """跑 ``op(sandbox_id)``; 若命中「沙箱不存在」404, 最多循环 N 次 (env 覆盖, 默认 3)
        重建 + 重试, 每轮 ``sleep(1s)``。

        循环语义:
        - 总尝试次数 = ``1 (initial) + max_retries`` (initial 在 attempt=0 直接跑;
          后续 attempt=1..N 每次先 ``sleep`` 再 ``_recreate_sandbox_after_loss``,
          然后用新 id 再跑一次 ``op``)。
        - 重建本身抛异常 (网络挂了 / server 还没起来) → 记 warning 后 continue
          进入下一轮重试; 用尽 N 次仍 404 时, 抛出最后一次的 ``HTTPStatusError``。
        - 非沙箱 404 (例如 ``File not found: /foo``) 立刻 reraise, 不进入重试分支。
        - ``N=0`` 退化为「跑一次, 不重试」。
        """
        max_retries = _resolve_recreate_retries()
        last_exc: Optional[httpx.HTTPStatusError] = None
        stale_sandbox_id = self._get_sandbox_id()
        for attempt in range(max_retries + 1):
            if attempt == 0:
                sandbox_id = stale_sandbox_id
            else:
                await asyncio.sleep(_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS)
                logger.info(
                    "[jiuwenbox] sandbox-not-found 自动重建 attempt %d/%d (stale=%s)",
                    attempt, max_retries, stale_sandbox_id,
                )
                try:
                    sandbox_id = await self._recreate_sandbox_after_loss(
                        stale_sandbox_id=stale_sandbox_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    # 重建本身失败 (网络挂了 / server 还没起来): 记 warning, 进
                    # 入下一轮; 不直接抛, 因为下一轮 sleep 后服务可能已恢复。
                    logger.warning(
                        "[jiuwenbox] 重建沙箱失败 (attempt %d/%d): %s",
                        attempt, max_retries, exc,
                    )
                    continue
            try:
                return await asyncio.to_thread(op, sandbox_id)
            except httpx.HTTPStatusError as exc:
                if not _is_sandbox_not_found_error(exc):
                    raise
                last_exc = exc
                # 把当前刚拿到的 id 也作为 stale, 下一轮 force_recreate 时一起 DELETE
                stale_sandbox_id = sandbox_id
                logger.warning(
                    "[jiuwenbox] sandbox %s 不存在 (attempt %d/%d)",
                    sandbox_id, attempt, max_retries,
                )
        raise last_exc

    async def _recreate_sandbox_after_loss(self, *, stale_sandbox_id: str) -> str:
        """串行化重建沙箱, 并把新 id 写回 ``launcher.extra_params`` + ``self._sandbox_id``.

        双检逻辑: 进锁后先看 ``launcher.extra_params["sandbox_id"]`` 与
        ``_shared_sandbox_ids[shared_key]`` 是否已被其他协程更新到非 stale id;
        若已更新, 直接复用, 避免重复 ``create_sandbox`` HTTP 调用。
        """
        base_url = _endpoint_value(self.endpoint, self.config, "base_url")
        if not base_url:
            raise ValueError("jiuwenbox provider requires endpoint.base_url")
        create_options = self._sandbox_create_options_from_launcher_extra_params()
        extra_params = self._launcher_extra_params()
        preserve_files_upload = (
            extra_params.get("preserve_files_upload") if isinstance(extra_params, dict) else None
        )

        async with self._get_recreate_lock():
            # 双检 1: launcher.extra_params 是不是已经被另一个协程更新过了
            current = self._sandbox_id_from_launcher_extra_params()
            shared_key = self._shared_scope_key()
            # 双检 2: shared cache 是不是已经有新 id 了
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
            )
            # force_recreate 内部已 ``register_shared_sandbox_id`` + 删旧, 这里
            # 只需同步本实例的 ``_sandbox_id`` 与 launcher.extra_params。
            self._sandbox_id = new_id
            self._launcher_extra_params(create=True)["sandbox_id"] = new_id
            return new_id


def clear_jiuwenbox_shared_sandbox(base_url: str) -> list[str]:
    """Pop ``base_url`` 下的全部 sandbox cache; 返回被 pop 的 sandbox_id 列表。

    返回值是给 ``force_recreate_jiuwenbox_sandbox`` 走"先清缓存、后回收远端
    sandbox"的链路用的; 历史调用方 (测试 fixture / 失败兜底) 不依赖返回值,
    可以继续无感丢弃。
    """
    return _JiuwenBoxProviderMixin.clear_shared_sandbox(base_url)


def _iter_host_files_for_upload(
    upload_entries: Any,
) -> List[Tuple[str, str]]:
    """根据 ``preserve_files_upload`` 展开 host 实际可上传的 (host_path, sandbox_path) 对.

    - ``kind == "file"``: 仅当 host 文件存在时返回。
    - ``kind == "directory"``: 递归枚举目录下所有常规文件; sandbox_path 按相对
      路径拼接。子条目缺失静默跳过。
    - 非法/空条目静默跳过。
    """
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
    """异步上传 ``preserve_files_upload`` 列表到指定 sandbox.

    Returns:
        实际成功上传的文件数量 (失败的项会记 warning, 不抛异常)。
    """
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
    """同步版 ``_upload_preserve_files`` (在非 async 上下文调用)."""
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


def _upload_preserve_files_best_effort(
    client: "_JiuwenBoxClient",
    sandbox_id: str,
    upload_entries: Any,
) -> int:
    """根据当前是否处于事件循环线程, 选择同步/异步路径上传 preserve_files.

    在 provider exec 路径下 ``_get_sandbox_id`` 通常通过 ``asyncio.to_thread``
    被调用 (非事件循环线程), 因此直接走 sync upload; 若错误判定也降级 sync。
    """
    if not upload_entries:
        return 0
    try:
        # 试一下当前线程是否绑定事件循环
        asyncio.get_running_loop()
        running_in_loop = True
    except RuntimeError:
        running_in_loop = False
    if running_in_loop:
        # 在 loop 中 (例如 async test); 启动任务但不阻塞
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
) -> str:
    """清除指定 ``base_url`` 的 sandbox_id 缓存, 并立即在远端创建新 sandbox 实例.

    用于 ``/sandbox files allow/deny`` 即时生效场景: 文件 policy 变更后, 立刻向
    jiuwenbox 服务发起 ``create_sandbox`` 并把新 ID 写回共享缓存, 这样 provider
    下次 exec 时直接复用新实例, 无需先发一次会失败的 exec 才触发重建.

    Args:
        base_url: jiuwenbox 服务 base url.
        policy / policy_mode: 新 sandbox 的安全策略.
        timeout_seconds: HTTP 客户端超时.
        preserve_files_upload: ``copy`` 模式下需要在新 sandbox 中重新上传的固有
            文件/目录列表 (``[{host_path, sandbox_path, kind}, ...]``); ``mount``
            模式下传 ``None`` 或 ``[]``。
        extra_stale_sandbox_ids: 额外需要 best-effort 删除的旧 sandbox_id 列表;
            会和 ``clear_jiuwenbox_shared_sandbox(base_url)`` 的结果合并去重后
            一起送进 delete-stale 循环。 retry 路径 (provider op 撞 "Sandbox
            not found" 时) 拿到的 stale ``_sandbox_id`` 不一定还留在
            ``_shared_sandbox_ids`` 里 (例如来自 ``launcher.extra_params``,
            或 cache 早就被另一次 force_recreate 清空过); 通过这个参数显式
            把它捎进来, 保证 server 端的活跃 sandbox 计数会被回收。

    Returns:
        新创建的 sandbox_id; 调用方应回写到 ``launcher_config.extra_params["sandbox_id"]``.
    """
    # 先把进程内 shared cache 里旧 sandbox_id pop 出来 —— 这一刻 jiuwenclaw 已
    # 不再"认识"它们, 但 jiuwenbox server 那一侧还是活跃 sandbox。 没有这一步,
    # 后面 ``_get_sandbox_id`` 命中残留 cache 会重用旧 ID, 新 policy 落不下来;
    # 但仅 pop 不够, 还需主动回收 server-side sandbox, 不然每次
    # ``/sandbox files allow|deny`` 都让服务端的活跃 sandbox 数 +1
    # (jiuwenbox 端**没有** idle-TTL 自动清理: ``sandbox_manager`` 里把
    # registry 视为 ephemeral across restarts, 只在进程重启时才丢弃描述符)。
    stale_sandbox_ids = clear_jiuwenbox_shared_sandbox(base_url)
    # 合并调用方显式声明的额外 stale id (例如 retry 路径上从 op 抛出来的 404
    # 对应的那个 sandbox_id), 按出现顺序去重——shared cache 里的优先, 调用方
    # 的追加。 这样既保证 cache 那批一定走到, 又不重复 DELETE 同一个 ID。
    if extra_stale_sandbox_ids:
        seen = set(stale_sandbox_ids)
        for extra in extra_stale_sandbox_ids:
            if isinstance(extra, str) and extra and extra not in seen:
                stale_sandbox_ids.append(extra)
                seen.add(extra)
    with _JiuwenBoxClient(base_url=base_url, timeout_seconds=timeout_seconds) as client:
        sandbox_id = await asyncio.to_thread(
            client.create_sandbox, policy=policy, policy_mode=policy_mode,
        )
        if preserve_files_upload:
            await _upload_preserve_files(client, sandbox_id, preserve_files_upload)
        # 新 sandbox 已经就绪(且若需上传 preserve files 也已经上传完毕)。 此时
        # 再去 best-effort 回收旧 sandbox, 这样即便 DELETE 失败也不会影响主
        # 流程返回新 sandbox_id。 顺序选择"先 create 后 delete"是因为:
        # 1) create 失败的概率远小于 delete 失败(后者要走网络且服务端可能正
        #    在 reaping); create 失败时 stale 沙箱保留, 用户至少看得到明确错;
        # 2) 反过来"先 delete 后 create"若 create 失败用户彻底没沙箱, 体验更差。
        for old_id in stale_sandbox_ids:
            if old_id == sandbox_id:
                # 防御性: 理论上不会发生(刚 pop 走的 ID 不应等于新 create 出来
                # 的随机 ID), 但万一两个进程并发把同一 ID 又写回 cache, 跳过
                # 删除避免误回收当前活动 sandbox。
                continue
            try:
                await asyncio.to_thread(client.delete_sandbox, old_id)
                logger.info(
                    "[jiuwenbox] force_recreate_jiuwenbox_sandbox: "
                    "deleted stale sandbox %s", old_id,
                )
            except Exception as exc:  # noqa: BLE001
                # 404 已经在 ``_JiuwenBoxClient.delete_sandbox`` 内部被吞,
                # 进入这条分支的多半是连接错误 / 5xx / 超时。 服务端会继续持
                # 有 stale sandbox 直到下次 jiuwenbox 重启, 但新 sandbox 已经
                # 上线, 不应当当致命错误向上抛, 仅 warn 让 operator 在日志里
                # 可见。
                logger.warning(
                    "[jiuwenbox] force_recreate_jiuwenbox_sandbox: "
                    "stale sandbox %s cleanup failed: %s", old_id, exc,
                )

    shared_key = base_url.rstrip("/")
    create_options: dict[str, Any] = {}
    if policy is not None:
        create_options["policy"] = policy
    if policy_mode is not None:
        create_options["policy_mode"] = policy_mode
    if create_options:
        options_key = json.dumps(create_options, sort_keys=True, separators=(",", ":"))
        shared_key = f"{shared_key}|{options_key}"
    _JiuwenBoxProviderMixin.register_shared_sandbox_id(shared_key, sandbox_id)
    logger.info(
        "[jiuwenbox] force_recreate_jiuwenbox_sandbox: new sandbox_id=%s for %s",
        sandbox_id,
        base_url,
    )
    return sandbox_id


def _decode_subprocess_stream(value: Any) -> str:
    """把 ``subprocess`` 返回 / 异常携带的 stdout / stderr 统一成 ``str``.

    ``subprocess.run(text=True)`` 正常情况下产出 ``str`` (``None`` 表示没有
    捕获), 但是 ``subprocess.TimeoutExpired`` 异常上挂的 ``stdout`` /
    ``stderr`` 文档明确说: 即使 ``text=True`` 也"可能是 ``bytes`` 或 ``None``"
    (CPython issue #16962 / docs)。 把这段三态归一成"始终 ``str``"在
    ``_run_local_subprocess`` 的 ``timeout`` 分支里要写两次, 直接复制粘贴会让
    那两行超过 120 列 (G.FMT.02); 抽出辅助函数同时收敛"被吃掉的 surrogate
    /非 UTF-8 字节用 ``replace`` 容错解码"的策略, 避免哪天误用 ``strict``
    把 timeout 路径再次踢回更难定位的解码异常上去。
    """
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
    """以本地 subprocess 形式运行 ``argv``, 返回与 jiuwenbox exec 同形结构.

    用于两层本地降级:
    - 预先路由 (``excluded_commands`` 命中) 跳过沙箱, 直接本地执行;
    - 沙箱执行失败且 ``fallback_on_failure=True`` 时降级本地.

    Returns:
        ``{"stdout": str, "stderr": str, "exit_code": int, "local": True}`` 字典.
    """
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
    """从 ``launcher_config.extra_params`` 读出 exclude 列表.

    读取 ``excluded_commands`` 字段; 返回 ``None`` 表示没有配置该列表。
    """
    if not isinstance(extra, dict):
        return None
    raw = extra.get("excluded_commands")
    if isinstance(raw, list):
        return raw
    return None


def _command_matches_exclude(command: str, patterns: list[str] | None) -> bool:
    """以 ``fnmatch`` 判定命令是否匹配任一 exclude pattern.

    匹配规则:
    - 命令首个 token 整体匹配 (例: ``git status`` 对 ``git *`` 命中);
    - 整个命令字符串匹配 (例: ``ls -la`` 对 ``ls *`` 命中).
    """
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

        # (a) 预先路由: 命中 excluded_commands 直接本地执行, 不发请求到沙箱
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

        try:
            result = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().exec(
                    sid,
                    ["bash", "-lc", command],
                    cwd=workdir,
                    timeout=exec_timeout,
                    environment=environment,
                )
            )
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
            # (b) 失败回退: 沙箱返回 exit_code != 0 且 fallback 开启时降级本地
            if exit_code != 0 and fallback_on_failure:
                logger.info(
                    "[jiuwenbox] sandbox exit_code=%d, falling back to local exec: %s",
                    exit_code,
                    command,
                )
                local_result = await _run_local_subprocess(
                    ["bash", "-lc", command],
                    cwd=workdir,
                    env=environment,
                    timeout=exec_timeout,
                )
                return self._wrap_shell_local_result(command, cwd, timeout, local_result)
            return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)
        except Exception as exc:
            # (b) 失败回退: 沙箱抛异常 (HTTP / 连接错误) 且 fallback 开启时降级本地
            if fallback_on_failure:
                logger.info(
                    "[jiuwenbox] sandbox raised %s, falling back to local exec: %s",
                    type(exc).__name__,
                    command,
                )
                local_result = await _run_local_subprocess(
                    ["bash", "-lc", command],
                    cwd=workdir,
                    env=environment,
                    timeout=exec_timeout,
                )
                return self._wrap_shell_local_result(command, cwd, timeout, local_result)
            return _build_shell_error_result("execute_cmd", str(exc), ExecuteCmdResult)

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

        # (a) 预先路由: code 的首行命中 exclude pattern 则直接本地执行
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

        try:
            result = await self._execute_with_sandbox_retry(
                lambda sid: self._get_client().exec(
                    sid,
                    command,
                    cwd="/tmp",
                    timeout=exec_timeout,
                    environment=merged_env,
                )
            )
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
            # (b) 失败回退: 沙箱返回 exit_code != 0 且 fallback 开启时降级本地
            if result_data.exit_code != 0 and fallback_on_failure:
                logger.info(
                    "[jiuwenbox] sandbox code exit_code=%d, falling back to local exec",
                    result_data.exit_code,
                )
                local_result = await _run_local_subprocess(
                    command,
                    cwd="/tmp",
                    env=merged_env,
                    timeout=exec_timeout,
                )
                return self._wrap_code_local_result(code, language, timeout, local_result)
            return ExecuteCodeResult(code=StatusCode.SUCCESS.code,
                                     message="Code executed successfully", data=result_data)
        except Exception as exc:
            # (b) 失败回退: 沙箱抛异常时降级本地
            if fallback_on_failure:
                logger.info(
                    "[jiuwenbox] sandbox code raised %s, falling back to local exec",
                    type(exc).__name__,
                )
                local_result = await _run_local_subprocess(
                    command,
                    cwd="/tmp",
                    env=merged_env,
                    timeout=exec_timeout,
                )
                return self._wrap_code_local_result(code, language, timeout, local_result)
            return _build_code_error_result("execute_code", str(exc), ExecuteCodeResult, data=data)

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
