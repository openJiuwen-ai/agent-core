# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import os
from typing import Optional

from openjiuwen.core.runner import Runner
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


class SysOperationRail(DeepAgentRail):
    """Rail for registering filesystem, shell and code tools."""

    priority = 100

    def __init__(
        self,
        *,
        with_code_tool: bool = False,
        read_only: bool = False,
        enable_read_image_multimodal: Optional[bool] = None,
    ) -> None:
        super().__init__()
        self.tools = None
        self._with_code_tool = with_code_tool
        self._read_only = read_only
        self._enable_read_image_multimodal = enable_read_image_multimodal

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        enable_read_image_multimodal = self._enable_read_image_multimodal
        if enable_read_image_multimodal is None:
            deep_config = getattr(agent, "deep_config", None)
            enable_read_image_multimodal = bool(
                getattr(deep_config, "enable_read_image_multimodal", True)
            )
        read_tool = ReadFileTool(
            self.sys_operation,
            lang,
            agent_id,
            enable_image_multimodal=enable_read_image_multimodal,
        )
        write_tool = WriteFileTool(self.sys_operation, lang, agent_id)
        edit_tool = EditFileTool(self.sys_operation, lang, agent_id)
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)
        bash_tool = BashTool(self.sys_operation, lang, agent_id=agent_id)
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

        # 工具 id 形如 "WriteFileTool_<agent_id>" 与 SysOperation 实例无关; 若上一次 agent
        # 生命周期里的同名工具仍残留在 resource_mgr (例如 adapter cleanup 未驱动 rail.uninit),
        # 直接 add_tool 会因 resource_already_exist 静默失败, 旧工具实例继续持有过期的
        # SysOperation 引用, 导致 SANDBOX 切换时 fs/shell 调用走 LOCAL 并写穿宿主。
        # 这里与 SkillUseRail.init 同款 idempotent 注册: 已存在则先 remove, 再 add。
        for tool in self.tools:
            if Runner.resource_mgr.get_tool(tool.card.id) is not None:
                Runner.resource_mgr.remove_tool(tool.card.id)
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
    "SysOperationRail",
]
