# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.memory.lite.coding_memory_tool_ops import (
    coding_memory_edit_with_context,
    coding_memory_read_with_context,
    coding_memory_write_with_context,
)
from openjiuwen.core.memory.lite.config import create_memory_settings, is_memory_enabled
from openjiuwen.core.memory.lite.manager import MemoryIndexManager, MemoryManagerParams
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.lite.memory_tool_ops import (
    edit_memory_with_context,
    memory_get_with_context,
    memory_search_with_context,
    read_memory_with_context,
    write_memory_with_context,
)

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation
    from openjiuwen.harness.workspace.workspace import Workspace


class MemberMemoryToolkit:

    def __init__(
        self,
        member_name: str,
        team_name: str,
        workspace: "Workspace",
        scenario: str = "general",
        embedding_config: Optional[EmbeddingConfig] = None,
        sys_operation: Optional["SysOperation"] = None,
        read_only: bool = False,
    ) -> None:
        self._member_name = member_name
        self._team_name = team_name
        self._workspace = workspace
        self._scenario = (scenario or "general").strip().lower()
        self._embedding_config = embedding_config
        self._sys_operation = sys_operation
        self._read_only = read_only
        self._manager: Optional[MemoryIndexManager] = None
        self._ctx: Optional[Union[MemoryToolContext, CodingMemoryToolContext]] = None
        self._tools: List[LocalFunction] = []
        self._initialized = False

    async def initialize(self) -> bool:
        if self._initialized and self._manager is not None and not self._manager.closed:
            return True

        if not is_memory_enabled():
            logger.info("[MemberMemoryToolkit] Memory system is disabled")
            return False

        agent_id = f"{self._team_name}.{self._member_name}"
        node_name = "coding_memory" if self._scenario == "coding" else "memory"
        node_path = self._workspace.get_node_path(node_name)
        memory_dir = str(node_path) if node_path else ""
        settings = create_memory_settings(memory_dir)

        try:
            params = MemoryManagerParams(
                agent_id=agent_id,
                workspace=self._workspace,
                settings=settings,
                embedding_config=self._embedding_config,
                sys_operation=self._sys_operation,
                node_name=node_name,
            )
            self._manager = await MemoryIndexManager.get(params)
        except Exception as e:
            logger.error(f"[MemberMemoryToolkit] Failed to get manager: {e}")
            self._manager = None
            return False

        if not self._manager:
            return False

        if self._scenario == "coding":
            self._ctx = CodingMemoryToolContext(
                workspace=self._workspace,
                settings=settings,
                agent_id=agent_id,
                embedding_config=self._embedding_config,
                sys_operation=self._sys_operation,
                manager=self._manager,
                coding_memory_dir=memory_dir,
                node_name="coding_memory",
            )
            self._tools = _create_coding_tools(self, self._read_only)
        else:
            self._ctx = MemoryToolContext(
                workspace=self._workspace,
                settings=settings,
                agent_id=agent_id,
                embedding_config=self._embedding_config,
                sys_operation=self._sys_operation,
                manager=self._manager,
            )
            self._tools = _create_general_tools(self, self._read_only)

        self._initialized = True
        return True

    @property
    def manager(self) -> Optional[MemoryIndexManager]:
        return self._manager

    def get_tools(self) -> List[LocalFunction]:
        return list(self._tools)

    def get_tool_cards(self) -> List[ToolCard]:
        return [t.card for t in self._tools]

    async def close(self) -> None:
        if self._manager is not None:
            try:
                await self._manager.close()
            except Exception as e:
                logger.warning(f"[MemberMemoryToolkit] close failed: {e}")
            finally:
                self._manager = None
        self._ctx = None
        self._tools = []
        self._initialized = False

    @property
    def ctx(self):
        return self._ctx

    @property
    def team_name(self):
        return self._team_name

    @property
    def member_name(self):
        return self._member_name


