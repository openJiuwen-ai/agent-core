# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

from typing import Optional, Dict, Any, List, TYPE_CHECKING

from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.harness.workspace.workspace import Workspace

from .manager import MemoryIndexManager, MemoryManagerParams
from .config import create_memory_settings, is_memory_enabled
from .memory_tool_context import MemoryToolContext
from .memory_tool_ops import (
    validate_memory_path,
    memory_search_with_context,
    memory_get_with_context,
    write_memory_with_context,
    edit_memory_with_context,
    read_memory_with_context,
)

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

_default_context: Optional[MemoryToolContext] = None


def bind_memory_runtime(
    workspace: Workspace,
    sys_operation: Optional["SysOperation"] = None,
    *,
    agent_id: str = "default",
    embedding_config: Optional[EmbeddingConfig] = None,
    manager: Optional[MemoryIndexManager] = None,
) -> None:
    """Attach runtime for tests or tooling without ``init_memory_manager_async``."""
    global _default_context
    memory_dir = str(workspace.get_node_path("memory") or "")
    settings = create_memory_settings(memory_dir)
    _default_context = MemoryToolContext(
        workspace=workspace,
        settings=settings,
        agent_id=agent_id,
        embedding_config=embedding_config,
        sys_operation=sys_operation,
        manager=manager,
        node_name="memory",
    )


def clear_memory_runtime() -> None:
    """Clear default memory context (e.g. test teardown)."""
    global _default_context
    _default_context = None


def _validate_memory_path(path: str, workspace: Optional["Workspace"] = None) -> tuple[bool, str]:
    ws = workspace or (_default_context.workspace if _default_context else None)
    if ws is None:
        return (False, "Workspace not initialized")
    return validate_memory_path(path, ws)


def get_embedding_config() -> Optional[EmbeddingConfig]:
    return _default_context.embedding_config if _default_context else None


def get_sys_operation() -> Optional["SysOperation"]:
    return _default_context.sys_operation if _default_context else None


async def init_memory_manager_async(
    workspace: Workspace,
    agent_id: str = "default",
    embedding_config: Optional[EmbeddingConfig] = None,
    sys_operation: Optional["SysOperation"] = None,
) -> Optional[MemoryIndexManager]:
    """Initialize memory manager with file watching."""
    global _default_context

    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None

    if _default_context is not None and _default_context.manager is not None:
        return _default_context.manager

    memory_dir = str(workspace.get_node_path("memory")) if workspace.get_node_path("memory") else ""
    settings = create_memory_settings(memory_dir)

    ctx = MemoryToolContext(
        workspace=workspace,
        settings=settings,
        agent_id=agent_id,
        embedding_config=embedding_config,
        sys_operation=sys_operation,
        manager=None,
        node_name="memory",
    )

    try:
        params = MemoryManagerParams(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
        )
        ctx.manager = await MemoryIndexManager.get(params)

        _default_context = ctx

        if ctx.manager:
            logger.info(f"Memory manager initialized for: {memory_dir}")

        return ctx.manager

    except Exception as e:
        logger.error(f"Failed to initialize memory manager: {e}")
        return None


@tool(
    name="memory_search",
    description=(
        "Search long-term memory for prior work, decisions, dates, people, preferences, or todos."
    ),
)
async def memory_search(
    query: str,
    max_results: Optional[int] = None,
    min_score: Optional[float] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Search indexed long-term memory (delegates to MemoryToolContext)."""
    return await memory_search_with_context(
        _default_context,
        query,
        max_results=max_results,
        min_score=min_score,
        session_key=session_key,
    )


@tool
async def memory_get(
    path: str,
    from_line: Optional[int] = None,
    lines: Optional[int] = None,
) -> Dict[str, Any]:
    """Read a slice of a markdown file under memory/."""
    return await memory_get_with_context(_default_context, path, from_line=from_line, lines=lines)


@tool
async def write_memory(path: str, content: str, append: bool = False) -> Dict[str, Any]:
    """Create or update a memory markdown file."""
    return await write_memory_with_context(_default_context, path, content, append=append)


@tool
async def edit_memory(path: str, old_text: str, new_text: str) -> Dict[str, Any]:
    """Exact-string edit inside a memory file."""
    return await edit_memory_with_context(_default_context, path, old_text, new_text)


@tool
async def read_memory(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Read lines from a memory markdown file."""
    return await read_memory_with_context(_default_context, path, offset=offset, limit=limit)


def get_decorated_tools() -> List:
    """Return decorated LocalFunction tools for registration."""
    return [memory_search, memory_get, write_memory, edit_memory, read_memory]
