# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import logging
import os
import shlex
import subprocess
import threading
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

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
            if exc.response.status_code == 404:
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
            pass

    def __enter__(self) -> "_JiuwenBoxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class _JiuwenBoxProviderMixin:
    _shared_lock = threading.Lock()
    _shared_sandbox_ids: Dict[str, str] = {}

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
        except Exception:  # noqa: BLE001
            pass
    return _upload_preserve_files_sync(client, sandbox_id, upload_entries)


async def force_recreate_jiuwenbox_sandbox(
    base_url: str,
    *,
    policy: dict | None = None,
    policy_mode: str | None = None,
    timeout_seconds: float = 30.0,
    preserve_files_upload: Any = None,
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

    优先读新键 ``excluded_commands``; 若不存在再读 legacy 键
    ``shell_exclude_patterns``, 以保证 provider 升级前/后都能识别 agent-server
    下发的字段。返回 None 表示没有配置 exclude 列表。
    """
    if not isinstance(extra, dict):
        return None
    raw = extra.get("excluded_commands")
    if raw is None:
        raw = extra.get("shell_exclude_patterns")
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
            raw = await asyncio.to_thread(self._get_client().download_bytes, self._get_sandbox_id(), path)
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
                await asyncio.to_thread(self._get_client().append_bytes, self._get_sandbox_id(), path, raw)
            else:
                await asyncio.to_thread(self._get_client().upload_bytes, self._get_sandbox_id(), path, raw)
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
            raw_items = await asyncio.to_thread(
                self._get_client().list_files,
                self._get_sandbox_id(),
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
            raw_items = await asyncio.to_thread(
                self._get_client().list_files,
                self._get_sandbox_id(),
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
                exists = await asyncio.to_thread(
                    self._get_client().path_exists,
                    self._get_sandbox_id(),
                    target_path,
                )
                if exists:
                    raise FileExistsError(f"File already exists: {target_path}")
            raw = Path(local_path).read_bytes()
            await asyncio.to_thread(self._get_client().upload_bytes, self._get_sandbox_id(), target_path, raw)
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
            raw = await asyncio.to_thread(self._get_client().download_bytes, self._get_sandbox_id(), source_path)
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
            raw_items = await asyncio.to_thread(
                self._get_client().search_files,
                self._get_sandbox_id(),
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
            result = await asyncio.to_thread(
                self._get_client().exec,
                self._get_sandbox_id(),
                ["bash", "-lc", command],
                cwd=workdir,
                timeout=exec_timeout,
                environment=environment,
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
            result = await asyncio.to_thread(
                self._get_client().exec,
                self._get_sandbox_id(),
                command,
                cwd="/tmp",
                timeout=exec_timeout,
                environment=merged_env,
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
