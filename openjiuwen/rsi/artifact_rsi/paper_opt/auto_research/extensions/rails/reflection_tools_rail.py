"""Read-only filesystem rail for the Reflection Agent.

Must-have context is a host-built summary in the task prompt. This rail lets
the agent pull item-level metrics, run logs, or generated code. The structured
judgment is submitted via ``submit_reflection`` registered as an extra tool;
the host renders the markdown artifact, so write_file is not available.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.filesystem import GlobTool, GrepTool, ListDirTool, ReadFileTool

ALLOWED_TOOL_NAMES = frozenset({"read_file", "list_files", "grep", "glob"})
FORBIDDEN_TOOL_NAMES = frozenset(
    {"write_file", "edit_file", "bash", "powershell", "code"}
)


class ReflectionToolsRail(DeepAgentRail):
    """read_file + list_files + grep + glob for optional extra context."""

    priority = 100

    def __init__(self) -> None:
        super().__init__()
        self.tools: list[Any] | None = None

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        read_tool = ReadFileTool(self.sys_operation, lang, agent_id, enable_image_multimodal=False)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)

        self.tools = [read_tool, list_dir_tool, grep_tool, glob_tool]
        names = {getattr(tool.card, "name", None) for tool in self.tools}
        unexpected = names - ALLOWED_TOOL_NAMES
        if unexpected:
            raise RuntimeError(f"unexpected reflection tools registered: {sorted(unexpected)}")
        if names & FORBIDDEN_TOOL_NAMES:
            raise RuntimeError("forbidden edit/execution tools leaked into reflection rail")

        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if not self.tools:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)


__all__ = ["ALLOWED_TOOL_NAMES", "FORBIDDEN_TOOL_NAMES", "ReflectionToolsRail"]
