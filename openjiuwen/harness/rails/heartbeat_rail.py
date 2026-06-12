# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HeartbeatRail — injects heartbeat system prompt."""

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    RunKind,
)
from openjiuwen.harness.prompts.sections.heartbeat import (
    build_heartbeat_section,
)
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)
from openjiuwen.harness.rails.base import DeepAgentRail


class HeartbeatRail(DeepAgentRail):
    """Rail that injects heartbeat system prompt.

    Detects heartbeat runs via run.kind and injects
    heartbeat-specific system prompt section.
    """

    priority = 80

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self.attachment_manager = None

    def init(self, agent) -> None:
        """Initialize HeartbeatRail."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

        if not agent.deep_config:
            logger.info("[HeartbeatRail] No deep_config configured")
            return

        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)

    def uninit(self, agent) -> None:
        """Remove heartbeat system prompt."""
        if self.system_prompt_builder:
            self.system_prompt_builder.remove_section("heartbeat")
            self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject heartbeat system prompt before model call."""
        if self.system_prompt_builder is None:
            return
        if self.attachment_manager is None:
            return
        writer = self.attachment_manager.bind_context(ctx)
        if ctx.extra.get("run_kind") != RunKind.HEARTBEAT:
            try:
                await writer.clear_section("heartbeat")
            except ValueError as exc:
                logger.warning("[HeartbeatRail] skip clearing heartbeat prompt attachment: %s", exc)
            return

        heartbeat_section = build_heartbeat_section(
            language=self.system_prompt_builder.language)
        if heartbeat_section is not None:
            try:
                await writer.add_from_prompt_section(
                    prompt_section=heartbeat_section,
                    kind=PromptAttachmentKind.TODO_REMINDER,
                    source="agent_core.heartbeat_rail",
                    language=self.system_prompt_builder.language,
                    content_kind="text/markdown",
                )
            except ValueError as exc:
                logger.warning("[HeartbeatRail] skip prompt attachment section=%s: %s", heartbeat_section.name, exc)
        else:
            self.system_prompt_builder.remove_section("heartbeat")
            try:
                await writer.clear_section("heartbeat")
            except ValueError as exc:
                logger.warning("[HeartbeatRail] skip clearing heartbeat prompt attachment: %s", exc)


__all__ = [
    "HeartbeatRail",
]
