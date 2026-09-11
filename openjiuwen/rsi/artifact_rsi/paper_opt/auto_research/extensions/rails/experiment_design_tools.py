"""Restricted filesystem rail for Experiment Design Agent.

Registers only read/search tools. Does not expose write/edit, shell,
PowerShell, or code execution.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails._multimodal import should_enable_read_image_multimodal
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.filesystem import (
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
)

ALLOWED_TOOL_NAMES = frozenset({"read_file", "glob", "grep", "list_files"})
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "write_file",
        "edit_file",
        "bash",
        "powershell",
        "code",
    }
)


class ExperimentDesignToolsRail(DeepAgentRail):
    """Read/search-only filesystem tools for experiment design."""

    priority = 100

    def __init__(self, *, enable_read_image_multimodal: bool | None = False) -> None:
        super().__init__()
        self.tools: list[Any] | None = None
        self._enable_read_image_multimodal = enable_read_image_multimodal

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        enable_read_image_multimodal = should_enable_read_image_multimodal(
            agent,
            self._enable_read_image_multimodal,
        )
        read_tool = ReadFileTool(
            self.sys_operation,
            lang,
            agent_id,
            enable_image_multimodal=enable_read_image_multimodal,
        )
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)

        self.tools = [read_tool, glob_tool, list_dir_tool, grep_tool]
        names = {getattr(tool.card, "name", None) for tool in self.tools}
        unexpected = names - ALLOWED_TOOL_NAMES
        if unexpected:
            raise RuntimeError(f"unexpected design tools registered: {sorted(unexpected)}")
        if names & FORBIDDEN_TOOL_NAMES:
            raise RuntimeError("forbidden mutation/execution tools leaked into design rail")

        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if not self.tools:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)


__all__ = [
    "ALLOWED_TOOL_NAMES",
    "FORBIDDEN_TOOL_NAMES",
    "ExperimentDesignToolsRail",
]
