# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that injects workspace and context sections into system prompt builder."""
from __future__ import annotations


from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections.workspace import build_workspace_section as _build_workspace
from openjiuwen.harness.prompts.sections.context import build_context_section as _build_context, \
    build_tools_section



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
        self._ability_manager = None

    def init(self, agent) -> None:
        """Capture references to system_prompt_builder and ability_manager."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._ability_manager = getattr(agent, "ability_manager", None)

    def uninit(self, agent) -> None:
        """Remove workspace, context, and tools sections from system prompt builder."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
        self.system_prompt_builder = None
        self._ability_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace directory structure and context files into messages before model call."""
        if self.system_prompt_builder is None:
            return
        workspace = self.workspace

        if workspace is None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            return

        lang = self.system_prompt_builder.language
        workspace_section = await _build_workspace(
            self.sys_operation,
            workspace,
            lang,
        )
        tools_section = build_tools_section(self._ability_manager, lang)
        context_section = await _build_context(
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

        if context_section is not None:
            self.system_prompt_builder.add_section(context_section)
        else:
            self.system_prompt_builder.remove_section("context")



