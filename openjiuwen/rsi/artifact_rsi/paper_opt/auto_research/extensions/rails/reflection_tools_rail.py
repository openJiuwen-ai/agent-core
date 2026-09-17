"""Read + write filesystem rail for the Reflection Agent.

Must-have context (the design story, implementation assumptions, final
per-variant metrics) is preloaded straight into the task prompt — no tool
call needed for any of that (see agent.py's _build_task_prompt). This rail
is for what *isn't* preloaded: read_file/list_files let the agent pull in
extra detail (a variant's full run log, the raw design doc, the generated
code itself) only if it decides the preloaded context genuinely isn't
enough, rather than the host guessing in advance what might matter and
inlining every possible source. write_file is unchanged from the module's
original write-only rail — the agent still authors its own markdown artifact
directly (see docs/reflection_design.md "Aspects" §3).
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool

ALLOWED_TOOL_NAMES = frozenset({"read_file", "list_files", "write_file"})
FORBIDDEN_TOOL_NAMES = frozenset({"edit_file", "glob", "grep", "bash", "powershell", "code"})


class ReflectionToolsRail(DeepAgentRail):
    """read_file + list_files (optional extra context) + write_file (the artifact)."""

    priority = 100

    def __init__(self) -> None:
        super().__init__()
        self.tools: list[Any] | None = None

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        read_tool = ReadFileTool(self.sys_operation, lang, agent_id, enable_image_multimodal=False)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        write_tool = WriteFileTool(self.sys_operation, lang, agent_id)

        self.tools = [read_tool, list_dir_tool, write_tool]
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
