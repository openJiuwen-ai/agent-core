# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""YuanRong sandbox providers (shell / code / fs).

Communication uses the ``yr`` actor SDK (see ``yr_image/test.py``):

- ``executor=default`` → ``yr.sandbox.Sandbox()``
- ``executor=docker`` → ``SandboxInstance`` with ``custom_extensions`` rootfs

FS APIs map to ``Sandbox`` / ``SandboxInstance`` ``read_file`` / ``write_file`` /
``list_files`` / ``search_files`` (native Python I/O inside the sandbox).

``PreDeployLauncherConfig.base_url`` is required by the launcher type but unused
by the SDK path; cluster address comes from YuanRong env (e.g. ``YR_SERVER_ADDRESS``).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import threading
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Dict, List, Literal, Optional, Tuple

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

logger = logging.getLogger(__name__)

DEFAULT_DOCKER_IMAGE = "yr-docker-runtime:v0"
DEFAULT_DOCKER_WORKDIR = "/tmp/test"
DEFAULT_DOCKER_MOUNTS = [{"source": "/tmp", "target": "/tmp/test", "readonly": True}]


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


def _item_from_payload(item: dict[str, Any]) -> FileSystemItem:
    return FileSystemItem(
        name=item.get("name", ""),
        path=item.get("path", ""),
        size=item.get("size") or 0,
        is_directory=bool(item.get("is_directory", False)),
        modified_time=item.get("modified_time") or "0",
        type=item.get("type"),
    )


