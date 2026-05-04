# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Lite memory manager bootstrap (used by MemoryRail)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

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
) -> Optional[MemoryIndexManager]:
    """Initialize memory manager with file watching."""
    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None

    memory_dir = str(workspace.get_node_path("memory")) if workspace.get_node_path("memory") else ""
    settings = create_memory_settings(memory_dir)
    try:
        params = MemoryManagerParams(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
        )
        manager = await MemoryIndexManager.get(params)
        if manager:
            logger.info(f"Memory manager initialized for: {memory_dir}")
        return manager
    except Exception as e:
        logger.error(f"Failed to initialize memory manager: {e}")
        return None


__all__ = ["init_memory_manager_async"]
