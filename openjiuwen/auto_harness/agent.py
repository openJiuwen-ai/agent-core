# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness Agent 工厂函数。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional

from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.foundation.tool import Tool, ToolCard
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
    from openjiuwen.core.single_agent.rail.base import (
        AgentRail,
    )
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.auto_harness.schema import (
        AutoHarnessConfig,
        CommitFacts,
    )

_SKILLS_DIR = str(Path(__file__).parent / "skills")
logger = logging.getLogger(__name__)


def _build_trusted_local_sys_operation(
    agent_name: str,
) -> SysOperation:
    """Build a permissive local SysOperation for trusted auto-harness runs.

    Auto-harness operates on user-owned local worktrees and needs to run the
    repository's native verification commands. Disable the shell allowlist and
    worktree path restriction here, while keeping the lower-level dangerous
    command checks in ``LocalShellOperation`` active.
    """
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
    commit_facts_provider: Optional[
        Callable[[], "CommitFacts"]
    ] = None,
    git: Optional[object] = None,
    extra_rails: Optional[List["AgentRail"]] = None,
    extra_tools: Optional[
        List[Tool | ToolCard]
    ] = None,
) -> "DeepAgent":
    """创建 Auto Harness Agent 实例（实现 agent）。

    Args:
        config: Auto Harness 配置。
        workspace_override: 可选，覆盖 agent 的 workspace 根目录。
        extra_rails: 额外的 rail 实例。
        extra_tools: 额外的 tool 实例。

    Returns:
        配置好的 DeepAgent 实例。
    """
    rails = _build_rails(
        config,
        edit_safety_rail=edit_safety_rail,
    )
    rails.append(_build_skill_rail(
        ["implement", "verify", "communicate"],
    ))
    if extra_rails:
        rails.extend(extra_rails)

    tools: List[Tool | ToolCard] = _build_tools(
        config,
        git=git,
        commit_facts_provider=commit_facts_provider,
    )
    if extra_tools:
        tools.extend(extra_tools)

    sections = build_auto_harness_sections(
        ci_gate_rules=_load_ci_gate_rules(config),
    )
    system_prompt = "\n\n".join(
        s.render(config.language) for s in sections
    )

    agent = create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness",
            description=(
                "自主优化 harness 框架的编码 agent"
            ),
        ),
        system_prompt=system_prompt,
        tools=tools or None,
        subagents=_build_subagents(config) or None,
        rails=rails or None,
        workspace=workspace_override or config.workspace,
        language=config.language,
        enable_task_planning=True,
        enable_async_subagent=True,
        max_iterations=30,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness"
        ),
    )
    return agent


def _build_rails(
    config: "AutoHarnessConfig",
    *,
    edit_safety_rail: Optional["AgentRail"] = None,
) -> list["AgentRail"]:
    """构建 auto-harness 专用 rails。"""
    from openjiuwen.auto_harness.rails.context_rail import (
        AutoHarnessContextRail,
    )
    from openjiuwen.harness.cli.rails.tool_tracker import (
        ToolTrackingRail,
    )
    from openjiuwen.harness.rails.filesystem_rail import (
        FileSystemRail,
    )
    from openjiuwen.harness.rails.lsp_rail import (
        LspRail,
    )
    from openjiuwen.auto_harness.rails.security_rail import (
        SecurityRail,
    )
    from openjiuwen.auto_harness.rails.experience_rail import (
        AutoHarnessExperienceRail,
    )
    from openjiuwen.auto_harness.rails.edit_safety_rail import (
        EditSafetyRail,
    )

    rails: list["AgentRail"] = [
        ToolTrackingRail(),
        FileSystemRail(),
        AutoHarnessContextRail(preset=True),
        LspRail(),
        AutoHarnessExperienceRail(
            config.resolved_experience_dir,
            language=config.language,
        ),
        SecurityRail(
            immutable_files=config.immutable_files,
            high_impact_prefixes=(
                config.high_impact_prefixes
            ),
        ),
        edit_safety_rail or EditSafetyRail(),
    ]
    return rails


def _build_tools(
    config: "AutoHarnessConfig",
    *,
    git: Optional[object] = None,
    commit_facts_provider: Optional[
        Callable[[], "CommitFacts"]
    ] = None,
) -> List[Tool | ToolCard]:
    """构建 auto-harness 专用 tools。"""
    tools: List[Tool | ToolCard] = []
    if git is not None and commit_facts_provider is not None:
        tools.extend(_build_commit_tool(
            git,
            commit_facts_provider,
            config.language,
        ))
    return tools


