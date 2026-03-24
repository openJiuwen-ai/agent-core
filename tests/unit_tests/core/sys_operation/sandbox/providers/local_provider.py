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

import re
from typing import AsyncIterator, Literal, Optional, Dict, Any

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
    ExecuteCodeResult, ExecuteCodeData,
    ExecuteCodeStreamResult, ExecuteCodeChunkData,
)
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
        # Simple hardcoded response to verify routing works
        return ReadFileResult(code=0, message="success", data=ReadFileData(
            path=path, content=f"local_read_content_for_{path}", mode=mode
        ))

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
        content = f"local_stream_content_for_{path}"
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            is_last = i + chunk_size >= len(content)
            yield ReadFileStreamResult(code=0, message="success", data=ReadFileChunkData(
                path=path, chunk_content=chunk, mode=mode,
                chunk_size=chunk_size, chunk_index=i // chunk_size, is_last_chunk=is_last
            ))

    async def write_file(
            self,
            path: str,
            content: str | bytes,
            *,
            mode: Literal['text', 'bytes'] = "text",
            prepend_newline: bool = False,
            append_newline: bool = False,
            create_if_not_exist: bool = True,
            permissions: str = "644",
            encoding: str = "utf-8",
            options: Optional[Dict[str, Any]] = None
    ) -> WriteFileResult:
        if isinstance(content, bytes):
            content = content.decode(encoding)
        return WriteFileResult(code=0, message="success", data=WriteFileData(
            path=path, size=len(content), mode=mode
        ))

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
        return UploadFileResult(code=0, message="success", data=UploadFileData(
            local_path=local_path, target_path=target_path, size=999
        ))

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
        yield UploadFileStreamResult(code=0, message="success", data=UploadFileChunkData(
            local_path=local_path, target_path=target_path,
            chunk_size=chunk_size, chunk_index=0, is_last_chunk=True
        ))

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
        return DownloadFileResult(code=0, message="success", data=DownloadFileData(
            source_path=source_path, local_path=local_path, size=888
        ))

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
        content = f"local_dl_content_for_{source_path}"
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            is_last = i + chunk_size >= len(content)
            yield DownloadFileStreamResult(code=0, message="success", data=DownloadFileChunkData(
                source_path=source_path, local_path=local_path,
                chunk_size=chunk_size, chunk_index=i // chunk_size, is_last_chunk=is_last
            ))

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
        items = [
            FileSystemItem(name="file1.txt", path=f"{path}/file1.txt", size=100, is_directory=False, modified_time="0"),
            FileSystemItem(name="file2.txt", path=f"{path}/file2.txt", size=200, is_directory=False, modified_time="0"),
        ]
        return ListFilesResult(code=0, message="success", data=FileSystemData(
            total_count=len(items), list_items=items, root_path=path,
            recursive=recursive, max_depth=max_depth
        ))

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
        items = [
            FileSystemItem(name="subdir", path=f"{path}/subdir", size=0, is_directory=True, modified_time="0"),
        ]
        return ListDirsResult(code=0, message="success", data=FileSystemData(
            total_count=len(items), list_items=items, root_path=path,
            recursive=recursive, max_depth=max_depth
        ))

    async def search_files(
            self,
            path: str,
            pattern: str,
            exclude_patterns: Optional[list[str]] = None
    ) -> SearchFilesResult:
        items = [
            FileSystemItem(name="matched.txt", path=f"{path}/matched.txt",
                           size=50, is_directory=False, modified_time="0"),
        ]
        return SearchFilesResult(code=0, message="success", data=SearchFilesData(
            total_matches=len(items), matching_files=items,
            search_path=path, search_pattern=pattern, exclude_patterns=exclude_patterns
        ))


@SandboxRegistry.provider("local", "shell")
class LocalShellProvider(BaseShellProvider):
    """Local Shell provider that returns hardcoded values."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        return ExecuteCmdResult(code=0, message="success", data=ExecuteCmdData(
            command=command, cwd=cwd or "/tmp", stdout=f"local_shell_output_for: {command}", stderr="", exit_code=0
        ))

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        yield ExecuteCmdStreamResult(code=0, message="success", data=ExecuteCmdChunkData(
            text=f"local_stream_shell: {command}", type="stdout", chunk_index=0, exit_code=0
        ))


@SandboxRegistry.provider("local", "code")
class LocalCodeProvider(BaseCodeProvider):
    """Local Code execution provider that returns hardcoded values."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        # Extract print statements for a simple mock behavior
        prints = re.findall(r'print\s*\(\s*["\']([^"\']*)["\']\s*\)', code)
        stdout = "\n".join(prints) + ("\n" if prints else "")
        return ExecuteCodeResult(code=0, message="success", data=ExecuteCodeData(
            code_content=code, language=language, stdout=stdout or "local_code_no_print", stderr="", exit_code=0
        ))

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        prints = re.findall(r'print\s*\(\s*["\']([^"\']*)["\']\s*\)', code)
        for i, msg in enumerate(prints):
            yield ExecuteCodeStreamResult(code=0, message="success", data=ExecuteCodeChunkData(
                text=msg + "\n", type="stdout", chunk_index=i, exit_code=None
            ))
        yield ExecuteCodeStreamResult(code=0, message="success", data=ExecuteCodeChunkData(
            text="", type="stdout", chunk_index=len(prints), exit_code=0
        ))
