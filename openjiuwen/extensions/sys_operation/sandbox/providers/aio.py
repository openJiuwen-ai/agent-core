# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import base64
import asyncio
import fnmatch
import os
import shlex
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, AsyncIterator

from openjiuwen.core.sys_operation.result import (
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
    ExecuteCmdChunkData,
    ExecuteCodeData,
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
    ExecuteCodeChunkData,
    ReadFileResult,
    ReadFileData,
    ReadFileStreamResult,
    ReadFileChunkData,
    WriteFileResult,
    WriteFileData,
    ListFilesResult,
    ListDirsResult,
    FileSystemData,
    FileSystemItem,
    SearchFilesResult,
    SearchFilesData,
    UploadFileResult,
    UploadFileData,
    UploadFileStreamResult,
    UploadFileChunkData,
    DownloadFileResult,
    DownloadFileData,
    DownloadFileStreamResult,
    DownloadFileChunkData,
)
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import (
    BaseFSProvider,
    BaseShellProvider,
    BaseCodeProvider,
)
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result


def _is_retryable_error(msg: str) -> bool:
    """Check if the error message indicates a retryable 502/503 status code."""
    return "502" in msg or "503" in msg


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
    if mode == "bytes" and any(param is not None for param in (head, tail, line_range)):
        return "Parameters 'head', 'tail', and 'line_range' are only supported in text mode"

    specified = []
    if head is not None:
        specified.append("head")
    if tail is not None:
        specified.append("tail")
    if line_range is not None:
        specified.append("line_range")

    if len(specified) > 1:
        return f"{' and '.join(specified)} cannot be specified simultaneously"
    return None


def _split_text_lines(content: str) -> List[str]:
    return content.splitlines(keepends=True)


def _select_text_lines(
        content: str,
        *,
        head: Optional[int],
        tail: Optional[int],
        line_range: Optional[Tuple[int, int]],
) -> Tuple[List[str], bool]:
    lines = _split_text_lines(content)

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


def _quote_shell_value(value: str) -> str:
    return shlex.quote(value)


def _split_marked_shell_output(output: str) -> Tuple[str, str]:
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    for line in output.splitlines(keepends=True):
        if line.startswith("__OJW_STDERR__:"):
            stderr_lines.append(line.removeprefix("__OJW_STDERR__:"))
        else:
            stdout_lines.append(line)
    return "".join(stdout_lines), "".join(stderr_lines)


def _fs_sort_key_by_modified_time(item: FileSystemItem) -> Optional[str]:
    return item.modified_time


def _fs_sort_key_by_size(item: FileSystemItem) -> int:
    return item.size


def _fs_sort_key_by_name(item: FileSystemItem) -> str:
    return item.name


def _sort_fs_items(items: List[FileSystemItem], sort_by: str, sort_descending: bool) -> List[FileSystemItem]:
    if sort_by == "modified_time":
        key_fn = _fs_sort_key_by_modified_time
    elif sort_by == "size":
        key_fn = _fs_sort_key_by_size
    else:
        key_fn = _fs_sort_key_by_name
    return sorted(items, key=key_fn, reverse=sort_descending)