def _build_subagents(
    config: "AutoHarnessConfig",
) -> list[object]:
    """构建 auto-harness 可调用的子代理。"""
    from openjiuwen.core.single_agent.schema.agent_card import (
        AgentCard as _AgentCard,
    )
    from openjiuwen.harness.prompts.sections.tools.task_tool import (
        EXPLORE_AGENT_DESC,
        EXPLORE_AGENT_SYSTEM_PROMPT_CN,
        EXPLORE_AGENT_SYSTEM_PROMPT_EN,
    )
    from openjiuwen.harness.rails.filesystem_rail import (
        FileSystemRail,
    )
    from openjiuwen.harness.schema.config import (
        SubAgentConfig,
    )
    from openjiuwen.harness.subagents.browser_agent import (
        build_browser_agent_config,
    )
    resolved_language = resolve_language(
        config.language,
    )
    subagents: list[object] = [
        SubAgentConfig(
            agent_card=_AgentCard(
                name="explore_agent",
                description=EXPLORE_AGENT_DESC.get(
                    resolved_language,
                    EXPLORE_AGENT_DESC["cn"],
                ),
            ),
            system_prompt=(
                EXPLORE_AGENT_SYSTEM_PROMPT_EN
                if resolved_language == "en"
                else EXPLORE_AGENT_SYSTEM_PROMPT_CN
            ),
            model=config.model,
            workspace=config.workspace,
            language=resolved_language,
            rails=[FileSystemRail()],
            max_iterations=20,
        ),
    ]

    try:
        subagents.append(
            build_browser_agent_config(
                config.model,
                workspace=config.workspace,
                language=resolved_language,
                max_iterations=20,
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
    """加载 CI 门控规则。"""
    if config.ci_gate_config:
        path = Path(config.ci_gate_config)
    else:
        path = (
            Path(__file__).parent
            / "resources"
            / "ci_gate.yaml"
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# ----------------------------------------------------------
# 各阶段 Agent 工厂函数
# ----------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 prompt 模板文件内容。"""
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def _build_readonly_rails(
    config: "AutoHarnessConfig",
) -> list["AgentRail"]:
    """构建只读 rails（无 EditCheckRail）。"""
    from openjiuwen.auto_harness.rails.context_rail import (
        AutoHarnessContextRail,
    )
    from openjiuwen.harness.cli.rails.tool_tracker import (
        ToolTrackingRail,
    )
    from openjiuwen.harness.rails.filesystem_rail import (
        FileSystemRail,
    )
    from openjiuwen.harness.rails.lsp_rail import (
        LspRail,
    )
    from openjiuwen.auto_harness.rails.experience_rail import (
        AutoHarnessExperienceRail,
    )

    return [
        ToolTrackingRail(),
        FileSystemRail(),
        AutoHarnessContextRail(preset=True),
        LspRail(),
        AutoHarnessExperienceRail(
            config.resolved_experience_dir,
            language=config.language,
        ),
    ]


def _build_skill_rail(
    skill_names: List[str],
) -> "AgentRail":
    """构建指向包内 skills/ 目录的 SkillUseRail。

    Args:
        skill_names: 要启用的 skill 名称列表。

    Returns:
        配置好的 SkillUseRail 实例。
    """
    from openjiuwen.harness.rails.skill_use_rail import (
        SkillUseRail,
    )

    skills_dir = [
        str(Path(_SKILLS_DIR) / s)
        for s in skill_names
    ]
    return SkillUseRail(
        skills_dir=skills_dir,
        skill_mode="all",
    )


def _build_commit_tool(
    git: object,
    commit_facts_provider: Callable[[], "CommitFacts"],
    language: str,
) -> List[Tool | ToolCard]:
    """构建 CommitTool 列表。"""
    from openjiuwen.auto_harness.tools.commit_tool import (
        CommitTool,
    )

    return [
        CommitTool(
            git=git,  # type: ignore[arg-type]
            facts_provider=commit_facts_provider,
            language=language,
        ),
    ]


def _build_research_tools(
    config: "AutoHarnessConfig",
) -> List[Tool | ToolCard]:
    """Build readonly research tools for assess/plan phases."""
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
    """创建 Assessment 阶段的 DeepAgent。"""
    prompt = _load_prompt("assess.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(["assess"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-assess",
            description="评估代码库当前状态",
        ),
        system_prompt=prompt,
        tools=_build_research_tools(config) or None,
        subagents=_build_subagents(config) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=30,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-assess"
        ),
    )


def create_plan_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """创建 Planning 阶段的 DeepAgent。"""
    prompt = _load_prompt("plan.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(["assess"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-plan",
            description="制定优化任务列表",
        ),
        system_prompt=prompt,
        tools=_build_research_tools(config) or None,
        subagents=_build_subagents(config) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=15,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-plan"
        ),
    )


def create_eval_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """创建 Evaluator 阶段的 DeepAgent。"""
    prompt = _load_prompt("evaluate.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(["verify"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-eval",
            description="评审代码变更质量",
        ),
        system_prompt=prompt,
        subagents=_build_subagents(config) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        enable_async_subagent=True,
        max_iterations=10,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-eval"
        ),
    )


def create_learnings_agent(
    config: "AutoHarnessConfig",
) -> "DeepAgent":
    """创建 Learnings 反思阶段的 DeepAgent。"""
    prompt = _load_prompt("learnings.md")
    rails = _build_readonly_rails(config)
    rails.append(_build_skill_rail(["communicate"]))
    return create_deep_agent(
        model=config.model,
        card=AgentCard(
            name="auto-harness-learnings",
            description=(
                "反思 session 结果并提取经验"
            ),
        ),
        system_prompt=prompt,
        tools=_build_memory_tool(config) or None,
        rails=rails or None,
        workspace=config.workspace,
        language=config.language,
        max_iterations=5,
        sys_operation=_build_trusted_local_sys_operation(
            "auto-harness-learnings"
        ),
    )
