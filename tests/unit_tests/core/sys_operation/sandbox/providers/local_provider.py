# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Local providers for testing the SandboxRegistry provider registration mechanism.

These providers:
- Are registered under sandbox_type="local" (completely separate from "aio")
- Return simple hardcoded values to verify the registration/routing flow
- Do NOT depend on any HTTP service or real sandbox
- Do NOT use the agent_sandbox SDK

The goal is to test that:
1. SandboxRegistry.register_provider() works
2. SandboxRegistry.create_provider() can instantiate them
3. The Gateway full-chain routing (handle_request) works
4. Operation classes (FsOperation, ShellOperation, CodeOperation) properly delegate to providers
"""

import fnmatch
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.result import (
    ReadFileResult, ReadFileData,
    ReadFileStreamResult, ReadFileChunkData,
    WriteFileResult, WriteFileData,
    ListFilesResult, ListDirsResult, FileSystemData, FileSystemItem,
    SearchFilesResult, SearchFilesData,
    UploadFileResult, UploadFileData,
    UploadFileStreamResult, UploadFileChunkData,
    DownloadFileResult, DownloadFileData,
    DownloadFileStreamResult, DownloadFileChunkData,
    ExecuteCmdResult, ExecuteCmdData,
    ExecuteCmdStreamResult, ExecuteCmdChunkData,
    ExecuteCmdBackgroundResult, ExecuteCmdBackgroundData,
    ExecuteCodeResult, ExecuteCodeData,
    ExecuteCodeStreamResult, ExecuteCodeChunkData,
)
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import (
    BaseFSProvider,
    BaseShellProvider,
    BaseCodeProvider,
)
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint


@SandboxRegistry.provider("local", "fs")
class LocalFSProvider(BaseFSProvider):
    """Local FileSystem provider that returns hardcoded values.

    This is used ONLY to test the provider registration and routing mechanism.
    """

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._root_dir = Path(tempfile.mkdtemp(prefix="oj_sandbox_fs_"))

    def _resolve_sandbox_path(self, path: str) -> Path:
        target = Path(path)
        candidate = target if target.is_absolute() else self._root_dir / target
        resolved = candidate.resolve()
        root = self._root_dir.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"Access denied: {path} traverses outside sandbox root")
        return resolved

    @staticmethod
    def _text_read_param_count(head: Optional[int], tail: Optional[int], line_range: Optional[tuple[int, int]]) -> int:
        count = 0
        if head is not None and head != 0:
            count += 1
        if tail is not None and tail != 0:
            count += 1
        if line_range is not None:
            count += 1
        return count

    @staticmethod
    def _apply_text_slice(
            content: str,
            head: Optional[int],
            tail: Optional[int],
            line_range: Optional[tuple[int, int]],
    ) -> str:
        lines = content.splitlines(keepends=True)
        if head is not None and head != 0:
            return "" if head < 0 else "".join(lines[:head])
        if tail is not None and tail != 0:
            return "" if tail < 0 else "".join(lines[-tail:] if tail > 0 else lines)
        if line_range is not None:
            start, end = line_range
            if start <= 0 or end <= 0 or start > end:
                return ""
            return "".join(lines[start - 1:end])
        return content

    @staticmethod
    def _build_item(path: Path, root: Path) -> FileSystemItem:
        relative_path = "." if path == root else path.relative_to(root).as_posix()
        stat = path.stat()
        return FileSystemItem(
            name=path.name if path != root else ".",
            path=relative_path,
            size=stat.st_size if path.is_file() else 0,
            is_directory=path.is_dir(),
            modified_time=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            type=path.suffix if path.is_file() else None,
        )

    async def read_file(
            self,
            path: str,
            *,
            mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None,
            tail: Optional[int] = None,
            line_range: Optional[tuple[int, int]] = None,
            encoding: str = "utf-8",
            chunk_size: int = 8192,
            options: Optional[Dict[str, Any]] = None
    ) -> ReadFileResult:
        try:
            if mode == "bytes" and any(param is not None for param in (head, tail, line_range)):
                return build_operation_error_result(
                    error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                    msg_format_kwargs={"execution": "read_file", "error_msg": "head/tail/line_range "
                                                                              "only supported in text mode"},
                    result_cls=ReadFileResult,
                )
            if self._text_read_param_count(head, tail, line_range) > 1:
                return build_operation_error_result(
                    error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                    msg_format_kwargs={
                        "execution": "read_file",
                        "error_msg": "head, tail and line_range cannot be specified simultaneously",
                    },
                    result_cls=ReadFileResult,
                )
            file_path = self._resolve_sandbox_path(path)
            raw = file_path.read_bytes()
            if mode == "bytes":
                content: str | bytes = raw
            else:
                text = raw.decode(encoding)
                content = self._apply_text_slice(text, head, tail, line_range)
            return ReadFileResult(
                code=0,
                message="success",
                data=ReadFileData(path=path, content=content, mode=mode),
            )
        except PermissionError as exc:
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "read_file", "error_msg": str(exc)},
                result_cls=ReadFileResult,
            )
        except FileNotFoundError as exc:
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "read_file", "error_msg": str(exc)},
                result_cls=ReadFileResult,
            )

    async def read_file_stream(
            self,
            path: str,
            *,
            mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None,
            tail: Optional[int] = None,
            line_range: Optional[tuple[int, int]] = None,
            encoding: str = "utf-8",
            chunk_size: int = 64,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ReadFileStreamResult]:
        if mode == "bytes" and any(param is not None for param in (head, tail, line_range)):
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "read_file_stream", "error_msg":
                    "head/tail/line_range only supported in text mode"},
                result_cls=ReadFileStreamResult,
            )
            return
        if self._text_read_param_count(head, tail, line_range) > 1:
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={
                    "execution": "read_file_stream",
                    "error_msg": "head, tail and line_range cannot be specified simultaneously",
                },
                result_cls=ReadFileStreamResult,
            )
            return
        try:
            file_path = self._resolve_sandbox_path(path)
            raw = file_path.read_bytes()
        except (PermissionError, FileNotFoundError) as exc:
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "read_file_stream", "error_msg": str(exc)},
                result_cls=ReadFileStreamResult,
            )
            return

        if mode == "bytes":
            actual_chunk_size = chunk_size if chunk_size and chunk_size > 0 else len(raw) or 1
            for index in range(0, len(raw), actual_chunk_size):
                chunk = raw[index:index + actual_chunk_size]
                yield ReadFileStreamResult(
                    code=0,
                    message="success",
                    data=ReadFileChunkData(
                        path=path,
                        chunk_content=chunk,
                        mode=mode,
                        chunk_size=actual_chunk_size,
                        chunk_index=index // actual_chunk_size,
                        is_last_chunk=index + actual_chunk_size >= len(raw),
                    ),
                )
            if not raw:
                yield ReadFileStreamResult(
                    code=0,
                    message="success",
                    data=ReadFileChunkData(
                        path=path,
                        chunk_content=b"",
                        mode=mode,
                        chunk_size=actual_chunk_size,
                        chunk_index=0,
                        is_last_chunk=True,
                    ),
                )
            return

        text = self._apply_text_slice(raw.decode(encoding), head, tail, line_range)
        if text == "":
            yield ReadFileStreamResult(
                code=0,
                message="success",
                data=ReadFileChunkData(
                    path=path,
                    chunk_content="",
                    mode=mode,
                    chunk_size=chunk_size,
                    chunk_index=0,
                    is_last_chunk=True,
                ),
            )
            return
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            yield ReadFileStreamResult(
                code=0,
                message="success",
                data=ReadFileChunkData(
                    path=path,
                    chunk_content=line,
                    mode=mode,
                    chunk_size=chunk_size,
                    chunk_index=index,
                    is_last_chunk=index == len(lines) - 1,
                ),
            )

    async def write_file(
            self,
            path: str,
            content: str | bytes,
            *,
            mode: Literal['text', 'bytes'] = "text",
            prepend_newline: bool = False,
            append_newline: bool = False,
            append: bool = False,
            create_if_not_exist: bool = True,
            permissions: str = "644",
            encoding: str = "utf-8",
            options: Optional[Dict[str, Any]] = None
    ) -> WriteFileResult:
        try:
            file_path = self._resolve_sandbox_path(path)
            if create_if_not_exist:
                file_path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "bytes":
                data = content if isinstance(content, bytes) else str(content).encode(encoding)
                if append:
                    existing = file_path.read_bytes() if file_path.exists() else b""
                    data = existing + data
                file_path.write_bytes(data)
                size = len(data)
            else:
                text = content.decode(encoding) if isinstance(content, bytes) else str(content)
                if prepend_newline:
                    text = "\n" + text
                if append_newline:
                    text = text + "\n"
                if append:
                    existing = file_path.read_text(encoding=encoding) if file_path.exists() else ""
                    text = existing + text
                file_path.write_text(text, encoding=encoding, newline="")
                size = len(text.encode(encoding))
            return WriteFileResult(code=0, message="success", data=WriteFileData(path=path, size=size, mode=mode))
        except PermissionError as exc:
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "write_file", "error_msg": str(exc)},
                result_cls=WriteFileResult,
            )

    async def upload_file(
            self,
            local_path: str,
            target_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = 1048576,
            options: Optional[Dict[str, Any]] = None
    ) -> UploadFileResult:
        target = self._resolve_sandbox_path(target_path)
        if create_parent_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)
        return UploadFileResult(
            code=0,
            message="success",
            data=UploadFileData(local_path=local_path, target_path=target_path, size=target.stat().st_size),
        )

    async def upload_file_stream(
            self,
            local_path: str,
            target_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = 1048576,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[UploadFileStreamResult]:
        result = await self.upload_file(
            local_path,
            target_path,
            overwrite=overwrite,
            create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions,
            chunk_size=chunk_size,
            options=options,
        )
        actual_chunk_size = chunk_size if chunk_size and chunk_size > 0 else result.data.size
        yield UploadFileStreamResult(
            code=result.code,
            message=result.message,
            data=UploadFileChunkData(
                local_path=local_path,
                target_path=target_path,
                chunk_size=actual_chunk_size,
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
            chunk_size: int = 1048576,
            options: Optional[Dict[str, Any]] = None
    ) -> DownloadFileResult:
        source = self._resolve_sandbox_path(source_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, local_path)
        return DownloadFileResult(
            code=0,
            message="success",
            data=DownloadFileData(source_path=source_path, local_path=local_path, size=Path(local_path).stat().st_size),
        )

    async def download_file_stream(
            self,
            source_path: str,
            local_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = 16,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[DownloadFileStreamResult]:
        result = await self.download_file(
            source_path,
            local_path,
            overwrite=overwrite,
            create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions,
            chunk_size=chunk_size,
            options=options,
        )
        actual_chunk_size = chunk_size if chunk_size and chunk_size > 0 else result.data.size
        yield DownloadFileStreamResult(
            code=result.code,
            message=result.message,
            data=DownloadFileChunkData(
                source_path=source_path,
                local_path=local_path,
                chunk_size=actual_chunk_size,
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
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False,
            file_types: Optional[list[str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ListFilesResult:
        root = self._resolve_sandbox_path(path)
        pattern_set = set(file_types or [])
        items: list[FileSystemItem] = []
        iterator = root.rglob("*") if recursive else root.glob("*")
        for item in iterator:
            if not item.is_file():
                continue
            depth = len(item.relative_to(root).parts)
            if max_depth is not None and recursive and depth > max_depth:
                continue
            if pattern_set and item.suffix not in pattern_set:
                continue
            items.append(self._build_item(item, self._root_dir))
        items.sort(key=lambda item: getattr(item, "name"), reverse=sort_descending)
        return ListFilesResult(
            code=0,
            message="success",
            data=FileSystemData(total_count=len(items), list_items=items, root_path=path,
                                recursive=recursive, max_depth=max_depth),
        )

    async def list_directories(
            self,
            path: str,
            *,
            recursive: bool = False,
            max_depth: Optional[int] = None,
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False,
            options: Optional[Dict[str, Any]] = None
    ) -> ListDirsResult:
        root = self._resolve_sandbox_path(path)
        items: list[FileSystemItem] = []
        iterator = root.rglob("*") if recursive else root.glob("*")
        for item in iterator:
            if not item.is_dir():
                continue
            depth = len(item.relative_to(root).parts)
            if max_depth is not None and recursive and depth > max_depth:
                continue
            items.append(self._build_item(item, self._root_dir))
        items.sort(key=lambda item: getattr(item, "name"), reverse=sort_descending)
        return ListDirsResult(
            code=0,
            message="success",
            data=FileSystemData(total_count=len(items), list_items=items, root_path=path,
                                recursive=recursive, max_depth=max_depth),
        )

    async def search_files(
            self,
            path: str,
            pattern: str,
            exclude_patterns: Optional[list[str]] = None
    ) -> SearchFilesResult:
        root = self._resolve_sandbox_path(path)
        excluded = exclude_patterns or []
        items: list[FileSystemItem] = []
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(root).as_posix()
            if not fnmatch.fnmatch(item.name, pattern) and not fnmatch.fnmatch(relative, pattern):
                continue
            if any(fnmatch.fnmatch(item.name, exclude) or fnmatch.fnmatch(relative, exclude) for exclude in excluded):
                continue
            items.append(self._build_item(item, self._root_dir))
        items.sort(key=lambda item: item.name)
        return SearchFilesResult(
            code=0,
            message="success",
            data=SearchFilesData(
                total_matches=len(items),
                matching_files=items,
                search_path=path,
                search_pattern=pattern,
                exclude_patterns=exclude_patterns,
            ),
        )


@SandboxRegistry.provider("local", "shell")
class LocalShellProvider(BaseShellProvider):
    """Local Shell provider that returns hardcoded values."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)

    @staticmethod
    def _resolve_stdout(command: str, cwd: Optional[str], environment: Optional[Dict[str, str]]) -> str:
        environment = environment or {}
        if command in ("pwd", "echo %CD%"):
            return f"{cwd or '/tmp'}\n"
        if command.startswith("echo "):
            payload = command[5:]
            if payload in ("$TEST_VAR", "%TEST_VAR%"):
                return f"{environment.get('TEST_VAR', '')}\n"
            if payload.startswith("$"):
                return f"{environment.get(payload[1:], '')}\n"
            return f"{payload}\n"
        if "127.0.0.1" in command:
            return "127.0.0.1\n127.0.0.1\n127.0.0.1\n"
        if "ls" in command or "dir" in command:
            return "file1.txt\nfile2.txt\n"
        if "chunk1" in command and "chunk2" in command:
            return "chunk1\nchunk2\n"
        return f"local_shell_output_for: {command}"

    @staticmethod
    def _resolve_stderr(command: str) -> str:
        return "error_chunk\n" if "error_chunk" in command else ""

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdResult:
        if not command or not command.strip():
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd", "error_msg": "command can not be empty"},
                result_cls=ExecuteCmdResult,
                data=ExecuteCmdData(command=command, cwd=cwd or "/tmp", exit_code=-1),
            )
        if timeout is not None and timeout <= 1 and any(token in command for token in ("sleep", "ping", "while True")):
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd", "error_msg":
                    f"execution timeout after {timeout} seconds"},
                result_cls=ExecuteCmdResult,
                data=ExecuteCmdData(
                    command=command,
                    cwd=cwd or "/tmp",
                    stdout=self._resolve_stdout(command, cwd, environment),
                    stderr=self._resolve_stderr(command),
                    exit_code=-1,
                ),
            )
        return ExecuteCmdResult(code=0, message="success", data=ExecuteCmdData(
            command=command,
            cwd=cwd or "/tmp",
            stdout=self._resolve_stdout(command, cwd, environment),
            stderr=self._resolve_stderr(command),
            exit_code=0,
        ))

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        if not command or not command.strip():
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_stream", "error_msg": "command can not be empty"},
                result_cls=ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return
        if timeout is not None and timeout <= 1 and any(token in command for token in ("sleep", "ping", "while True")):
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_stream", "error_msg":
                    f"execution timeout after {timeout} seconds"},
                result_cls=ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return

        chunk_index = 0
        stdout = self._resolve_stdout(command, cwd, environment)
        stderr = self._resolve_stderr(command)
        for line in stdout.splitlines(keepends=True):
            yield ExecuteCmdStreamResult(
                code=0,
                message="Get stdout stream successfully",
                data=ExecuteCmdChunkData(text=line, type="stdout", chunk_index=chunk_index),
            )
            chunk_index += 1
        for line in stderr.splitlines(keepends=True):
            yield ExecuteCmdStreamResult(
                code=0,
                message="Get stderr stream successfully",
                data=ExecuteCmdChunkData(text=line, type="stderr", chunk_index=chunk_index),
            )
            chunk_index += 1
        yield ExecuteCmdStreamResult(
            code=0,
            message="Command executed successfully",
            data=ExecuteCmdChunkData(text="", type="stdout", chunk_index=chunk_index, exit_code=0),
        )

    async def execute_cmd_background(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            environment: Optional[Dict[str, str]] = None,
            grace: float = 3.0,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdBackgroundResult:
        if not command or not command.strip():
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_background", "error_msg": "command can not be empty"},
                result_cls=ExecuteCmdBackgroundResult,
                data=ExecuteCmdBackgroundData(command=command, cwd=cwd or "/tmp", pid=None),
            )
        return ExecuteCmdBackgroundResult(
            code=0,
            message="success",
            data=ExecuteCmdBackgroundData(command=command, cwd=cwd or "/tmp", pid=12345),
        )


