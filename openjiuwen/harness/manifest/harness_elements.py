# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Harness-unique Spec catalog elements (rails, subagents).

These capabilities are not declared in ``builtin_elements`` (shared with Team
overlap) and are not the SysOperation-backed tool groups. Cold-path Spec build
resolves them via ``ensure_builtin_elements_registered``.

Import graph: ``harness.*`` + ``core.*`` only — never ``agent_teams``.
Subagent names use the ``core.subagent.*`` prefix so they do not collide with
Team's ``core.explore_agent`` / ``core.plan_agent`` / ``core.browser_agent``.
"""

from __future__ import annotations

import os
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.manifest import (
    ConstructionInput,
    ElementKind,
    context_field,
    harness_element,
    param_field,
)
from openjiuwen.harness.prompts.tools.task_tool import GENERAL_PURPOSE_AGENT_DESC
from openjiuwen.harness.rails.agent_mode_rail import AgentModeRail
from openjiuwen.harness.rails.context_engineer.context_assemble_rail import ContextAssembleRail
from openjiuwen.harness.rails.mcp_rail import McpRail
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.rails.skills.skill_create_rail import SkillCreateRail
from openjiuwen.harness.rails.subagent.verification_rail import VerificationRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
from openjiuwen.harness.subagents.code_agent import build_code_agent_config
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config
from openjiuwen.harness.subagents.research_agent import build_research_agent_config
from openjiuwen.harness.subagents.verification_agent import build_verification_agent_config

# ---------------------------------------------------------------------------
# Element names
# ---------------------------------------------------------------------------

PROGRESSIVE_TOOL = "core.progressive_tool"
MCP = "core.mcp"
CONTEXT_ASSEMBLE = "core.context_assemble"
MEMORY = "core.memory"
TASK_COMPLETION = "core.task_completion"
VERIFICATION = "core.verification"
AGENT_MODE = "core.agent_mode"
SKILL_CREATE = "core.skill_create"

SUBAGENT_EXPLORE = "core.subagent.explore_agent"
SUBAGENT_PLAN = "core.subagent.plan_agent"
SUBAGENT_BROWSER = "core.subagent.browser_agent"
SUBAGENT_CODE = "core.subagent.code_agent"
SUBAGENT_RESEARCH = "core.subagent.research_agent"
SUBAGENT_VERIFICATION = "core.subagent.verification_agent"
SUBAGENT_GENERAL_PURPOSE = "core.subagent.general_purpose_agent"

_PARENT_MODEL_EXTRAS_KEY = "_parent_model"
_DEFAULT_MAX_ITERATIONS = 15


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _workspace_root(context: Any) -> str:
    """Resolve the member workspace root path (defaults to ``./``)."""
    workspace = getattr(context, "workspace", None)
    return str(getattr(workspace, "root_path", None) or "./")


def _parent_model(context: Any) -> Any:
    """Return the parent model published on the build context (or None)."""
    extras = getattr(context, "extras", None) or {}
    return extras.get(_PARENT_MODEL_EXTRAS_KEY)


# ---------------------------------------------------------------------------
# Rails — inputs + builders
# ---------------------------------------------------------------------------


class ProgressiveToolInput(ConstructionInput):
    """Construction inputs for progressive tool disclosure."""

    planning_tool_names: list[str] | None = param_field(
        default=None,
        description="Alias for progressive_tool_default_visible_tools.",
    )
    executor_tool_names: list[str] | None = param_field(
        default=None,
        description="Alias for progressive_tool_always_visible_tools.",
    )
    default_visible_tools: list[str] | None = param_field(
        default=None,
        description="Tools visible by default under progressive disclosure.",
    )
    always_visible_tools: list[str] | None = param_field(
        default=None,
        description="Tools always kept visible.",
    )
    max_loaded_tools: int | None = param_field(
        default=None,
        description="Maximum number of concurrently loaded tools.",
    )


def _build_progressive_tool_rail(params: dict[str, Any], context: Any) -> ProgressiveToolRail:
    """Build ProgressiveToolRail from extras model + workspace/language."""
    p = dict(params or {})
    config = DeepAgentConfig(
        model=_parent_model(context),
        workspace=getattr(context, "workspace", None),
        sys_operation=None,
        language=getattr(context, "language", None) or "cn",
    )
    config.progressive_tool_enabled = True
    if p.get("planning_tool_names") is not None:
        config.progressive_tool_default_visible_tools = list(p["planning_tool_names"])
    if p.get("executor_tool_names") is not None:
        config.progressive_tool_always_visible_tools = list(p["executor_tool_names"])
    if p.get("default_visible_tools") is not None:
        config.progressive_tool_default_visible_tools = list(p["default_visible_tools"])
    if p.get("always_visible_tools") is not None:
        config.progressive_tool_always_visible_tools = list(p["always_visible_tools"])
    if p.get("max_loaded_tools") is not None:
        config.progressive_tool_max_loaded_tools = int(p["max_loaded_tools"])
    return ProgressiveToolRail(config)


class MemoryInput(ConstructionInput):
    """Construction inputs for MemoryRail (requires embedding_config)."""

    embedding_config: Any = param_field(
        default=None,
        description="EmbeddingConfig instance (or compatible) for memory.",
    )
    is_proactive: bool = param_field(
        default=True,
        description="Whether memory tools are registered proactively.",
    )


def _build_memory_rail(params: dict[str, Any], context: Any) -> Any:
    """Lazy-build MemoryRail (avoids importing optional external memory at ensure time)."""
    del context
    # Imported lazily: ``rails.memory`` package init pulls optional jiuwen_memory.
    from openjiuwen.harness.rails.memory.memory_rail import MemoryRail

    return MemoryRail(**dict(params or {}))


class TaskCompletionInput(ConstructionInput):
    """Construction inputs for TaskCompletionRail."""

    task_instruction: str | None = param_field(default=None, description="Task instruction template.")
    completion_promise: str | None = param_field(default=None, description="Completion promise text.")
    required_confirmations: int = param_field(default=1, description="Required completion confirmations.")
    allow_promise_details: bool = param_field(default=False, description="Allow promise detail tags.")
    max_rounds: int | None = param_field(default=None, description="Max task-loop rounds.")
    timeout_seconds: float | None = param_field(default=None, description="Task-loop timeout seconds.")


class VerificationInput(ConstructionInput):
    """Construction inputs for VerificationRail."""

    allowed_tools: list[str] | None = param_field(
        default=None,
        description="Optional tool allowlist override.",
    )


def _build_verification_rail(params: dict[str, Any], context: Any) -> VerificationRail:
    """Build VerificationRail; coerce list allowlists to frozenset."""
    del context
    p = dict(params or {})
    allowed = p.pop("allowed_tools", None)
    if allowed is not None and not isinstance(allowed, frozenset):
        allowed = frozenset(allowed)
    return VerificationRail(allowed_tools=allowed)


class AgentModeInput(ConstructionInput):
    """Construction inputs for AgentModeRail."""

    allowed_tools: list[str] | None = param_field(default=None, description="Plan-mode allowed tools.")
    allow_switch_mode: bool = param_field(default=True, description="Whether switch_mode tool is registered.")
    plan_mode_system_note: str | None = param_field(default=None, description="Plan-mode system note.")
    enter_plan_instructions: str | None = param_field(default=None, description="Enter-plan instructions.")
    exit_plan_notification: str | None = param_field(default=None, description="Exit-plan notification.")


class SkillCreateInput(ConstructionInput):
    """Construction inputs for SkillCreateRail."""

    skills_dir: str = param_field(default="", description="Directory where new skills are written.")
    language: str = context_field(attr="language", default="cn", description="Prompt language.")
    auto_trigger: bool = param_field(default=True, description="Whether threshold auto-trigger is on.")
    tool_call_threshold: int = param_field(default=10, description="Tool-call count threshold.")
    tool_diversity_threshold: int = param_field(default=5, description="Tool diversity threshold.")


# ---------------------------------------------------------------------------
# Rail declarations
# ---------------------------------------------------------------------------

harness_element(
    kind=ElementKind.RAIL,
    name=PROGRESSIVE_TOOL,
    description="Progressive tool disclosure rail.",
    input_model=ProgressiveToolInput,
    builder=_build_progressive_tool_rail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=MCP,
    description="MCP resource list/read tools rail.",
    builder=McpRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=CONTEXT_ASSEMBLE,
    description="Workspace / context prompt assembly rail.",
    builder=ContextAssembleRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=MEMORY,
    description="Personal memory tools + prompt rail.",
    input_model=MemoryInput,
    builder=_build_memory_rail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=TASK_COMPLETION,
    description="Task-loop completion strategy rail.",
    input_model=TaskCompletionInput,
    builder=TaskCompletionRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=VERIFICATION,
    description="Verification allowlist / reminder rail.",
    input_model=VerificationInput,
    builder=_build_verification_rail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=AGENT_MODE,
    description="Plan-mode enforcement rail.",
    input_model=AgentModeInput,
    builder=AgentModeRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=SKILL_CREATE,
    description="Skill-creation evolution rail.",
    input_model=SkillCreateInput,
    builder=SkillCreateRail,
)


# ---------------------------------------------------------------------------
# Subagents (core.subagent.*)
# ---------------------------------------------------------------------------


class SubAgentInput(ConstructionInput):
    """Construction inputs shared by harness-unique sub-agents."""

    max_iterations: int = param_field(
        default=_DEFAULT_MAX_ITERATIONS,
        description="Maximum task-loop iterations for the sub-agent.",
    )
    language: str = param_field(
        default="en",
        description="Runtime-prompt language for the sub-agent.",
    )
    workspace_root: str = context_field(
        resolver=_workspace_root,
        default="./",
        description="Member workspace root (defaults to ./ when absent).",
    )


def _common_kwargs(inp: SubAgentInput) -> dict[str, Any]:
    """Build the shared ``build_*_agent_config`` kwargs from resolved inputs."""
    return {
        "workspace": inp.workspace_root,
        "language": inp.language,
        "max_iterations": inp.max_iterations,
    }


class BrowserSubAgentInput(SubAgentInput):
    """Browser sub-agent inputs: per-instance browser identity."""

    browser_key: str = param_field(default="", description="Browser identity key.")
    browser_port: int = param_field(default=0, description="Managed Chrome debug port; 0 auto-allocates.")
    browser_profile: str = param_field(default="", description="Managed browser profile name.")
    browser_driver: str = param_field(default="", description="Driver mode: managed / remote / extension.")
    browser_cdp_url: str = param_field(default="", description="Remote-mode CDP endpoint URL.")


def _browser_instance_dict(inp: BrowserSubAgentInput) -> dict[str, Any] | None:
    """Build a serializable browser-instance dict, or None for legacy behavior."""
    if not any((inp.browser_key, inp.browser_port, inp.browser_profile, inp.browser_driver, inp.browser_cdp_url)):
        return None
    data: dict[str, Any] = {}
    if inp.browser_key:
        data["key"] = inp.browser_key
    if inp.browser_port:
        data["managed_port"] = inp.browser_port
    if inp.browser_profile:
        data["profile_name"] = inp.browser_profile
    if inp.browser_cdp_url:
        data["cdp_url"] = inp.browser_cdp_url
    data["driver_mode"] = inp.browser_driver or ("remote" if inp.browser_cdp_url else "managed")
    return data


def build_explore_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build explore sub-agent config; parent model from extras when present."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    spec = build_explore_agent_config(model=_parent_model(context), **_common_kwargs(inp))
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


def build_plan_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build plan sub-agent config; parent model from extras when present."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    spec = build_plan_agent_config(model=_parent_model(context), **_common_kwargs(inp))
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


def build_browser_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build browser sub-agent config; skipped when parent model is absent."""
    inp = BrowserSubAgentInput.resolve(factory_kwargs, context)
    model = _parent_model(context)
    if model is None:
        logger.warning(
            "[%s] skipped: no parent model on build context",
            SUBAGENT_BROWSER,
        )
        return None
    spec = build_browser_agent_config(model, **_common_kwargs(inp))
    instance_dict = _browser_instance_dict(inp)
    if instance_dict is None:
        if not str(os.getenv("BROWSER_DRIVER") or "").strip():
            os.environ["BROWSER_DRIVER"] = "managed"
        spec.factory_kwargs = {"auto_create_workspace": False}
    else:
        spec.factory_kwargs = {"auto_create_workspace": False, "browser_instance": instance_dict}
    return spec