@SandboxRegistry.provider("aio", "fs")
class AIOFSProvider(BaseFSProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            if not self.endpoint.base_url:
                raise ValueError("AIO provider requires endpoint.base_url")
            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

    async def read_file(self, path: str, mode: str = "text", **kwargs) -> ReadFileResult:
        client = await asyncio.to_thread(self._get_client)

        tail = kwargs.pop('tail', None)
        head = kwargs.pop('head', None)
        line_range = kwargs.pop('line_range', None)
        head, tail, line_range = _normalize_read_params(head=head, tail=tail, line_range=line_range)
        validation_error = _validate_read_params(mode=mode, head=head, tail=tail, line_range=line_range)
        if validation_error:
            return _build_fs_error_result("read_file", validation_error, ReadFileResult)

        if tail is not None or head is not None or line_range is not None:
            return await self._read_file_via_shell(path, mode, tail=tail, head=head, line_range=line_range)

        if mode == "bytes":
            return await self._read_file_bytes(path=path, chunk_size=kwargs.get("chunk_size", 0))

        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _read():
                    res = client.file.read_file(file=path)
                    content = res.data.content
                    if mode == "bytes":
                        if isinstance(content, str):
                            content = base64.b64decode(content)
                    data = ReadFileData(path=path, content=content, mode=mode or "text")
                    return ReadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_read)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("read_file", msg, ReadFileResult)
        return _build_fs_error_result(
            "read_file",
            str(last or TimeoutError("aio read_file timeout")),
            ReadFileResult,
        )

    async def _read_file_bytes(self, path: str, chunk_size: int = 0) -> ReadFileResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _download():
                    return b"".join(client.file.download_file(path=path))

                content = await asyncio.to_thread(_download)
                if chunk_size and chunk_size > 0:
                    content = content[:chunk_size]
                return ReadFileResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileData(path=path, content=content, mode="bytes"),
                )
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("read_file", msg, ReadFileResult)
        return _build_fs_error_result(
            "read_file",
            str(last or TimeoutError("aio read_file timeout")),
            ReadFileResult,
        )

    async def _read_file_via_shell(self, path: str, mode: str = "text",
                                   tail: Optional[int] = None,
                                   head: Optional[int] = None,
                                   line_range: Optional[Tuple[int, int]] = None) -> ReadFileResult:
        """Read full file then apply local-style text slicing in Python."""
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _read():
                    res = client.file.read_file(file=path)
                    return res.data.content

                full_content = await asyncio.to_thread(_read)
                break
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("read_file", msg, ReadFileResult)
        else:
            return _build_fs_error_result(
                "read_file",
                str(last or TimeoutError("aio read_file timeout")),
                ReadFileResult,
            )

        lines, _ = _select_text_lines(full_content, head=head, tail=tail, line_range=line_range)
        return ReadFileResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=ReadFileData(path=path, content="".join(lines), mode=mode),
        )

    async def write_file(self, path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult:
        prepend_newline = kwargs.get("prepend_newline", True)
        append_newline = kwargs.get("append_newline", False)
        append = kwargs.get("append", False)
        if mode == "bytes":
            return await self._write_file_bytes(path=path, content=content, append=append)

        if isinstance(content, bytes):
            content_str = content.decode('utf-8')
            encoding = "utf-8"
        else:
            content_str = content
            encoding = "utf-8"
        if mode == "text":
            if prepend_newline:
                content_str = "\n" + content_str
            if append_newline:
                content_str = content_str + "\n"
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _write():
                    res = client.file.write_file(file=path, content=content_str, encoding=encoding, append=append)
                    size = len(content_str.encode("utf-8")) if mode != "bytes" else len(base64.b64decode(content_str))
                    data = WriteFileData(path=path, size=size, mode=mode or "text")
                    return WriteFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_write)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("write_file", msg, WriteFileResult)
        return _build_fs_error_result(
            "write_file",
            str(last or TimeoutError("aio write_file timeout")),
            WriteFileResult,
        )

    async def _write_file_bytes(self, path: str, content: str | bytes, append: bool = False) -> WriteFileResult:
        client = await asyncio.to_thread(self._get_client)
        raw_bytes = content if isinstance(content, (bytes, bytearray)) else bytes(content)
        content_b64 = base64.b64encode(raw_bytes).decode('ascii')
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _write_bytes():
                    client.file.write_file(file=path, content=content_b64, encoding="base64", append=append)
                await asyncio.to_thread(_write_bytes)
                return WriteFileResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=WriteFileData(path=path, size=len(raw_bytes), mode="bytes"),
                )
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("write_file", msg, WriteFileResult)
        return _build_fs_error_result(
            "write_file",
            str(last or TimeoutError("aio write_file timeout")),
            WriteFileResult,
        )

    async def list_files(self, path: str, *, recursive: bool = False,
                         max_depth: Optional[int] = None,
                         sort_by: str = "name", sort_descending: bool = False,
                         file_types: Optional[List[str]] = None, **kwargs) -> ListFilesResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _list():
                    list_kwargs = dict(path=path, recursive=recursive, include_size=True)
                    if max_depth is not None:
                        list_kwargs["max_depth"] = max_depth
                    res = client.file.list_path(**list_kwargs)
                    files = [
                        FileSystemItem(
                            name=item.name,
                            path=item.path,
                            size=item.size if item.size is not None else 0,
                            is_directory=False,
                            modified_time=item.modified_time or "0",
                            type=os.path.splitext(item.name)[1] or None,
                        )
                        for item in (res.data.files or [])
                        if not item.is_directory
                    ]
                    if file_types:
                        files = [item for item in files if item.type in file_types]
                    files = _sort_fs_items(files, sort_by, sort_descending)
                    data = FileSystemData(
                        total_count=len(files),
                        list_items=files,
                        root_path=path,
                        recursive=recursive,
                        max_depth=max_depth,
                    )
                    return ListFilesResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_list)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("list_files", msg, ListFilesResult)
        return _build_fs_error_result(
            "list_files",
            str(last or TimeoutError("aio list_files timeout")),
            ListFilesResult,
        )

    async def list_directories(self, path: str, *, recursive: bool = False,
                               max_depth: Optional[int] = None,
                               sort_by: str = "name", sort_descending: bool = False,
                               **kwargs) -> ListDirsResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _list():
                    list_kwargs = dict(path=path, recursive=recursive)
                    if max_depth is not None:
                        list_kwargs["max_depth"] = max_depth
                    res = client.file.list_path(**list_kwargs)
                    dirs = [
                        FileSystemItem(
                            name=item.name,
                            path=item.path,
                            size=0,
                            is_directory=True,
                            modified_time=item.modified_time or "0",
                        )
                        for item in (res.data.files or [])
                        if item.is_directory
                    ]
                    dirs = _sort_fs_items(dirs, sort_by, sort_descending)
                    data = FileSystemData(
                        total_count=len(dirs),
                        list_items=dirs,
                        root_path=path,
                        recursive=recursive,
                        max_depth=max_depth,
                    )
                    return ListDirsResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_list)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("list_directories", msg, ListDirsResult)
        return _build_fs_error_result(
            "list_directories",
            str(last or TimeoutError("aio list_directories timeout")),
            ListDirsResult,
        )

    async def read_file_stream(self, path: str, *, mode: str = "text",
                               head: Optional[int] = None, tail: Optional[int] = None,
                               line_range: Optional[Tuple[int, int]] = None,
                               encoding: str = "utf-8", chunk_size: int = 8192,
                               **kwargs) -> AsyncIterator[ReadFileStreamResult]:
        head, tail, line_range = _normalize_read_params(head=head, tail=tail, line_range=line_range)
        validation_error = _validate_read_params(mode=mode, head=head, tail=tail, line_range=line_range)
        if validation_error:
            yield _build_fs_error_result("read_file_stream", validation_error, ReadFileStreamResult)
            return

        result = await self.read_file(path, mode=mode, head=head, tail=tail, line_range=line_range)
        if result.code != StatusCode.SUCCESS.code:
            yield ReadFileStreamResult(code=result.code, message=result.message, data=None)
            return

        content = result.data.content
        if mode == "bytes":
            data_bytes = content if isinstance(content, bytes) else str(content).encode(encoding)
            if chunk_size <= 0:
                chunk_size = 8192
            if not data_bytes:
                return
            for chunk_index, start in enumerate(range(0, len(data_bytes), chunk_size)):
                chunk = data_bytes[start:start + chunk_size]
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=path,
                        chunk_content=chunk,
                        mode="bytes",
                        chunk_size=len(chunk),
                        chunk_index=chunk_index,
                        is_last_chunk=start + chunk_size >= len(data_bytes),
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

        for chunk_index, line in enumerate(selected_lines):
            yield ReadFileStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileChunkData(
                    path=path,
                    chunk_content=line,
                    mode="text",
                    chunk_size=len(line.encode(encoding)),
                    chunk_index=chunk_index,
                    is_last_chunk=chunk_index == len(selected_lines) - 1,
                ),
            )

    async def upload_file(self, local_path: str, target_path: str, *,
                          overwrite: bool = False, create_parent_dirs: bool = True,
                          preserve_permissions: bool = True,
                          chunk_size: int = 0, **kwargs) -> UploadFileResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _upload():
                    filename = os.path.basename(local_path)
                    with open(local_path, "rb") as fh:
                        res = client.file.upload_file(
                            file=(filename, fh, "application/octet-stream"),
                            path=target_path,
                        )
                    data = UploadFileData(
                        local_path=local_path,
                        target_path=target_path,
                        size=res.data.file_size,
                    )
                    return UploadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_upload)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio upload_file timeout")

    async def upload_file_stream(self, local_path: str, target_path: str, *,
                                 overwrite: bool = False, create_parent_dirs: bool = True,
                                 preserve_permissions: bool = True,
                                 chunk_size: int = 1048576,
                                 **kwargs) -> AsyncIterator[UploadFileStreamResult]:
        client = await asyncio.to_thread(self._get_client)
        file_size = os.path.getsize(local_path)
        chunk_idx = 0
        with open(local_path, "rb") as fh:
            while True:
                raw = fh.read(chunk_size)
                if not raw:
                    break
                is_first = (chunk_idx == 0)
                content_str = raw.decode("utf-8", errors="replace")
                is_last = (fh.tell() >= file_size)

                def _write_chunk(c=content_str, first=is_first):
                    client.file.write_file(file=target_path, content=c, append=not first)

                await asyncio.to_thread(_write_chunk)
                yield UploadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                             data=UploadFileChunkData(
                                                 local_path=local_path, target_path=target_path,
                                                 chunk_size=chunk_size, chunk_index=chunk_idx, is_last_chunk=is_last
                                             ))
                chunk_idx += 1

    async def download_file(self, source_path: str, local_path: str, *,
                            overwrite: bool = False, create_parent_dirs: bool = True,
                            preserve_permissions: bool = True,
                            chunk_size: int = 0, **kwargs) -> DownloadFileResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _download():
                    chunks = []
                    for chunk in client.file.download_file(path=source_path):
                        chunks.append(chunk)
                    return b"".join(chunks)

                content = await asyncio.to_thread(_download)
                if create_parent_dirs:
                    parent = os.path.dirname(local_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                if not overwrite and os.path.exists(local_path):
                    raise FileExistsError(f"File already exists: {local_path}")
                with open(local_path, "wb") as fh:
                    fh.write(content)
                data = DownloadFileData(
                    source_path=source_path,
                    local_path=local_path,
                    size=len(content),
                )
                return DownloadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)
            except FileExistsError:
                raise
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio download_file timeout")

    async def download_file_stream(self, source_path: str, local_path: str, *,
                                   overwrite: bool = False, create_parent_dirs: bool = True,
                                   preserve_permissions: bool = True,
                                   chunk_size: int = 1048576,
                                   **kwargs) -> AsyncIterator[DownloadFileStreamResult]:
        client = await asyncio.to_thread(self._get_client)
        if create_parent_dirs:
            parent = os.path.dirname(local_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not overwrite and os.path.exists(local_path):
            raise FileExistsError(f"File already exists: {local_path}")

        def _iter_download():
            return list(client.file.download_file(path=source_path))

        raw_chunks = await asyncio.to_thread(_iter_download)
        buffer = b""
        chunk_idx = 0
        total_raw = len(raw_chunks)
        with open(local_path, "wb") as fh:
            for i, raw in enumerate(raw_chunks):
                buffer += raw
                is_last_raw = (i == total_raw - 1)
                while len(buffer) >= chunk_size:
                    piece = buffer[:chunk_size]
                    buffer = buffer[chunk_size:]
                    fh.write(piece)
                    yield DownloadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                                   data=DownloadFileChunkData(
                                                       source_path=source_path, local_path=local_path,
                                                       chunk_size=chunk_size, chunk_index=chunk_idx,
                                                       is_last_chunk=(is_last_raw and len(buffer) == 0)
                                                   ))
                    chunk_idx += 1
                if is_last_raw and buffer:
                    fh.write(buffer)
                    yield DownloadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                                   data=DownloadFileChunkData(
                                                       source_path=source_path, local_path=local_path,
                                                       chunk_size=chunk_size, chunk_index=chunk_idx,
                                                       is_last_chunk=True
                                                   ))

    async def search_files(self, path: str, pattern: str,
                           exclude_patterns: Optional[List[str]] = None) -> SearchFilesResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _search():
                    # Try glob_files first; fall back to list_path + fnmatch
                    try:
                        glob_kwargs = dict(
                            path=path,
                            pattern=pattern,
                            files_only=True,
                            include_metadata=True,
                        )
                        if exclude_patterns:
                            glob_kwargs["exclude"] = exclude_patterns
                        res = client.file.glob_files(**glob_kwargs)
                        files = res.data.files or []
                    except Exception as glob_err:
                        if "404" in str(glob_err):
                            # glob_files not supported, fall back to list_path
                            res = client.file.list_path(path=path, recursive=True, include_size=True)
                            files = [
                                f for f in (res.data.files or [])
                                if not f.is_directory and fnmatch.fnmatch(f.name, pattern)
                            ]
                            if exclude_patterns:
                                for ep in exclude_patterns:
                                    files = [f for f in files if not fnmatch.fnmatch(f.name, ep)]
                        else:
                            raise
                    items = [
                        FileSystemItem(
                            name=f.name,
                            path=f.path,
                            size=f.size if f.size is not None else 0,
                            is_directory=f.is_directory or False,
                            modified_time=f.modified_time or "0",
                            type=os.path.splitext(f.name)[1] or None,
                        )
                        for f in files
                    ]
                    items = _sort_fs_items(items, "name", False)
                    data = SearchFilesData(
                        total_matches=len(items),
                        matching_files=items,
                        search_path=path,
                        search_pattern=pattern,
                        exclude_patterns=exclude_patterns,
                    )
                    return SearchFilesResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_search)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_fs_error_result("search_files", msg, SearchFilesResult)
        return _build_fs_error_result(
            "search_files",
            str(last or TimeoutError("aio search_files timeout")),
            SearchFilesResult,
        )


