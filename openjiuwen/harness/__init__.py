# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgents public API."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.task_loop.task_loop_event_executor import (
        TaskLoopEventExecutor,
    )
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.harness.schema.config import (
        AudioModelConfig,
        DeepAgentConfig,
        VisionModelConfig,
    )
    from openjiuwen.harness.workspace.workspace import Workspace

__all__ = [
    "DeepAgent",
    "TaskLoopEventHandler",
    "TaskLoopEventExecutor",
    "DeepAgentConfig",
    "AudioModelConfig",
    "VisionModelConfig",
    "create_deep_agent",
    "Workspace",
]


def __getattr__(name: str) -> Any:
    """Lazily import heavy modules on demand."""
    if name == "DeepAgent":
        from openjiuwen.harness.deep_agent import (
            DeepAgent,
        )
        return DeepAgent
    if name == "TaskLoopEventHandler":
        from openjiuwen.harness.task_loop.task_loop_event_handler import (
            TaskLoopEventHandler,
        )
        return TaskLoopEventHandler
    if name == "TaskLoopEventExecutor":
        from openjiuwen.harness.task_loop.task_loop_event_executor import (
            TaskLoopEventExecutor,
        )
        return TaskLoopEventExecutor
    if name == "DeepAgentConfig":
        from openjiuwen.harness.schema.config import (
            DeepAgentConfig,
        )
        return DeepAgentConfig
    if name == "AudioModelConfig":
        from openjiuwen.harness.schema.config import (
            AudioModelConfig,
        )
        return AudioModelConfig
    if name == "VisionModelConfig":
        from openjiuwen.harness.schema.config import (
            VisionModelConfig,
        )
        return VisionModelConfig
    if name == "create_deep_agent":
        from openjiuwen.harness.factory import (
            create_deep_agent,
        )
        return create_deep_agent
    if name == "Workspace":
        from openjiuwen.harness.workspace.workspace import (
            Workspace,
        )
        return Workspace
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
