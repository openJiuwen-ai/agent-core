# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that injects workspace and context sections into system prompt builder."""
from __future__ import annotations


from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)
from openjiuwen.harness.prompts.sections.workspace import build_workspace_section as _build_workspace
from openjiuwen.harness.prompts.sections.context import (
    build_context_file_sections,
    build_tools_section,
)


_SYSTEM_CONTEXT_SECTIONS = frozenset({
    "context.agent",
    "context.soul",
    "context.identity",
    "context.user",
})

_ATTACHMENT_CONTEXT_SECTIONS = frozenset({
    "context.heartbeat",
})

_ALL_SPLIT_CONTEXT_SECTIONS = _SYSTEM_CONTEXT_SECTIONS | _ATTACHMENT_CONTEXT_SECTIONS


class ContextAssembleRail(DeepAgentRail):
    """Rail that injects workspace directory structure and context files into system prompt.

    In ``init``, captures references to ``system_prompt_builder`` and ``ability_manager``.

    In ``before_model_call``, builds and injects workspace/context/tools sections
    into the system prompt builder.
    """

    priority = 85

    def __init__(self):
        super().__init__()
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._ability_manager = None

    def init(self, agent) -> None:
        """Capture references to system_prompt_builder and ability_manager."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._ability_manager = getattr(agent, "ability_manager", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent) -> None:
        """Remove workspace, context, and tools sections from system prompt builder."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            for section in _ALL_SPLIT_CONTEXT_SECTIONS:
                self.system_prompt_builder.remove_section(section)
            self.system_prompt_builder.remove_section("tools")
            self.system_prompt_builder = None
        self.attachment_manager = None

    async def _upsert_attachment_section(self, writer, section, *, kind) -> None:
        try:
            await writer.add_from_prompt_section(
                prompt_section=section,
                kind=kind,
                source="agent_core.context_assemble_rail",
                language=self.system_prompt_builder.language,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[ContextAssembleRail] skip prompt attachment section=%s: %s", section.name, exc)

    async def _clear_attachment_section(self, writer, section: str) -> None:
        try:
            await writer.clear_section(section)
        except ValueError as exc:
            logger.warning("[ContextAssembleRail] skip clearing prompt attachment section=%s: %s", section, exc)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace directory structure and context files into messages before model call."""
        if self.system_prompt_builder is None:
            return
        writer = None
        if self.attachment_manager is None:
            logger.warning("[ContextAssembleRail] prompt attachment manager is unavailable; skip attachment sections")
        else:
            writer = self.attachment_manager.bind_context(ctx)
        workspace = self.workspace

        if workspace is None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            for section in _ALL_SPLIT_CONTEXT_SECTIONS:
                self.system_prompt_builder.remove_section(section)
            self.system_prompt_builder.remove_section("tools")
            if writer is not None:
                await self._clear_attachment_section(writer, "context")
                for section in _ATTACHMENT_CONTEXT_SECTIONS:
                    await self._clear_attachment_section(writer, section)
            return

        lang = self.system_prompt_builder.language
        workspace_section = await _build_workspace(
            self.sys_operation,
            workspace,
            lang,
        )
        tools_section = build_tools_section(self._ability_manager, lang)
        context_sections = await build_context_file_sections(
            self.sys_operation,
            workspace,
            lang,
        )

        if workspace_section is not None:
            self.system_prompt_builder.add_section(workspace_section)
        else:
            self.system_prompt_builder.remove_section("workspace")

        if tools_section is not None:
            self.system_prompt_builder.add_section(tools_section)
        else:
            self.system_prompt_builder.remove_section("tools")

        self.system_prompt_builder.remove_section("context")
        if writer is not None:
            await self._clear_attachment_section(writer, "context")

        for section_name in _SYSTEM_CONTEXT_SECTIONS:
            section = context_sections.get(section_name)
            if section is not None:
                self.system_prompt_builder.add_section(section)
            else:
                self.system_prompt_builder.remove_section(section_name)

        for section_name in _ATTACHMENT_CONTEXT_SECTIONS:
            section = context_sections.get(section_name)
            if section is not None:
                if writer is not None:
                    await self._upsert_attachment_section(
                        writer,
                        section,
                        kind=PromptAttachmentKind.FILE
                    )
            else:
                if writer is not None:
                    await self._clear_attachment_section(writer, section_name)
