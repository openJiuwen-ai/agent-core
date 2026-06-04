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
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    ElementKind,
    context_field,
    harness_element,
    param_field,
)
from openjiuwen.harness.cli.rails.token_tracker import TokenTrackingRail
from openjiuwen.harness.cli.rails.tool_tracker import ToolTrackingRail
from openjiuwen.harness.lsp import InitializeOptions
from openjiuwen.harness.rails import (
    HeartbeatRail,
    LspRail,
    SecurityRail,
    SkillUseRail,
    SubagentRail,
    TaskPlanningRail,
)
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools.web_tools import WebFetchWebpageTool, WebFreeSearchTool
from openjiuwen.harness.tools.worktree import WorktreeConfig, WorktreeRail

# Element name constants — the RailSpec / BuiltinToolSpec ``type`` values. Every
# openjiuwen built-in element lives under the ``core.`` namespace (parallel to a
# platform's ``swarm.*`` namespace), so a spec ``type`` unambiguously names its
# owning layer.
TASK_PLANNING = "core.task_planning"
SKILL_USE = "core.skill_use"
SUBAGENT = "core.subagent"
SYS_OPERATION = "core.sys_operation"
SECURITY = "core.security"
HEARTBEAT = "core.heartbeat"
WORKTREE = "core.worktree"
LSP = "core.lsp"
TOKEN_TRACKING = "core.token_tracking"
TOOL_TRACKING = "core.tool_tracking"
ASK_USER = "core.ask_user"
CONFIRM_INTERRUPT = "core.confirm_interrupt"
WEB_SEARCH = "core.web_search"
WEB_FETCH = "core.web_fetch"


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


class ConfirmInterruptInput(ConstructionInput):
    """Construction inputs for the confirm-interrupt rail."""

    tool_names: list[str] = param_field(
        default_factory=list,
        description="Tool names that require user confirmation before running.",
    )


class WorktreeInput(ConstructionInput):
    """Construction inputs for the git worktree rail."""

    enabled: bool = param_field(
        default=False,
        description="Whether the worktree workflow (enter / exit tools) is enabled.",
    )


def _build_worktree_rail(params: dict[str, Any], context: Any) -> WorktreeRail:
    """Build a WorktreeRail from a serializable worktree config block.

    Args:
        params: Spec params matching ``WorktreeConfig`` fields (e.g. ``enabled``).
        context: Per-member build context (unused).

    Returns:
        A configured ``WorktreeRail``.
    """
    return WorktreeRail(config=WorktreeConfig(**dict(params or {})))


class LspInput(ConstructionInput):
    """Construction inputs for the LSP rail."""

    project_dir: str | None = context_field(
        attr="project_dir",
        default=None,
        description="Project root the language server operates in.",
    )


def _build_lsp_rail(params: dict[str, Any], context: Any) -> LspRail:
    """Build an LspRail rooted at the build context's project directory.

    Args:
        params: Spec params (unused).
        context: Per-member build context; ``project_dir`` roots the LSP.

    Returns:
        A configured ``LspRail``.
    """
    inp = LspInput.resolve(params, context)
    return LspRail(InitializeOptions(cwd=inp.project_dir))


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
    name=SYS_OPERATION,
    description="System-operation rail (shell / file / sandbox operation tools).",
    builder=SysOperationRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=SKILL_USE,
    description="Skill discovery / use rail; resolves skills_dir from the workspace.",
    builder=_build_skill_use_rail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=TOKEN_TRACKING,
    description="Tracks token usage across the run (CLI).",
    builder=TokenTrackingRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=TOOL_TRACKING,
    description="Tracks tool calls across the run (CLI).",
    builder=ToolTrackingRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=ASK_USER,
    description="Interrupt rail that asks the user for input.",
    builder=AskUserRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=CONFIRM_INTERRUPT,
    description="Interrupt rail that confirms tool calls with the user.",
    input_model=ConfirmInterruptInput,
    builder=ConfirmInterruptRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=SECURITY,
    description="Injects the safety / security prompt segment.",
    builder=SecurityRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=HEARTBEAT,
    description="Heartbeat rail keeping long-running agents alive.",
    builder=HeartbeatRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=WORKTREE,
    description="Git worktree rail (enter_worktree / exit_worktree tools).",
    input_model=WorktreeInput,
    builder=_build_worktree_rail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=LSP,
    description="Language-server rail rooted at the project directory.",
    input_model=LspInput,
    builder=_build_lsp_rail,
)
harness_element(
    kind=ElementKind.TOOL,
    name=WEB_SEARCH,
    description="Free web search tool.",
    builder=WebFreeSearchTool,
)
harness_element(
    kind=ElementKind.TOOL,
    name=WEB_FETCH,
    description="Web page fetch tool.",
    builder=WebFetchWebpageTool,
)


__all__ = [
    "TASK_PLANNING",
    "SKILL_USE",
    "SUBAGENT",
    "SYS_OPERATION",
    "SECURITY",
    "HEARTBEAT",
    "WORKTREE",
    "LSP",
    "TOKEN_TRACKING",
    "TOOL_TRACKING",
    "ASK_USER",
    "CONFIRM_INTERRUPT",
    "WEB_SEARCH",
    "WEB_FETCH",
]
