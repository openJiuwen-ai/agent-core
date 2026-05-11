# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coding Memory bootstrap (used by CodingMemoryRail)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.harness.workspace.workspace import Workspace

from .config import create_memory_settings, is_memory_enabled
from .manager import MemoryIndexManager, MemoryManagerParams

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation


async def init_memory_manager_async(
    workspace: Workspace,
    agent_id: str = "default",
    embedding_config: Optional[EmbeddingConfig] = None,
    sys_operation: Optional["SysOperation"] = None,
    llm: Optional[Any] = None,
) -> Optional[MemoryIndexManager]:
    """Initialize a coding-memory manager. Idempotent (MemoryIndexManager.get caches by key)."""
    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None

    node_path = workspace.get_node_path("coding_memory")
    cm_dir = str(node_path) if node_path else ""
    settings = create_memory_settings(cm_dir)

    try:
        params = MemoryManagerParams(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
            node_name="coding_memory",
        )
        manager = await MemoryIndexManager.get(params)
        if manager:
            if llm:
                manager.llm = llm
            logger.info(f"initialized Coding Memory manager for: {cm_dir}")
        return manager
    except Exception as e:
        logger.error(f"Failed to initialize Coding Memory manager: {e}")
        return None


__all__ = ["init_memory_manager_async"]
