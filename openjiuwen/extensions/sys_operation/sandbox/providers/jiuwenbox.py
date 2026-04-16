# coding: utf-8
from __future__ import annotations

import asyncio
import base64
import os
import shlex
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

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


STDERR_MARKER = "__OJW_STDERR__:"
DEFAULT_SANDBOX_COMMAND = ["/usr/bin/python3", "-c", "import time; time.sleep(3600)"]


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


def _split_marked_shell_output(output: str) -> Tuple[str, str]:
    stdout_parts: List[str] = []
    stderr_parts: List[str] = []
    cursor = 0
    while cursor < len(output):
        marker_index = output.find(STDERR_MARKER, cursor)
        if marker_index < 0:
            stdout_parts.append(output[cursor:])
            break
        stdout_parts.append(output[cursor:marker_index])
        stderr_start = marker_index + len(STDERR_MARKER)
        stderr_end = output.find("\n", stderr_start)
        if stderr_end < 0:
            stderr_parts.append(output[stderr_start:])
            break
        stderr_parts.append(output[stderr_start:stderr_end + 1])
        cursor = stderr_end + 1
    return "".join(stdout_parts), "".join(stderr_parts)


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


class _JiuwenBoxClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def create_sandbox(self) -> str:
        response = self._client.post("/api/v1/sandboxes", json={"command": DEFAULT_SANDBOX_COMMAND})
        response.raise_for_status()
        return response.json()["id"]

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
        body: dict[str, Any] = {
            "command": command,
            "workdir": cwd,
            "env": environment,
            "stdin": stdin,
            "timeout_seconds": timeout,
        }
        body = {key: value for key, value in body.items() if value is not None}
        response = self._client.post(
            f"/api/v1/sandboxes/{sandbox_id}/exec",
            json=body,
            timeout=max(timeout or 30, 30),
        )
        response.raise_for_status()
        return dict(response.json())

    def upload_bytes(self, sandbox_id: str, sandbox_path: str, content: bytes) -> None:
        response = self._client.post(
            f"/api/v1/sandboxes/{sandbox_id}/upload",
            params={"sandbox_path": sandbox_path},
            files={"file": (Path(sandbox_path).name or "upload.bin", content)},
        )
        response.raise_for_status()

    def download_bytes(self, sandbox_id: str, sandbox_path: str) -> bytes:
        response = self._client.get(
            f"/api/v1/sandboxes/{sandbox_id}/download",
            params={"sandbox_path": sandbox_path},
        )
        response.raise_for_status()
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
        response.raise_for_status()
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
        response.raise_for_status()
        return list(response.json().get("items", []))


class _JiuwenBoxProviderMixin:
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

    def _get_sandbox_id(self) -> str:
        env_sandbox_id = os.environ.get("JIUWENBOX_SANDBOX_ID")
        if env_sandbox_id:
            self._sandbox_id = env_sandbox_id
        if self._sandbox_id is None:
            self._sandbox_id = self._get_client().create_sandbox()
        return self._sandbox_id


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
                try:
                    raw = await asyncio.to_thread(
                        self._get_client().download_bytes,
                        self._get_sandbox_id(),
                        path,
                    ) + raw
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
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

        result = await self.read_file(path, mode=mode, head=head, tail=tail, line_range=line_range, encoding=encoding)
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
                try:
                    await asyncio.to_thread(self._get_client().download_bytes, self._get_sandbox_id(), target_path)
                    raise FileExistsError(f"File already exists: {target_path}")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
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

    @staticmethod
    def _build_wrapped_command(
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> str:
        inner_parts: List[str] = []
        if cwd:
            inner_parts.append(f"cd {_quote_shell_value(cwd)}")
        if environment:
            env_prefix = " ".join(f"{key}={_quote_shell_value(value)}" for key, value in environment.items())
            inner_parts.append(f"export {env_prefix}")
        inner_parts.append(f"{{ {command}; }} 2> >(sed 's/^/{STDERR_MARKER}/')")
        bash_path = os.path.join(os.sep, "usr", "bin", "bash")
        shell_command = " ".join([bash_path, "-lc", _quote_shell_value(" && ".join(inner_parts))])
        if timeout is not None and timeout > 0:
            timeout_path = os.path.join(os.sep, "usr", "bin", "timeout")
            shell_command = " ".join([timeout_path, f"{int(timeout)}s", shell_command])
        return shell_command

    async def execute_cmd(
        self,
        command: str,
        cwd: str = ".",
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> ExecuteCmdResult:
        if not command or not command.strip():
            return _build_shell_error_result("execute_cmd", "command can not be empty", ExecuteCmdResult)
        wrapped = self._build_wrapped_command(command, cwd=cwd or ".", timeout=timeout, environment=environment)
        exec_timeout = timeout + 1 if timeout is not None and timeout > 0 else timeout
        try:
            result = await asyncio.to_thread(
                self._get_client().exec,
                self._get_sandbox_id(),
                ["/usr/bin/bash", "-lc", wrapped],
                timeout=exec_timeout,
            )
            stdout, stderr = _split_marked_shell_output(result.get("stdout") or "")
            exit_code = int(result.get("exit_code") or 0)
            data = ExecuteCmdData(command=command, cwd=cwd or ".", stdout=stdout, stderr=stderr, exit_code=exit_code)
            if exit_code == 124:
                return _build_shell_error_result(
                    "execute_cmd",
                    f"execution timeout after {timeout} seconds",
                    ExecuteCmdResult,
                    data=data,
                )
            return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)
        except Exception as exc:
            return _build_shell_error_result("execute_cmd", str(exc), ExecuteCmdResult)

    async def execute_cmd_stream(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        result = await self.execute_cmd(command, cwd=cwd or ".", timeout=timeout, environment=environment)
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
                return ["/usr/bin/bash", "-lc", (
                    "tmp=$(/usr/bin/mktemp /tmp/ojw_code_XXXXXX.py) && "
                    f"printf %s {_quote_shell_value(encoded)} | /usr/bin/base64 -d > \"$tmp\" && "
                    "/usr/bin/python3 \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )]
            return ["/usr/bin/python3", "-c", code]
        if language == "javascript":
            if force_file:
                return ["/usr/bin/bash", "-lc", (
                    "tmp=$(/usr/bin/mktemp /tmp/ojw_code_XXXXXX.js) && "
                    f"printf %s {_quote_shell_value(encoded)} | /usr/bin/base64 -d > \"$tmp\" && "
                    "/usr/bin/node \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )]
            return ["/usr/bin/node", "-e", code]
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
        try:
            result = await asyncio.to_thread(
                self._get_client().exec,
                self._get_sandbox_id(),
                command,
                cwd="/tmp",
                timeout=timeout,
                environment=self._prepare_code_environment(language, environment),
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
            return ExecuteCodeResult(code=StatusCode.SUCCESS.code,
                                     message="Code executed successfully", data=result_data)
        except Exception as exc:
            return _build_code_error_result("execute_code", str(exc), ExecuteCodeResult, data=data)

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