def build_code_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build code sub-agent config; requires parent model."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    model = _parent_model(context)
    if model is None:
        logger.warning(
            "[%s] skipped: no parent model on build context",
            SUBAGENT_CODE,
        )
        return None
    kwargs = _common_kwargs(inp)
    kwargs["sys_operation"] = factory_kwargs.get("sys_operation")
    return build_code_agent_config(model, **kwargs)


def build_research_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build research sub-agent config; requires parent model."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    model = _parent_model(context)
    if model is None:
        logger.warning(
            "[%s] skipped: no parent model on build context",
            SUBAGENT_RESEARCH,
        )
        return None
    return build_research_agent_config(model, **_common_kwargs(inp))


def build_verification_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build verification sub-agent config; parent model from extras when present."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    return build_verification_agent_config(model=_parent_model(context), **_common_kwargs(inp))


def build_general_purpose_subagent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build general-purpose sub-agent; sparse extras use empty tools/mcps/skills."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    language = inp.language or getattr(context, "language", None) or "cn"
    return SubAgentConfig(
        agent_card=AgentCard(
            name="general-purpose",
            description=GENERAL_PURPOSE_AGENT_DESC.get(
                language,
                GENERAL_PURPOSE_AGENT_DESC["cn"],
            ),
        ),
        system_prompt="",
        tools=[],
        mcps=[],
        model=_parent_model(context),
        skills=[],
        rails=[SysOperationRail()],
        workspace=getattr(context, "workspace", None),
        sys_operation=None,
        language=language,
        restrict_to_work_dir=False,
        max_iterations=inp.max_iterations,
    )


harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_EXPLORE,
    description="Read-only exploration sub-agent (core.subagent.*; no ObservabilityRail).",
    input_model=SubAgentInput,
    builder=build_explore_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_PLAN,
    description="Planning sub-agent (core.subagent.*; no ObservabilityRail).",
    input_model=SubAgentInput,
    builder=build_plan_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_BROWSER,
    description="Browser automation sub-agent (core.subagent.*; no ObservabilityRail).",
    input_model=BrowserSubAgentInput,
    builder=build_browser_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_CODE,
    description="Code sub-agent (core.subagent.*; sys_operation=None by default).",
    input_model=SubAgentInput,
    builder=build_code_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_RESEARCH,
    description="Research sub-agent (core.subagent.*).",
    input_model=SubAgentInput,
    builder=build_research_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_VERIFICATION,
    description="Verification sub-agent (core.subagent.*).",
    input_model=SubAgentInput,
    builder=build_verification_subagent,
)
harness_element(
    kind=ElementKind.SUBAGENT,
    name=SUBAGENT_GENERAL_PURPOSE,
    description="General-purpose sub-agent with sparse BuildContext defaults.",
    input_model=SubAgentInput,
    builder=build_general_purpose_subagent,
)


__all__ = [
    "PROGRESSIVE_TOOL",
    "MCP",
    "CONTEXT_ASSEMBLE",
    "MEMORY",
    "TASK_COMPLETION",
    "VERIFICATION",
    "AGENT_MODE",
    "SKILL_CREATE",
    "SUBAGENT_EXPLORE",
    "SUBAGENT_PLAN",
    "SUBAGENT_BROWSER",
    "SUBAGENT_CODE",
    "SUBAGENT_RESEARCH",
    "SUBAGENT_VERIFICATION",
    "SUBAGENT_GENERAL_PURPOSE",
]
