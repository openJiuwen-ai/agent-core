# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manifest declarations for openjiuwen's built-in DeepAgent rails / tools.

These are the rails / tools that ``RailSpec`` / ``BuiltinToolSpec`` reference by
name (``task_planning`` / ``skill_use`` / ``web_search`` / ...). They are
declared here through the manifest ``@harness_element`` mechanism so every
capability — built-in or team-specific — resolves through the single provider
registry; the legacy ``_RAIL_TYPE_REGISTRY`` / ``_TOOL_TYPE_REGISTRY`` class
registries are gone.

Most rails take no required constructor args, so they are declared with a class
``builder`` (adapted to the ``(params, context) -> instance`` provider contract
at registration, auto-injecting ``language`` when the constructor accepts it).
``skill_use`` needs a resolved ``skills_dir`` and so uses a small factory.
Optional rails / tools (CLI trackers, interrupt rails, web tools) are declared
only when their classes are importable.
"""

from __future__ import annotations

import importlib
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ElementKind,
    harness_element,
)
from openjiuwen.harness.rails import (
    SkillUseRail,
    SubagentRail,
    TaskPlanningRail,
)
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail

# Element name constants — these are the RailSpec / BuiltinToolSpec ``type``
# values, kept identical to the legacy class-registry keys so existing specs
# resolve unchanged.
TASK_PLANNING = "task_planning"
SKILL_USE = "skill_use"
SUBAGENT = "subagent"
FILESYSTEM = "filesystem"
TOKEN_TRACKING = "token_tracking"
TOOL_TRACKING = "tool_tracking"
ASK_USER = "ask_user"
CONFIRM_INTERRUPT = "confirm_interrupt"
WEB_SEARCH = "web_search"
WEB_FETCH = "web_fetch"


def _build_skill_use_rail(params: dict[str, Any], context: Any) -> SkillUseRail:
    """Build a SkillUseRail, resolving ``skills_dir`` from the workspace.

    When ``skills_dir`` is absent from params, derive it from the build
    context's workspace ``skills/`` node plus the default CLI skill directories
    (mirrors the legacy RailSpec class branch). SkillUseRail does not accept a
    ``language`` argument.

    Args:
        params: Spec params (may carry ``skills_dir`` / ``skill_mode`` / ...).
        context: Per-member build context; ``context.workspace`` resolves the
            workspace skills node.

    Returns:
        A configured ``SkillUseRail``.
    """
    kwargs = dict(params or {})
    if "skills_dir" not in kwargs:
        dirs: list[str] = []
        workspace = getattr(context, "workspace", None) if context is not None else None
        if workspace is not None:
            skills_base = workspace.get_node_path("skills")
            if skills_base:
                dirs.append(str(skills_base))
        dirs.extend([
            "~/.openjiuwen/workspace/skills",
            "~/.claude/skills",
        ])
        kwargs["skills_dir"] = dirs
    return SkillUseRail(**kwargs)


def _try_declare(
    kind: ElementKind,
    name: str,
    module_path: str,
    cls_name: str,
    description: str,
) -> None:
    """Declare an optional element only when its class is importable.

    Args:
        kind: The element kind (RAIL / TOOL).
        name: The element name (= spec ``type``).
        module_path: Dotted module path to import.
        cls_name: Class attribute to read from the module.
        description: Human-readable element description.
    """
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
    except (ImportError, AttributeError):
        return
    harness_element(kind=kind, name=name, description=description, builder=cls)


# Mandatory rails (always importable).
harness_element(
    kind=ElementKind.RAIL,
    name=TASK_PLANNING,
    description="Outer task-loop planning rail (todo list lifecycle).",
    builder=TaskPlanningRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=SUBAGENT,
    description="Registers sub-agent spawn / task tools on the agent.",
    builder=SubagentRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=FILESYSTEM,
    description="System-operation rail (shell / file / sandbox operation tools).",
    builder=SysOperationRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=SKILL_USE,
    description="Skill discovery / use rail; resolves skills_dir from the workspace.",
    builder=_build_skill_use_rail,
)

# Optional rails / tools — declared only when importable (CLI trackers, the
# interrupt rails, and the web tools are not always present).
_try_declare(
    ElementKind.RAIL,
    TOKEN_TRACKING,
    "openjiuwen.harness.cli.rails.token_tracker",
    "TokenTrackingRail",
    "Tracks token usage across the run (CLI).",
)
_try_declare(
    ElementKind.RAIL,
    TOOL_TRACKING,
    "openjiuwen.harness.cli.rails.tool_tracker",
    "ToolTrackingRail",
    "Tracks tool calls across the run (CLI).",
)
_try_declare(
    ElementKind.RAIL,
    ASK_USER,
    "openjiuwen.harness.rails.interrupt.ask_user_rail",
    "AskUserRail",
    "Interrupt rail that asks the user for input.",
)
_try_declare(
    ElementKind.RAIL,
    CONFIRM_INTERRUPT,
    "openjiuwen.harness.rails.interrupt.confirm_rail",
    "ConfirmInterruptRail",
    "Interrupt rail that confirms tool calls with the user.",
)
_try_declare(
    ElementKind.TOOL,
    WEB_SEARCH,
    "openjiuwen.harness.tools.web_tools",
    "WebFreeSearchTool",
    "Free web search tool.",
)
_try_declare(
    ElementKind.TOOL,
    WEB_FETCH,
    "openjiuwen.harness.tools.web_tools",
    "WebFetchWebpageTool",
    "Web page fetch tool.",
)


__all__ = [
    "TASK_PLANNING",
    "SKILL_USE",
    "SUBAGENT",
    "FILESYSTEM",
    "TOKEN_TRACKING",
    "TOOL_TRACKING",
    "ASK_USER",
    "CONFIRM_INTERRUPT",
    "WEB_SEARCH",
    "WEB_FETCH",
]