@SandboxRegistry.provider("local", "code")
class LocalCodeProvider(BaseCodeProvider):
    """Local Code execution provider that returns hardcoded values."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)

    @staticmethod
    def _extract_prints(code: str) -> list[str]:
        outputs = re.findall(r'print\s*\(\s*["\']([^"\']*)["\']\s*\)', code)
        if "os.getenv" in code:
            outputs.extend(re.findall(r'os\.getenv\(["\']([^"\']+)["\']\)', code))
        return outputs

    @staticmethod
    def _render_env_output(code: str, environment: Optional[Dict[str, str]]) -> str:
        env = environment or {}
        keys = re.findall(r'os\.getenv\(["\']([^"\']+)["\']\)', code)
        if keys:
            return "".join(f"{env.get(key, '')}\n" for key in keys)
        return ""

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        if not code or not code.strip():
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code", "error_msg": "code can not be empty"},
                result_cls=ExecuteCodeResult,
                data=ExecuteCodeData(code_content=code, language=language, exit_code=-1),
            )
        if language not in ("python", "javascript"):
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code", "error_msg": f"{language} is not supported"},
                result_cls=ExecuteCodeResult,
                data=ExecuteCodeData(code_content=code, language=language, exit_code=-1),
            )
        if timeout <= 1 and any(token in code for token in ("time.sleep(3)", "while True")):
            return build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code", "error_msg":
                    f"execution timeout after {timeout} seconds"},
                result_cls=ExecuteCodeResult,
                data=ExecuteCodeData(code_content=code, language=language, exit_code=-1),
            )

        stderr = ""
        exit_code = 0
        if "missing quote" in code:
            stderr = "SyntaxError: unterminated string literal"
            exit_code = 1
        elif "undefined_variable_999" in code:
            stderr = "NameError: name 'undefined_variable_999' is not defined"
            exit_code = 1
        elif "undefined_variable" in code:
            stderr = "NameError: name 'undefined_variable' is not defined"
            exit_code = 1

        stdout = self._render_env_output(code, environment)
        if not stdout:
            prints = self._extract_prints(code)
            stdout = "\n".join(prints) + ("\n" if prints else "")
        if not stdout and not stderr:
            stdout = "local_code_no_print"

        return ExecuteCodeResult(
            code=0,
            message="Code executed successfully",
            data=ExecuteCodeData(
                code_content=code,
                language=language,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
            ),
        )

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        if not code or not code.strip():
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code_stream", "error_msg": "code can not be empty"},
                result_cls=ExecuteCodeStreamResult,
                data=ExecuteCodeChunkData(chunk_index=0, exit_code=-1),
            )
            return
        if language not in ("python", "javascript"):
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code_stream", "error_msg": f"{language} is not supported"},
                result_cls=ExecuteCodeStreamResult,
                data=ExecuteCodeChunkData(chunk_index=0, exit_code=-1),
            )
            return
        if timeout <= 2 and "while True" in code:
            yield build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code_stream",
                                   "error_msg": f"execution timeout after {timeout} seconds"},
                result_cls=ExecuteCodeStreamResult,
                data=ExecuteCodeChunkData(chunk_index=0, exit_code=-1),
            )
            return

        chunk_index = 0
        stdout = self._render_env_output(code, environment)
        if not stdout:
            prints = self._extract_prints(code)
            stdout = "".join(f"{msg}\n" for msg in prints)
        stderr = ""
        exit_code = 0
        if "undefined_variable_999" in code:
            stderr = "NameError: name 'undefined_variable_999' is not defined\n"
            exit_code = 1
        elif "undefined_variable" in code:
            stderr = "NameError: name 'undefined_variable' is not defined\n"
            exit_code = 1

        if stdout:
            for line in stdout.splitlines(keepends=True):
                yield ExecuteCodeStreamResult(
                    code=0,
                    message="Get stdout stream successfully",
                    data=ExecuteCodeChunkData(text=line, type="stdout", chunk_index=chunk_index),
                )
                chunk_index += 1
        if stderr:
            for line in stderr.splitlines(keepends=True):
                yield ExecuteCodeStreamResult(
                    code=0,
                    message="Get stderr stream successfully",
                    data=ExecuteCodeChunkData(text=line, type="stderr", chunk_index=chunk_index),
                )
                chunk_index += 1
        yield ExecuteCodeStreamResult(
            code=0,
            message="Code executed successfully",
            data=ExecuteCodeChunkData(text="", type="stdout", chunk_index=chunk_index, exit_code=exit_code),
        )