@SandboxRegistry.provider("aio", "shell")
class AIOShellProvider(BaseShellProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

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
        inner_parts.append(f"{{ {command}; }} 2> >(sed 's/^/__OJW_STDERR__:/')")
        inner_command = " && ".join(inner_parts)
        shell_command = f"/bin/bash -lc {_quote_shell_value(inner_command)}"
        if timeout is not None and timeout > 0:
            shell_command = f"timeout {int(timeout)}s {shell_command}"
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

        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        wrapped_command = self._build_wrapped_command(
            command,
            cwd=cwd or ".",
            timeout=timeout,
            environment=environment,
        )
        while time.time() < deadline:
            try:
                def _exec():
                    res = client.shell.exec_command(command=wrapped_command)
                    raw_exit = res.data.exit_code if hasattr(res.data, "exit_code") else 0
                    raw_output = getattr(res.data, "output", "") or ""
                    stdout, stderr = _split_marked_shell_output(raw_output)
                    if raw_exit == 124:
                        return _build_shell_error_result(
                            "execute_cmd",
                            f"execution timeout after {timeout} seconds",
                            ExecuteCmdResult,
                            data=ExecuteCmdData(
                                command=command,
                                cwd=cwd or ".",
                                stdout=stdout,
                                stderr=stderr,
                                exit_code=raw_exit,
                            ),
                        )
                    data = ExecuteCmdData(
                        command=command,
                        cwd=cwd or ".",
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=raw_exit if raw_exit is not None else 0,
                    )
                    return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_exec)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                return _build_shell_error_result("execute_cmd", msg, ExecuteCmdResult)
        return _build_shell_error_result(
            "execute_cmd",
            str(last or TimeoutError("aio execute_cmd timeout")),
            ExecuteCmdResult,
        )

    async def execute_cmd_stream(self, command: str, *, cwd: Optional[str] = None,
                                 timeout: Optional[int] = 300,
                                 environment: Optional[Dict[str, str]] = None,
                                 **kwargs) -> AsyncIterator[ExecuteCmdStreamResult]:
        if not command or not command.strip():
            yield _build_shell_error_result(
                "execute_cmd_stream",
                "command can not be empty",
                ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return

        result = await self.execute_cmd(command, cwd=cwd or ".", timeout=timeout, environment=environment)
        if result.code != StatusCode.SUCCESS.code:
            yield _build_shell_error_result(
                "execute_cmd_stream",
                result.message.split("reason: ", 1)[-1] if "reason: " in result.message else result.message,
                ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return

        stdout = result.data.stdout or ""
        stderr = result.data.stderr or ""
        exit_code = result.data.exit_code
        all_parts: List[Tuple[str, str]] = []
        if stdout:
            for line in stdout.splitlines(keepends=True):
                all_parts.append((line, "stdout"))
        if stderr:
            for line in stderr.splitlines(keepends=True):
                all_parts.append((line, "stderr"))
        for i, (text, stype) in enumerate(all_parts):
            yield ExecuteCmdStreamResult(
                code=StatusCode.SUCCESS.code,
                message=f"Get {stype} stream successfully",
                data=ExecuteCmdChunkData(text=text, type=stype, chunk_index=i),
            )
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message="Command executed successfully",
            data=ExecuteCmdChunkData(chunk_index=len(all_parts), exit_code=exit_code),
        )


@SandboxRegistry.provider("aio", "code")
class AIOCodeProvider(BaseCodeProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            if not self.endpoint.base_url:
                raise ValueError("AIO provider requires endpoint.base_url")
            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

    @staticmethod
    def _build_code_command(code: str, language: str, *, force_file: bool) -> Optional[str]:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        if language == "python":
            if force_file:
                return (
                    "tmp=$(mktemp /tmp/ojw_code_XXXXXX.py) && "
                    f"printf %s {_quote_shell_value(encoded)} | base64 -d > \"$tmp\" && "
                    "python \"$tmp\"; status=$?; rm -f \"$tmp\"; exit $status"
                )
            return (
                    "python -c "
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
            return "node -e " + _quote_shell_value(f"eval(Buffer.from('{encoded}','base64').toString('utf8'))")
        return None

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

        shell_provider = AIOShellProvider(self.endpoint, self.config)
        shell_result = await shell_provider.execute_cmd(
            command=command,
            cwd=".",
            timeout=timeout,
            environment=environment,
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
                shell_result.message.split("reason: ", 1)[-1] if "reason: " in
                                                                 shell_result.message else shell_result.message,
                ExecuteCodeResult,
                data=result_data,
            )

        return ExecuteCodeResult(
            code=StatusCode.SUCCESS.code,
            message="Code executed successfully",
            data=result_data,
        )

    async def execute_code_stream(self, code: str, *, language: str = "python",
                                  timeout: int = 300,
                                  environment: Optional[Dict[str, str]] = None,
                                  options: Optional[Dict[str, Any]] = None,
                                  **kwargs) -> AsyncIterator[ExecuteCodeStreamResult]:
        data = ExecuteCodeChunkData(chunk_index=0, exit_code=-1)
        if not code or not code.strip():
            yield _build_code_error_result("execute_code_stream", "code can not be empty",
                                           ExecuteCodeStreamResult, data)
            return
        if language not in {"python", "javascript"}:
            yield _build_code_error_result(
                "execute_code_stream",
                f"{language} is not supported",
                ExecuteCodeStreamResult,
                data,
            )
            return

        force_file = bool((options or {}).get("force_file", False))
        command = self._build_code_command(code, language, force_file=force_file)
        if command is None:
            yield _build_code_error_result(
                "execute_code_stream",
                "subprocess cmd can not be none",
                ExecuteCodeStreamResult,
                data,
            )
            return

        shell_provider = AIOShellProvider(self.endpoint, self.config)
        async for shell_chunk in shell_provider.execute_cmd_stream(
                command=command,
                cwd=".",
                timeout=timeout,
                environment=environment,
        ):
            if shell_chunk.code != StatusCode.SUCCESS.code:
                yield _build_code_error_result(
                    "execute_code_stream",
                    shell_chunk.message.split("reason: ", 1)[-1]
                    if "reason: " in shell_chunk.message else shell_chunk.message,
                    ExecuteCodeStreamResult,
                    data=ExecuteCodeChunkData(chunk_index=shell_chunk.data.chunk_index if shell_chunk.data else 0,
                                              exit_code=-1),
                )
                return

            chunk_data = shell_chunk.data
            if chunk_data.exit_code is not None:
                yield ExecuteCodeStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message="Code executed successfully",
                    data=ExecuteCodeChunkData(
                        chunk_index=chunk_data.chunk_index,
                        exit_code=chunk_data.exit_code,
                    ),
                )
            else:
                yield ExecuteCodeStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=f"Get {chunk_data.type} stream successfully",
                    data=ExecuteCodeChunkData(
                        text=chunk_data.text,
                        type=chunk_data.type,
                        chunk_index=chunk_data.chunk_index,
                    ),
                )
