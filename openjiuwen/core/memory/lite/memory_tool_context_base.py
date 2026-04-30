# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared base for lite memory runtime contexts (general ``memory`` and ``coding_memory`` nodes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.lite.config import MemorySettings
from openjiuwen.core.memory.lite.manager import MemoryIndexManager, MemoryManagerParams

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation
    from openjiuwen.harness.workspace.workspace import Workspace


@dataclass
class LiteMemoryToolContextBase:
    """Common workspace-scoped state for MemoryIndexManager-backed tool surfaces."""

    workspace: Optional["Workspace"] = None
    settings: Optional[MemorySettings] = None
    agent_id: str = "default"
    embedding_config: Optional[EmbeddingConfig] = None
    sys_operation: Optional["SysOperation"] = None
    manager: Optional[MemoryIndexManager] = None
    node_name: str = "memory"

    async def ensure_manager(self) -> bool:
        """Lazy-initialize ``manager`` when ``workspace`` is set."""
        if self.manager is not None and not getattr(self.manager, "closed", False):
            return True
        if self.workspace is None:
            return False
        try:
            self.settings = self.settings or MemorySettings()
            params = MemoryManagerParams(
                agent_id=self.agent_id,
                workspace=self.workspace,
                settings=self.settings,
                embedding_config=self.embedding_config,
                sys_operation=self.sys_operation,
                node_name=self.node_name,
            )
            self.manager = await MemoryIndexManager.get(params)
            return self.manager is not None
        except Exception as e:
            logger.error(
                f"Failed to initialize memory manager (node_name={self.node_name}): {e}"
            )
            return False


__all__ = ["LiteMemoryToolContextBase"]