def _create_general_tools(toolkit: MemberMemoryToolkit, read_only: bool) -> List[LocalFunction]:
    ctx = toolkit.ctx
    if not isinstance(ctx, MemoryToolContext):
        raise TypeError(f"Expected MemoryToolContext, got {type(ctx).__name__}")
    pfx = f"memory.{toolkit.team_name}.{toolkit.member_name}"

    @tool(
        name="memory_search",
        card=ToolCard(id=f"{pfx}.memory_search", name="memory_search"),
        input_params={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query text"},
                "max_results": {"type": "integer", "description": "max number of results to return"},
                "min_score": {"type": "number", "description": "minimum similarity score threshold"},
                "session_key": {"type": "string", "description": "optional session key for context"}
            },
            "required": ["query"]
        }
    )
    async def memory_search(
        query: str,
        max_results: Optional[int] = None,
        min_score: Optional[float] = None,
        session_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await memory_search_with_context(
            ctx,
            query,
            max_results=max_results,
            min_score=min_score,
            session_key=session_key,
        )

    @tool(
        name="memory_get",
        card=ToolCard(id=f"{pfx}.memory_get", name="memory_get"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path"},
                "from_line": {"type": "integer", "description": "start line number"},
                "lines": {"type": "integer", "description": "number of lines to read"}
            },
            "required": ["path"]
        }
    )
    async def memory_get(
        path: str,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await memory_get_with_context(ctx, path, from_line=from_line, lines=lines)

    @tool(
        name="read_memory",
        card=ToolCard(id=f"{pfx}.read_memory", name="read_memory"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path"},
                "offset": {"type": "integer", "description": "start line offset"},
                "limit": {"type": "integer", "description": "max lines to read"}
            },
            "required": ["path"]
        }
    )
    async def read_memory(
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await read_memory_with_context(ctx, path, offset=offset, limit=limit)

    tools: List[LocalFunction] = [memory_search, memory_get, read_memory]

    if read_only:
        return tools

    @tool(
        name="write_memory",
        card=ToolCard(id=f"{pfx}.write_memory", name="write_memory"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path"},
                "content": {"type": "string", "description": "content to be write"},
                "append": {"type": "boolean", "default": False, "description": "append to file or overwrite"}
            },
            "required": ["path", "content"]
        },
    )
    async def write_memory(
        path: str,
        content: str,
        append: bool = False,
    ) -> Dict[str, Any]:
        return await write_memory_with_context(ctx, path, content, append=append)

    @tool(
        name="edit_memory",
        card=ToolCard(id=f"{pfx}.edit_memory", name="edit_memory"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path"},
                "old_text": {"type": "string", "description": "old memory in file"},
                "new_text": {"type": "string", "description": "new memory to be write"},
            },
            "required": ["path", "old_text", "new_text"]  # 修正
        },
    )
    async def edit_memory(path: str, old_text: str, new_text: str) -> Dict[str, Any]:
        return await edit_memory_with_context(ctx, path, old_text, new_text)

    tools.extend([write_memory, edit_memory])
    return tools


def _create_coding_tools(toolkit: MemberMemoryToolkit, read_only: bool) -> List[LocalFunction]:
    ctx = toolkit.ctx
    if not isinstance(ctx, CodingMemoryToolContext):
        raise TypeError(f"Expected CodingMemoryToolContext, got {type(ctx).__name__}")
    pfx = f"coding_memory.{toolkit.team_name}.{toolkit.member_name}"

    @tool(
        name="coding_memory_read",
        card=ToolCard(id=f"{pfx}.coding_memory_read", name="coding_memory_read"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path to read"},
                "offset": {"type": "integer", "description": "start line offset"},
                "limit": {"type": "integer", "description": "max number of lines to read"}
            },
            "required": ["path"]
        }
    )
    async def coding_memory_read(
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await coding_memory_read_with_context(ctx, path, offset=offset, limit=limit)

    tools: List[LocalFunction] = [coding_memory_read]

    if read_only:
        return tools

    @tool(
        name="coding_memory_write",
        card=ToolCard(id=f"{pfx}.coding_memory_write", name="coding_memory_write"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path to write"},
                "content": {"type": "string", "description": "content to write"}
            },
            "required": ["path", "content"]
        }
    )
    async def coding_memory_write(path: str, content: str) -> Dict[str, Any]:
        return await coding_memory_write_with_context(ctx, path, content)

    @tool(
        name="coding_memory_edit",
        card=ToolCard(id=f"{pfx}.coding_memory_edit", name="coding_memory_edit"),
        input_params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path to edit"},
                "old_text": {"type": "string", "description": "old text to be replaced"},
                "new_text": {"type": "string", "description": "new text to replace with"}
            },
            "required": ["path", "old_text", "new_text"]
        }
    )
    async def coding_memory_edit(
        path: str, old_text: str, new_text: str
    ) -> Dict[str, Any]:
        return await coding_memory_edit_with_context(ctx, path, old_text, new_text)

    tools.extend([coding_memory_write, coding_memory_edit])
    return tools


__all__ = [
    "MemberMemoryToolkit",
    "_create_coding_tools",
    "_create_general_tools",
]
