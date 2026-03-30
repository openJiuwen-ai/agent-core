# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HeartbeatRail — injects heartbeat system prompt."""

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    RunKind,
)
from openjiuwen.deepagents.prompts.sections.heartbeat import (
    build_heartbeat_section,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.workspace.workspace import WorkspaceNode


class HeartbeatRail(DeepAgentRail):
    """Rail that injects heartbeat system prompt.

    Detects heartbeat runs via run.kind and injects
    heartbeat-specific system prompt section.
    """

    priority = 80

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self.heartbeat_dir = None

    def init(self, agent) -> None:
        """Initialize HeartbeatRail."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if not agent.deep_config:
            logger.info("[HeartbeatRail] No deep_config configured")
            return

        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)
        self.heartbeat_dir = str(self.workspace.get_node_path(WorkspaceNode.HEARTBEAT_MD))

    def uninit(self, agent) -> None:
        """Remove heartbeat system prompt."""
        if self.system_prompt_builder:
            self.system_prompt_builder.remove_section("heartbeat")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject heartbeat system prompt before model call."""
        if self.system_prompt_builder is None or ctx.extra.get("run_kind") != RunKind.HEARTBEAT:
            return

        if not self.sys_operation:
            logger.warning("HeartbeatRail: sys_operation not configured")
            return

        fs = self.sys_operation.fs()
        read_res = await fs.read_file(self.heartbeat_dir, mode="text")
        content = ""
        if read_res.code == 0:
            content = read_res.data.content
        else:
            logger.warning("HeartbeatRail: failed to read HEARTBEAT.md")
        heartbeat_section = build_heartbeat_section(
            language=self.system_prompt_builder.language, heartbeat_content=content)
        if heartbeat_section is not None:
            self.system_prompt_builder.add_section(heartbeat_section)
        else:
            self.system_prompt_builder.remove_section("heartbeat")


__all__ = [
    "HeartbeatRail",
]
