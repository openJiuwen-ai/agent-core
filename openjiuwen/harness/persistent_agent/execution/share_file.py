# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""share_file (PA-08): list a finished workspace file for this ending's handoff.

Listing checks the read authorization, the path and the file, then records the file in this
Execution's table; it reads no content and sends nothing. The ending prepares the candidate from
the file as it is then, and the host delivers it to the current reply target.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import uuid4

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import FileToolSpec, register_file_tool

from ..schema.execution import ExecutionContext
from ..workspace.access import WorkspaceAccessPort
from .handoff import (
    MAX_REGISTRATIONS,
    SHARE_FILE,
    SHARE_MODES,
    HandoffFileError,
    Registration,
    check_shared_file,
    is_logical_path,
    merge_registration,
    read_scope,
    registration_result,
    resolve_shared_path,
    valid_caption,
)

# The permission engine checks the listed path as a read, with FileGuard's rules for sensitive paths.
register_file_tool(FileToolSpec(SHARE_FILE, "file_path", "read"))

_DESCRIPTION = (
    "把办公空间中已经完成、可以公开的文件列入本次交付。执行结束时，程序按文件当时的内容交给服务端，"
    "由服务端发到当前对话：upload 上传到群空间，attachment 作为附件发送。登记不等于已发送；"
    "投递目标由程序决定，不能指定其他群或会话。caption 会随文件公开。"
    "同一文件以同一方式重复登记只算一项，说明以最后一次为准；草稿和未完成的文件不要登记。"
)
_INPUT_PARAMS = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "办公空间内的相对路径"},
        "share_mode": {
            "type": "string",
            "enum": list(SHARE_MODES),
            "description": "upload：上传到群空间；attachment：作为附件发送",
        },
        "caption": {"type": "string", "description": "可选，随文件公开的说明，最多 4096 字符"},
    },
    "required": ["file_path", "share_mode"],
}
_FAILURES = {
    "forbidden": "无权读取该路径，未登记",
    "unknown": "暂时无法确认读取授权，未登记",
    "not_found": "文件不存在，未登记",
    "unavailable": "文件暂时无法读取，未登记",
    "not_a_file": "该路径不是普通文件，未登记",
    "too_large": "文件超过交付大小上限，未登记",
    "invalid_arguments": (
        "参数无效，未登记：file_path 须为办公空间内的相对路径（最多 1024 字符），"
        "share_mode 只能是 upload 或 attachment，caption 最多 4096 字符"
    ),
    "too_many_files": f"本次交付最多登记 {MAX_REGISTRATIONS} 个文件，未登记",
}


def _failure(status: str) -> ToolOutput:
    safe_status = status if status in _FAILURES else "unknown"
    return ToolOutput(success=False, data={"status": safe_status}, error=_FAILURES[safe_status])


def _arguments(inputs: Any) -> tuple[str, str, str | None] | None:
    if not isinstance(inputs, Mapping):
        return None
    file_path, share_mode, caption = inputs.get("file_path"), inputs.get("share_mode"), inputs.get("caption")
    if isinstance(caption, str) and not caption.strip():
        caption = None
    if not isinstance(file_path, str) or not file_path or share_mode not in SHARE_MODES or not valid_caption(caption):
        return None
    return file_path, share_mode, caption


class ShareFileTool(Tool):
    """List one file for this Execution's handoff; the table is read when the Execution ends."""

    def __init__(
        self,
        *,
        execution: ExecutionContext,
        workspace_access: WorkspaceAccessPort,
        registrations: dict,
        max_bytes: int,
    ) -> None:
        super().__init__(
            ToolCard(
                id=f"PersistentShareFileTool_{uuid4().hex}",
                name=SHARE_FILE,
                description=_DESCRIPTION,
                input_params=_INPUT_PARAMS,
                idempotent=True,
            )
        )
        self._execution = execution
        self._workspace_access = workspace_access
        self._registrations = registrations
        self._max_bytes = max_bytes

    async def invoke(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> ToolOutput:
        arguments = _arguments(inputs)
        if arguments is None:
            return _failure("invalid_arguments")
        file_path, share_mode, caption = arguments
        try:
            scope = await read_scope(self._execution, self._workspace_access)
            resolved = resolve_shared_path(scope, file_path)
            if not is_logical_path(resolved.relative_path):
                return _failure("invalid_arguments")
            await asyncio.to_thread(check_shared_file, resolved, self._max_bytes)
        except HandoffFileError as exc:
            return _failure(exc.status)
        registration = Registration(resolved.relative_path, share_mode, caption)
        if not merge_registration(self._registrations, registration):
            return _failure("too_many_files")
        return ToolOutput(success=True, data={"content": registration_result(registration)})

    async def stream(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        """Expose the same listing for callers that use the Tool stream API."""
        yield await self.invoke(inputs, **kwargs)


class ShareFileRail(DeepAgentRail):
    """Register share_file with the read capability; the listing table belongs to this Execution."""

    priority = 100

    def __init__(
        self,
        execution: ExecutionContext,
        workspace_access: WorkspaceAccessPort,
        registrations: dict,
        max_bytes: int,
    ) -> None:
        super().__init__()
        self.execution = execution
        self.workspace_access = workspace_access
        self.registrations = registrations
        self.max_bytes = max_bytes
        self.tools: list[ShareFileTool] = []

    def init(self, agent) -> None:
        if self.tools:
            return
        tool = ShareFileTool(
            execution=self.execution,
            workspace_access=self.workspace_access,
            registrations=self.registrations,
            max_bytes=self.max_bytes,
        )
        self.tools = [tool]
        agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        for tool in self.tools:
            if hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(tool.card.name)
        self.tools = []


__all__ = ["ShareFileRail", "ShareFileTool"]