def _normalize_list_items(raw: Any) -> List[dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("items", [])
        return items if isinstance(items, list) else []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _coerce_bytes(value: Any) -> bytes:
    """Normalize SDK binary payloads (bytes / bytearray / memoryview) to bytes."""
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


def build_yuanrong_shared_scope_key(base_url: str, isolation_key: str) -> str:
    """Build the provider shared-cache key for a base_url + isolation_key pair."""
    return f"{str(base_url).rstrip('/')}|{isolation_key}"


def _build_wrapped_command(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    environment: Optional[Dict[str, str]] = None,
) -> str:
    inner_parts: List[str] = []
    if cwd and cwd != ".":
        inner_parts.append(f"cd {_quote_shell_value(cwd)}")
    if environment:
        env_prefix = " ".join(f"{key}={_quote_shell_value(value)}" for key, value in environment.items())
        inner_parts.append(f"export {env_prefix}")
    inner_parts.append(command)
    inner_command = " && ".join(inner_parts)
    shell_command = f"/bin/sh -c {_quote_shell_value(inner_command)}"
    if timeout is not None and timeout > 0:
        shell_command = f"timeout {int(timeout)}s {shell_command}"
    return shell_command


class _YuanrongProviderMixin:
    """Shared YuanRong instance lifecycle for shell/code/fs providers."""

    _yr_init_lock: ClassVar[threading.Lock] = threading.Lock()
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()
    # shared_key -> (instance, executor_mode) where instance is Sandbox or SandboxInstance
    _shared_instances: ClassVar[Dict[str, Tuple[Any, str]]] = {}

    _instance: Any
    _executor: str
    _timeout_seconds: int

    def _init_yuanrong(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig]) -> None:
        self._instance = None
        self._timeout_seconds = int(getattr(config, "timeout_seconds", 30) or 30)
        extra = self._launcher_extra_params()
        executor = str(extra.get("executor") or "default").strip().lower()
        if executor not in {"default", "docker"}:
            executor = "default"
        self._executor = executor

    def _launcher_extra_params(self) -> dict[str, Any]:
        launcher_config = getattr(self.config, "launcher_config", None) if self.config is not None else None
        if launcher_config is None:
            return {}
        extra_params = getattr(launcher_config, "extra_params", None)
        return extra_params if isinstance(extra_params, dict) else {}

    def _shared_scope_key(self) -> str:
        base_url = _endpoint_value(self.endpoint, self.config, "base_url") or "yuanrong"
        parts = [str(base_url).rstrip("/")]
        isolation_key = getattr(self.endpoint, "isolation_key", None)
        if isinstance(isolation_key, str) and isolation_key:
            parts.append(isolation_key)
        return "|".join(parts)

    @classmethod
    def _ensure_yr_init(cls) -> Any:
        """Initialize YuanRong once per process, keyed off SDK ``is_initialized()``.

        A local ClassVar is insufficient: other code (or a prior test) may have
        already called ``yr.init()``, and ``yr.finalize()`` clears SDK state
        without updating our flag. Always consult the SDK.
        """
        import yr

        with cls._yr_init_lock:
            if not yr.is_initialized():
                yr.init()
        return yr

    def _docker_invoke_options(self, yr: Any) -> Any:
        extra = self._launcher_extra_params()
        rootfs = extra.get("rootfs")
        if isinstance(rootfs, dict):
            rootfs_payload = dict(rootfs)
        else:
            image = extra.get("image") or DEFAULT_DOCKER_IMAGE
            workdir = extra.get("workdir") or DEFAULT_DOCKER_WORKDIR
            mounts = extra.get("mounts") if isinstance(extra.get("mounts"), list) else DEFAULT_DOCKER_MOUNTS
            rootfs_payload = {
                "type": "image",
                "imageurl": image,
                "workdir": workdir,
                "mounts": mounts,
            }
            if "imageurl" not in rootfs_payload and "image" in rootfs_payload:
                rootfs_payload["imageurl"] = rootfs_payload.pop("image")

        opt = yr.InvokeOptions()
        opt.skip_serialize = True
        opt.custom_extensions["sandbox_type"] = "docker"
        opt.custom_extensions["rootfs"] = json.dumps(rootfs_payload)
        opt.cpu = int(extra.get("cpu", 1000))
        opt.cpu_limit = int(extra.get("cpu_limit", 2000))
        opt.memory = int(extra.get("memory", 512))
        opt.mem_limit = int(extra.get("mem_limit", 1024))
        return opt

    def _create_instance(self) -> Tuple[Any, str]:
        yr = self._ensure_yr_init()
        if self._executor == "docker":
            opt = self._docker_invoke_options(yr)
            inst = yr.sandbox.SandboxInstance.options(opt).invoke()
            return inst, "docker"
        sb = yr.sandbox.Sandbox()
        return sb, "default"

    def _get_instance(self) -> Tuple[Any, str]:
        if self._instance is not None:
            return self._instance, self._executor

        shared_key = self._shared_scope_key()
        with self._shared_lock:
            cached = self._shared_instances.get(shared_key)
            if cached is not None:
                self._instance, self._executor = cached
                return self._instance, self._executor
            instance, executor = self._create_instance()
            self._instance = instance
            self._executor = executor
            self._shared_instances[shared_key] = (instance, executor)
            logger.info(
                "[yuanrong] created sandbox executor=%s shared_key=%s",
                executor,
                shared_key,
            )
            return instance, executor

    @classmethod
    def pop_cached_instance(cls, shared_key: str) -> Optional[Tuple[Any, str]]:
        with cls._shared_lock:
            return cls._shared_instances.pop(shared_key, None)

    @classmethod
    def clear_shared_for_base_url(cls, base_url: str) -> List[Tuple[str, Any, str]]:
        prefix = f"{str(base_url).rstrip('/')}|"
        removed: List[Tuple[str, Any, str]] = []
        with cls._shared_lock:
            keys = [k for k in cls._shared_instances if k == str(base_url).rstrip("/") or k.startswith(prefix)]
            for key in keys:
                inst, mode = cls._shared_instances.pop(key)
                removed.append((key, inst, mode))
        return removed

    def _exec_sync(self, command: str) -> Dict[str, Any]:
        import yr

        instance, executor = self._get_instance()
        if executor == "docker":
            result = yr.get(instance.execute.invoke(command))
        else:
            # Sandbox.exec already resolves ObjectRef in current SDK; tolerate both.
            result = instance.exec(command)
            if not isinstance(result, dict):
                result = yr.get(result)
        if not isinstance(result, dict):
            return {"returncode": -1, "stdout": "", "stderr": f"unexpected result type: {type(result)!r}"}
        return result

    async def _run_command(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        wrapped = _build_wrapped_command(
            command,
            cwd=cwd,
            timeout=timeout,
            environment=environment,
        )
        return await asyncio.to_thread(self._exec_sync, wrapped)

    def _fs_read_sync(self, path: str, *, mode: str = "rb") -> Any:
        import yr

        instance, executor = self._get_instance()
        if executor == "docker":
            return yr.get(instance.read_file.invoke(path, mode=mode))
        return instance.read_file(path, mode=mode)

    def _fs_write_sync(self, path: str, data: Any, *, mode: str = "wb") -> None:
        import yr

        instance, executor = self._get_instance()
        if executor == "docker":
            yr.get(instance.write_file.invoke(path, data, mode=mode))
            return
        instance.write_file(path, data, mode=mode)

    def _fs_list_sync(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_depth: Optional[int] = None,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> List[dict[str, Any]]:
        import yr

        instance, executor = self._get_instance()
        if executor == "docker":
            raw = yr.get(
                instance.list_files.invoke(
                    path,
                    recursive=recursive,
                    max_depth=max_depth,
                    include_files=include_files,
                    include_dirs=include_dirs,
                )
            )
        else:
            raw = instance.list_files(
                path,
                recursive=recursive,
                max_depth=max_depth,
                include_files=include_files,
                include_dirs=include_dirs,
            )
        return _normalize_list_items(raw)

    def _fs_search_sync(
        self,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[dict[str, Any]]:
        import yr

        instance, executor = self._get_instance()
        if executor == "docker":
            raw = yr.get(
                instance.search_files.invoke(
                    path,
                    pattern,
                    exclude_patterns=exclude_patterns,
                )
            )
        else:
            raw = instance.search_files(path, pattern, exclude_patterns=exclude_patterns)
        return _normalize_list_items(raw)

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


def _terminate_instance(instance: Any, executor: str) -> None:
    try:
        import yr

        if executor == "docker":
            try:
                yr.get(instance.cleanup.invoke())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[yuanrong] cleanup.invoke failed: %s", exc)
        else:
            try:
                cleanup_ref = instance.cleanup()
                if not isinstance(cleanup_ref, dict) and cleanup_ref is not None:
                    yr.get(cleanup_ref)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[yuanrong] cleanup failed: %s", exc)
        try:
            instance.terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[yuanrong] terminate failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[yuanrong] terminate_instance failed: %s", exc)


async def delete_yuanrong_sandbox(
    *,
    shared_key: Optional[str] = None,
    base_url: Optional[str] = None,
    reason: str = "teardown",
) -> list[str]:
    """Delete cached YuanRong sandbox instance(s) on sysoperation teardown."""
    entries: List[Tuple[str, Any, str]] = []
    if shared_key:
        popped = _YuanrongProviderMixin.pop_cached_instance(shared_key)
        if popped is not None:
            entries.append((shared_key, popped[0], popped[1]))
    elif base_url:
        entries.extend(_YuanrongProviderMixin.clear_shared_for_base_url(base_url))

    deleted: list[str] = []
    for key, instance, executor in entries:
        await asyncio.to_thread(_terminate_instance, instance, executor)
        deleted.append(key)
        logger.info("[yuanrong] deleted sandbox shared_key=%s reason=%s", key, reason)
    return deleted


@SandboxRegistry.provider("yuanrong", "fs")
class YuanrongFSProvider(_YuanrongProviderMixin, BaseFSProvider):
    """YuanRong FS provider via Sandbox / SandboxInstance native file APIs."""

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
        del create_parent_dirs, preserve_permissions, chunk_size  # parents created by SDK write_file
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

        exit_code = int(result.get("returncode", -1))
        data = ExecuteCmdData(
            command=command,
            cwd=cwd or ".",
            stdout=result.get("stdout") or "",
            stderr=result.get("stderr") or "",
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
