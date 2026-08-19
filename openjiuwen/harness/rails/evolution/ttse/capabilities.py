# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dynamically enumerate the agent's capabilities for the induce prompt.

Replaces the reference ``capabilities.py`` static catalog with a live view:
the agent's currently registered tools (``AbilityManager.list_tool_info``) and
skills (``SkillManager.get_all``). TIPs may only reference these real names, so
induction stays grounded in what the agent can actually do.

When no agent is available (e.g. async background induction without a captured
snapshot) the renderer falls back to a small built-in tool list so TIPs still
name valid capabilities.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

# Fallback capability set used only when the agent cannot be introspected.
# Mirrors jiuwen's common built-in coding tools.
BASIC_TOOLS: List[Tuple[str, str]] = [
    ("bash", "run shell commands in the workspace"),
    ("read_file", "read a file's contents"),
    ("write_file", "write or overwrite a file"),
    ("edit_file", "targeted string replacement inside a file"),
    ("grep", "search file contents"),
    ("python_exec", "run python for computation, parsing, or data processing"),
    ("web_search", "search the web and read results"),
]


def _name_desc(obj: Any) -> Tuple[str, str]:
    name = getattr(obj, "name", None)
    desc = getattr(obj, "description", None)
    if name is None and isinstance(obj, dict):
        name = obj.get("name")
        desc = obj.get("description")
    return (str(name) if name else "", str(desc or "")[:130])


async def _enumerate_tools(agent: Any) -> List[Tuple[str, str]]:
    ability_manager = getattr(agent, "ability_manager", None)
    list_info = getattr(ability_manager, "list_tool_info", None)
    if not callable(list_info):
        return []
    try:
        infos = await list_info()
    except Exception:  # noqa: BLE001 - degrade gracefully
        return []
    out: List[Tuple[str, str]] = []
    for info in infos or []:
        name, desc = _name_desc(info)
        if name:
            out.append((name, desc))
    return out


def _enumerate_skills(agent: Any) -> List[Tuple[str, str]]:
    skill_manager = getattr(agent, "skill_manager", None)
    get_all = getattr(skill_manager, "get_all", None)
    if not callable(get_all):
        return []
    try:
        skills = get_all()
    except Exception:  # noqa: BLE001
        return []
    out: List[Tuple[str, str]] = []
    for skill in skills or []:
        name, desc = _name_desc(skill)
        if name:
            out.append((name, desc))
    return out


async def render_capabilities(agent: Optional[Any] = None) -> str:
    """Render the Available Capabilities block fed to the induce prompt.

    Accepts an agent or an :class:`AgentCallbackContext` (unwrapped via
    ``.agent``). When the agent is None or exposes no abilities, a built-in
    tool list is rendered so TIPs still reference valid capability names.
    """
    if agent is not None and not hasattr(agent, "ability_manager"):
        agent = getattr(agent, "agent", agent)

    skills: List[Tuple[str, str]] = []
    tools: List[Tuple[str, str]] = []
    if agent is not None:
        skills = _enumerate_skills(agent)
        tools = await _enumerate_tools(agent)
    if not skills and not tools:
        tools = list(BASIC_TOOLS)

    lines = ["BUILT-IN SKILLS (load a skill's SKILL.md with the read tool when its description matches your task):"]
    for name, desc in skills:
        lines.append(f"- skill `{name}`: {desc}")
    lines.append("")
    lines.append("BASIC TOOLS (always available, no loading needed):")
    for name, desc in tools:
        lines.append(f"- tool `{name}`: {desc}")
    return "\n".join(lines)


__all__ = ["render_capabilities", "BASIC_TOOLS"]
