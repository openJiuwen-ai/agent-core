# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""System-operation rail: filesystem / shell / optional code tools.

ENT currently ships ``FileSystemRail`` for plan/code adapters. Team/swarm
specs reference ``core.sys_operation`` / ``SysOperationRail`` (agent-core
manifest). This module provides that rail so ``builtin_elements`` can load.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools import BashTool, PowerShellTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)


def _resolve_enable_read_image_multimodal(
    agent: Any,
    override: Optional[bool],
) -> bool:
    if override is not None:
        return bool(override)
    deep_config = getattr(agent, "deep_config", None)
    return bool(getattr(deep_config, "enable_read_image_multimodal", True))


class SysOperationRail(DeepAgentRail):
    """Rail for registering filesystem, shell and code tools."""

    priority = 100

    def __init__(
        self,
        *,
        with_code_tool: bool = False,
        read_only: bool = False,
        enable_read_image_multimodal: Optional[bool] = None,
        bash_deny_patterns: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.tools: list[Any] | None = None
        self._with_code_tool = with_code_tool
        self._read_only = read_only
        self._enable_read_image_multimodal = enable_read_image_multimodal
        self._bash_deny_patterns = list(bash_deny_patterns or [])

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        workspace_path = str(self.workspace.root_path) if self.workspace else None
        enable_read_image_multimodal = _resolve_enable_read_image_multimodal(
            agent,
            self._enable_read_image_multimodal,
        )
        read_tool = ReadFileTool(
            self.sys_operation,
            lang,
            agent_id,
            enable_image_multimodal=enable_read_image_multimodal,
        )
        write_tool = WriteFileTool(
            self.sys_operation, lang, agent_id, workspace_path=workspace_path
        )
        edit_tool = EditFileTool(
            self.sys_operation, lang, agent_id, workspace_path=workspace_path
        )
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)
        bash_tool = BashTool(
            self.sys_operation,
            lang,
            agent_id=agent_id,
            deny_patterns=self._bash_deny_patterns,
        )
        powershell_tool = (
            PowerShellTool(self.sys_operation, lang, agent_id=agent_id)
            if os.name == "nt"
            else None
        )

        shared = [glob_tool, list_dir_tool, grep_tool, bash_tool]
        if self._read_only:
            self.tools = [read_tool, *shared]
        else:
            self.tools = [read_tool, write_tool, edit_tool, *shared]
        if powershell_tool is not None:
            self.tools.append(powershell_tool)

        if self._with_code_tool and not self._read_only:
            self.tools.append(CodeTool(self.sys_operation, lang, agent_id))

        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        for tool in self.tools:
            if hasattr(ability_manager, "add_ability"):
                ability_manager.add_ability(tool.card, tool)
            else:
                ability_manager.add(tool.card)

    def uninit(self, agent):
        if not self.tools:
            return
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if not name:
                continue
            if hasattr(ability_manager, "remove_ability"):
                ability_manager.remove_ability(name)
            elif hasattr(ability_manager, "remove"):
                ability_manager.remove(name)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx


__all__ = [
    "SysOperationRail",
]
