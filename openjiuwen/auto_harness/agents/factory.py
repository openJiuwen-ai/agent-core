# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness agent factories."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.auto_harness.prompts.sections import (
    build_auto_harness_sections,
)

if TYPE_CHECKING:
    pass

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_SKILLS_DIR = str(_PACKAGE_DIR / "skills")
_PROMPTS_DIR = _PACKAGE_DIR / "prompts"
logger = logging.getLogger(__name__)


def _build_trusted_local_sys_operation(
    agent_name: str,
) -> SysOperation:
    """Build a permissive local SysOperation for trusted auto-harness runs."""
    return SysOperation(
        SysOperationCard(
            id=f"{agent_name}_trusted_local",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                shell_allowlist=None,
                restrict_to_sandbox=False,
            ),
        )
    )


def create_auto_harness_agent(
    config: "AutoHarnessConfig",
    *,
    workspace_override: Optional[str] = None,
    edit_safety_rail: Optional["AgentRail"] = None,
    skill_names: Optional[List[str]] = None,
    enable_task_loop: bool = True,
    enable_task_planning: bool = True,
    enable_progress_repeat: bool = True,
    extra_rails: Optional[List["AgentRail"]] = None,
    extra_tools: Optional[List[Tool | ToolCard]] = None,
) -> "DeepAgent":
    """Create the main task implementation agent."""
    rails = _build_rails(
        config,
        edit_safety_rail=edit_safety_rail,
    )
    rails.append(
        _build_skill_rail(
            config,
            skill_names
            or [
                "implement",
                "verify",
                "communicate",
            ],
        )
    )
    if enable_task_planning:
        from openjiuwen.harness.rails import (
            TaskPlanningRail,
        )

        rails.append(
            TaskPlanningRail(
                enable_progress_repeat=enable_progress_repeat
            )
        )
    if extra_rails:
        rails.extend(extra_rails)

    tools: List[Tool | ToolCard] = []
    if extra_tools:
        tools.extend(extra_tools)

    sections = build_auto_harness_sections(
        ci_gate_rules=_load_ci_gate_rules(config),
    )
    system_prompt = "\n\n".join(
        s.render(config.language) for s in sections
    )
    workspace = workspace_override or config.workspace
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness",
            description="自主优化 harness 框架的编码 agent",
        ),
        system_prompt=system_prompt,
        tools=tools or None,
        subagents=_build_subagents(
            config,
            workspace=workspace,
        ) or None,
        rails=rails or None,
        workspace=workspace,
        language=config.language,
        enable_task_loop=enable_task_loop,
        enable_task_planning=enable_task_planning,
        enable_async_subagent=True,
        max_iterations=config.resolve_agent_iterations(
            "implement", 30
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness"
        ),
    )


def create_commit_agent(
    config: "AutoHarnessConfig",
    *,
    workspace_override: Optional[str] = None,
) -> "DeepAgent":
    """Create the dedicated commit-stage agent."""
    return create_auto_harness_agent(
        config,
        workspace_override=workspace_override,
        skill_names=["commit", "communicate"],
        enable_task_loop=False,
        enable_task_planning=False,
        enable_progress_repeat=False,
    )


def _build_rails(
    config: "AutoHarnessConfig",
    *,
    edit_safety_rail: Optional["AgentRail"] = None,
) -> list["AgentRail"]:
    """Build the standard rails for writable task stages."""
    from openjiuwen.auto_harness.rails.context_rail import (
        AutoHarnessContextRail,
    )
    from openjiuwen.auto_harness.rails.edit_safety_rail import (
        EditSafetyRail,
    )
    from openjiuwen.auto_harness.rails.experience_rail import (
        AutoHarnessExperienceRail,
    )
    from openjiuwen.auto_harness.rails.security_rail import (
        SecurityRail,
    )
    from openjiuwen.harness.cli.rails.tool_tracker import (
        ToolTrackingRail,
    )
    from openjiuwen.harness.rails.sys_operation_rail import (
        SysOperationRail,
    )
    from openjiuwen.harness.rails.lsp_rail import (
        LspRail,
    )

    return [
        ToolTrackingRail(),
        SysOperationRail(),
        AutoHarnessContextRail(preset=True),
        LspRail(),
        AutoHarnessExperienceRail(
            config.resolved_experience_dir,
            language=config.language,
        ),
        SecurityRail(
            immutable_files=config.immutable_files,
            high_impact_prefixes=config.high_impact_prefixes,
        ),
        edit_safety_rail or EditSafetyRail(),
    ]


