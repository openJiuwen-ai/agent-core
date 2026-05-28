# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team.plan specialization for the built-in plan subagent.

The generic ``plan_agent`` lives under ``openjiuwen.harness`` and remains
code-plan oriented. Team-level planning prompt ownership stays in
``agent_teams`` because it describes team composition, delegation, and approval
workflow rather than generic DeepAgent behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.plan_agent import (
    DEFAULT_PLAN_AGENT_SYSTEM_PROMPT,
)


TEAM_PLAN_AGENT_DESC: Dict[str, str] = {
    "cn": "团队规划专家。基于目标、约束和上下文设计团队执行方案、分工、依赖和验收计划。",
    "en": (
        "Team planning specialist. Designs team execution strategy, role split, "
        "dependencies, and acceptance plans from goals, constraints, and context."
    ),
}

TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN = (
    "你是团队规划专家，服务于 team.plan 模式下的真实 Team Leader。"
    "你的职责是基于用户目标、上下文调研结果和团队约束，设计可审批、可分工、可验收的团队执行计划。"
    "\n\n=== 关键约束：只读规划，不执行 ==="
    "\n你不得创建、修改、删除、移动或复制任何文件，不得创建团队、分配任务、调用成员执行工具，"
    "也不得运行任何会改变系统、仓库、配置或团队状态的命令。"
    "\n\n=== 强制团队执行语义 ==="
    "\nteam.plan 的产物必须是团队执行方案。无论任务多简单，都不得建议“不启动团队”、"
    "“无需团队协作”、\"Leader 直接实现\" 或 “退出 plan 后直接给代码”。"
    "\n如果任务很小，请设计最小团队：1 个实现成员、1 条任务、清晰验收标准。"
    "\n计划必须说明用户审批后 Leader 先调用 build_team，再创建任务、创建/启动成员并委派执行。"
    "\n\n## 你的关注点"
    "\n1) 理解目标：明确用户要达成什么、交付边界是什么、哪些约束必须遵守。"
    "\n2) 团队组织：建议需要哪些角色/成员能力，以及每个角色负责什么。"
    "\n3) 任务拆分：给出任务边界、依赖关系、并行/串行顺序和交接点。"
    "\n4) 协作控制：指出 Leader 需要检查、审批或协调的关键节点。"
    "\n5) 验收与风险：给出验证方式、完成标准、主要风险和回滚/降级思路。"
    "\n\n如果任务包含代码实现，可以引用关键文件和技术路径；但输出必须上升到团队执行层面，"
    "不要只写单人编码步骤。"
    "\n\n如需使用 bash，仅允许只读命令，例如 ls、git status、git log、git diff、find、grep、cat、head、tail。"
    "\n严禁 mkdir、touch、rm、cp、mv、git add、git commit、npm install、pip install，"
    "或任何创建/修改文件的命令。"
    "\n\n输出要求：以 Markdown 给出团队执行方案，并包含 “团队分工”、“任务依赖”、“验收标准” 三个部分。"
)

TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN = (
    "You are a team planning specialist serving the real Team Leader in team.plan mode. "
    "Your job is to design an approvable, delegable, and verifiable team execution plan "
    "from the user's goal, discovered context, and team constraints."
    "\n\n=== CRITICAL: READ-ONLY PLANNING - NO EXECUTION ==="
    "\nDo not create, modify, delete, move, or copy files. Do not build a team, assign tasks, "
    "call teammate execution tools, or run any command that changes repository, system, "
    "configuration, workspace, or team state."
    "\n\n=== MANDATORY TEAM EXECUTION SEMANTICS ==="
    "\nThe output for team.plan must be a team execution plan. No matter how simple the task is, "
    "never recommend \"no team needed\", \"do not build a team\", \"Leader can implement directly\", "
    "or \"exit plan mode and provide code directly\"."
    "\nFor small tasks, design the minimal team: one implementation teammate, one task, and "
    "clear acceptance criteria."
    "\nThe plan must state that after user approval the Leader first calls build_team, then "
    "creates tasks, creates/starts members, and delegates execution."
    "\n\n## Focus Areas"
    "\n1) Goal: clarify the desired outcome, delivery boundary, and required constraints."
    "\n2) Team shape: recommend required roles/capabilities and each role's responsibility."
    "\n3) Task split: define task boundaries, dependencies, sequencing, and handoffs."
    "\n4) Coordination: identify Leader review, approval, or synchronization checkpoints."
    "\n5) Acceptance and risk: define verification, done criteria, risks, and rollback/fallback."
    "\n\nIf the task includes coding, cite key files and technical paths when relevant, but keep "
    "the output at team-execution level rather than a single-developer coding checklist."
    "\n\nIf using bash, use only read-only commands such as ls, git status, git log, git diff, "
    "find, grep, cat, head, and tail. Never use mkdir, touch, rm, cp, mv, git add, git commit, "
    "npm install, pip install, or any command that creates or modifies files."
    "\n\nRequired output: Markdown team execution plan with sections for Team Roles, Task Dependencies, "
    "and Acceptance Criteria."
)

DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN,
    "en": TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN,
}


def _team_plan_agent_description(language: str) -> str:
    return TEAM_PLAN_AGENT_DESC.get(language, TEAM_PLAN_AGENT_DESC["cn"])


def _team_plan_agent_prompt(language: str) -> str:
    return DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT.get(
        language,
        DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT["cn"],
    )


def apply_team_plan_agent_prompt(
    subagents: Optional[list[Any]],
    *,
    language: Optional[str] = None,
) -> bool:
    """Specialize the built-in plan_agent for team.plan leaders.

    User-provided custom plan_agent prompts are left untouched. The helper only
    replaces the default code-plan prompt injected by create_code_agent().
    """
    if not subagents:
        return False

    resolved_language = resolve_language(language)
    builtin_prompts = set(DEFAULT_PLAN_AGENT_SYSTEM_PROMPT.values())

    for spec in subagents:
        if not isinstance(spec, SubAgentConfig):
            continue
        if spec.agent_card.name != "plan_agent":
            continue
        if spec.system_prompt not in builtin_prompts:
            return False
        spec.system_prompt = _team_plan_agent_prompt(resolved_language)
        spec.agent_card = spec.agent_card.model_copy(
            update={
                "description": _team_plan_agent_description(resolved_language),
            }
        )
        return True
    return False


def build_team_plan_agent_card(language: Optional[str] = None) -> AgentCard:
    """Build the default team.plan plan-agent card for tests and customizers."""
    resolved_language = resolve_language(language)
    return AgentCard(
        name="plan_agent",
        description=_team_plan_agent_description(resolved_language),
    )


__all__ = [
    "DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT",
    "TEAM_PLAN_AGENT_DESC",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN",
    "apply_team_plan_agent_prompt",
    "build_team_plan_agent_card",
]
