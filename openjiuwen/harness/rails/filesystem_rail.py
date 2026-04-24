# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools import BashTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)


class FileSystemRail(DeepAgentRail):
    """Rail for registering filesystem, shell and code tools."""

    priority = 100

    def __init__(self, *, with_code_tool: bool = False) -> None:
        super().__init__()
        self.tools = None
        self._with_code_tool = with_code_tool

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        workspace_path = str(self.workspace.root_path) if self.workspace else None
        read_tool = ReadFileTool(self.sys_operation, lang, agent_id)
        write_tool = WriteFileTool(self.sys_operation, lang, agent_id, workspace_path=workspace_path)
        edit_tool = EditFileTool(self.sys_operation, lang, agent_id, workspace_path=workspace_path)
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)
        bash_tool = BashTool(self.sys_operation, lang, agent_id=agent_id)

        self.tools = [
            read_tool,
            write_tool,
            edit_tool,
            glob_tool,
            list_dir_tool,
            grep_tool,
            bash_tool,
        ]

        if self._with_code_tool:
            self.tools.append(CodeTool(self.sys_operation, lang, agent_id))

        Runner.resource_mgr.add_tool(self.tools)

        for tool in self.tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent):
        if self.tools:
            for tool in self.tools:
                name = getattr(tool.card, 'name', None)
                if name and hasattr(agent, 'ability_manager'):
                    agent.ability_manager.remove(name)
                
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)


    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx


__all__ = [
    "FileSystemRail",
]