def _build_subagents(
    config: "AutoHarnessConfig",
    *,
    workspace: Optional[str] = None,
) -> list[object]:
    """Build the reusable subagents exposed to auto-harness."""
    from openjiuwen.harness.subagents.browser_agent import (
        build_browser_agent_config,
    )
    from openjiuwen.harness.subagents.explore_agent import (
        DEFAULT_EXPLORE_AGENT_DESCRIPTION,
        build_explore_agent_config,
    )

    resolved_language = resolve_language(config.language)
    subagent_workspace = workspace or config.workspace
    subagents: list[object] = [
        build_explore_agent_config(
            card=AgentCard(
                name="explore_agent",
                description=DEFAULT_EXPLORE_AGENT_DESCRIPTION.get(
                    resolved_language,
                    DEFAULT_EXPLORE_AGENT_DESCRIPTION["cn"],
                ),
            ),
            model=config.model,
            workspace=subagent_workspace,
            language=resolved_language,
            max_iterations=config.resolve_agent_iterations(
                "explore_subagent", 20
            ),
        ),
    ]
    try:
        subagents.append(
            build_browser_agent_config(
                config.model,
                workspace=subagent_workspace,
                language=resolved_language,
                max_iterations=config.resolve_agent_iterations(
                    "browser_subagent", 20
                ),
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "Browser subagent not available for auto-harness",
            exc_info=True,
        )
    return subagents


def _load_ci_gate_rules(
    config: "AutoHarnessConfig",
) -> str:
    """Load CI gate rules text for prompt construction."""
    if config.ci_gate_config:
        path = Path(config.ci_gate_config)
    else:
        path = _PACKAGE_DIR / "resources" / "ci_gate.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the package prompt directory."""
    return (_PROMPTS_DIR / filename).read_text(
        encoding="utf-8"
    )


def _render_prompt(
    template: str,
    **values: str,
) -> str:
    """Render simple placeholders without touching JSON braces."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(
            f"{{{key}}}",
            value,
        )
    return rendered


def _build_readonly_rails(
    config: "AutoHarnessConfig",
) -> list["AgentRail"]:
    """Build readonly rails for assess/plan/eval style stages."""
    from openjiuwen.auto_harness.rails.context_rail import (
        AutoHarnessContextRail,
    )
    from openjiuwen.auto_harness.rails.experience_rail import (
        AutoHarnessExperienceRail,
    )
    from openjiuwen.harness.cli.rails.tool_tracker import (
        ToolTrackingRail,
    )
    from openjiuwen.harness.rails.sys_operation_rail import (
        SysOperationRail,
    )
    from openjiuwen.harness.rails.lsp_rail import (
        LspRail,
    )

    return [
        ToolTrackingRail(),
        SysOperationRail(),
        AutoHarnessContextRail(preset=True),
        LspRail(),
        AutoHarnessExperienceRail(
            config.resolved_experience_dir,
            language=config.language,
        ),
    ]


def _build_skill_rail(
    config: "AutoHarnessConfig",
    skill_names: List[str],
) -> "AgentRail":
    """Build a skill rail from package-local and configured skill roots."""
    from openjiuwen.harness.rails.skills.skill_use_rail import (
        SkillUseRail,
    )

    roots = [_SKILLS_DIR, *config.skills_dirs]
    skills_dir: list[str] = []
    enabled_skills: list[str] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        if not any(
            (root_path / skill_name).is_dir()
            for skill_name in skill_names
        ):
            continue
        skills_dir.append(str(root_path))
    for skill_name in skill_names:
        for root in skills_dir:
            if (Path(root) / skill_name).is_dir():
                enabled_skills.append(skill_name)
                break
    return SkillUseRail(
        skills_dir=skills_dir,
        skill_mode="all",
        enabled_skills=enabled_skills,
    )


def _build_research_tools(
    config: "AutoHarnessConfig",
) -> List[Tool | ToolCard]:
    """Build readonly research tools for assess and plan stages."""
    del config
    from openjiuwen.harness.tools import (
        WebFetchWebpageTool,
        WebFreeSearchTool,
    )

    return [
        WebFreeSearchTool(language="en"),
        WebFetchWebpageTool(language="en"),
    ]


def create_assess_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """Create the assessment-stage agent."""
    prompt = _load_prompt("assess.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(config, ["assess"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-assess",
            description="评估代码库当前状态",
        ),
        system_prompt=prompt,
        tools=_build_research_tools(config) or None,
        subagents=_build_subagents(
            config,
            workspace=config.workspace,
        ) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=config.resolve_agent_iterations(
            "assess", 30
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-assess"
        ),
    )


def create_plan_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """Create the planning-stage agent."""
    prompt = _load_prompt("plan.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(config, ["plan"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-plan",
            description="制定优化任务列表",
        ),
        system_prompt=prompt,
        tools=_build_research_tools(config) or None,
        subagents=_build_subagents(
            config,
            workspace=config.workspace,
        ) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=config.resolve_agent_iterations(
            "plan", 15
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-plan"
        ),
    )


def create_eval_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """Create the evaluator agent used by the verify fix loop."""
    prompt = _load_prompt("evaluate.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(config, ["verify"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-eval",
            description="评审代码变更质量",
        ),
        system_prompt=prompt,
        subagents=_build_subagents(
            config,
            workspace=config.workspace,
        ) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=config.resolve_agent_iterations(
            "eval", 10
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-eval"
        ),
    )


def create_select_pipeline_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """Create the pipeline-selection agent."""
    prompt = _load_prompt("select_pipeline.md")
    rails = _build_readonly_rails(config)
    rails.append(
        _build_skill_rail(config, ["select_pipeline"])
    )
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-select-pipeline",
            description="选择最合适的优化流水线",
        ),
        system_prompt=prompt,
        tools=_build_research_tools(config) or None,
        subagents=_build_subagents(
            config,
            workspace=config.workspace,
        ) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=config.resolve_agent_iterations(
            "select_pipeline", 10
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-select-pipeline"
        ),
    )


def create_pr_draft_agent(
    config: "AutoHarnessConfig",
    *,
    workspace_override: Optional[str] = None,
) -> "DeepAgent":
    """Create the communicate-only agent used for PR drafts."""
    prompt = _load_prompt("pr_draft.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(config, ["communicate"]))
    workspace = workspace_override or config.workspace
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-pr-draft",
            description="根据任务事实生成 GitCode PR draft",
        ),
        system_prompt=prompt,
        rails=rails or None,
        workspace=workspace,
        language=config.language,
        max_iterations=config.resolve_agent_iterations(
            "pr_draft", 5
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-pr-draft"
        ),
    )


def create_learnings_agent(
    config: "AutoHarnessConfig",
    *,
    session_results: str = "",
    existing_memories: str = "",
) -> "DeepAgent":
    """Create the session learnings agent."""
    prompt = _render_prompt(
        _load_prompt("learnings.md"),
        session_results=session_results,
        existing_memories=existing_memories,
    )
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(config, ["communicate"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-learnings",
            description="反思 session 结果并提取经验",
        ),
        system_prompt=prompt,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        max_iterations=config.resolve_agent_iterations(
            "learnings", 5
        ),
        auto_create_workspace=False,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-learnings"
        ),
    )
