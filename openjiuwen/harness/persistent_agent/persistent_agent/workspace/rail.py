# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 rail that exposes only guarded workspace read tools."""

from __future__ import annotations

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.filesystem import ListDirTool, ReadFileTool

from ..schema.execution import ExecutionContext
from ..tools.workspace_read import PersistentWorkspaceListTool, PersistentWorkspaceReadTool
from .access import WorkspaceAccessPort


class PersistentWorkspaceReadRail(DeepAgentRail):
    """Register the PA-14 ``read_file`` and ``list_files`` abilities only."""

    priority = 100

    def __init__(self, execution: ExecutionContext, workspace_access: WorkspaceAccessPort) -> None:
        super().__init__()
        self.execution = execution
        self.workspace_access = workspace_access
        self.tools = []

    def init(self, agent) -> None:
        if self.tools:
            return
        if self.sys_operation is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="workspace_read requires enable_sys_operation",
            )

        language = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        read_tool = PersistentWorkspaceReadTool(
            base_tool=ReadFileTool(self.sys_operation, language, agent_id),
            execution=self.execution,
            workspace_access=self.workspace_access,
        )
        list_tool = PersistentWorkspaceListTool(
            base_tool=ListDirTool(self.sys_operation, language, agent_id),
            execution=self.execution,
            workspace_access=self.workspace_access,
        )
        self.tools = [read_tool, list_tool]
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)
        self.tools = []


__all__ = ["PersistentWorkspaceReadRail"]
