# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution protocol prompt section."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

EVOLUTION_PROTOCOL_PROMPT_CN = """## 演进协议

### 演进时机判断
- `/evolve` 或模糊自检表示检查演进机会，不表示必须写入经验。
- 先判断是否存在可复用 Skill 经验线索：用户纠正、目标 Skill 能力缺口、知识过期、
  更稳定的 recurring workflow，或执行失败暴露出 Skill 缺少 precheck/fallback/verification/排错指引。
- 演进审查聚焦可复用的 Skill 指令改进；具体执行失败仅作为是否存在可复用
  Skill 指令缺口的证据。
- user_intent 仅表示审查方向，不是证据。若没有可用任务记录且没有 user_intent，
  应提交 no_evolution。
- 一次性任务事实、个人偏好、临时环境/权限/网络问题、已有经验覆盖或低置信度候选，
  应不演进。

### 工具流程
- 所有演进工具都必须使用顶层 subject 参数对象，例如 {"kind":"skill","name":"..."}。
- 用户同意后，调用 prepare_skill_evolution(user_confirmed=true, user_intent=...)。
- 随后使用 evolve_review_task(evolution_review_ref=...) 和返回的 evolution_review_ref。
- 从 evolve_review_task.data.output.proposal_selection_for_submission 读取 selection，再调用
  evolve_skill_experiences；不要复制或改写 proposal 内容。
- 不要直接编辑 SKILL.md、evolutions.json 或 evolution/*.md；提交演进变更时可能需要用户审批。
"""

EVOLUTION_FUZZY_REVIEW_PROMPT_CN = """
### 模糊演进自检
- 检查近期上下文是否有可复用 Skill 经验线索；这不是处理本次错误的请求。
- 当用户纠正上一轮 agent 的执行步骤、顺序、确认条件或交付流程时，
  先判定为高优先级可复用流程线索。
  该反馈可作为演进候选，但不表示必然演进。
- 只有问题可归入已使用或可从近期任务上下文推断的相关 Skill，且可归因到 Skill
  指令、改法可复用且非重复时，才算可演进机会。
- 当用户用“你应该/应该先/先...再.../确认后再.../不要直接...”等规则化表达，
  且内容是可复用工作流或可复用执行规则，要先确认该建议是否要沉淀为可复用
  Skill 经验。
- 弱信号、没有可演进机会或只是具体执行失败时，不要因为出现失败就询问用户演进。
- 一次性偏好、不可复用、已有经验覆盖或无法归入相关 Skill 场景时，不要询问演进。
- 如果你需要回复本次自检，只能说本次技能演进自检未发现可演进的 Skill，
  不要使用“候选”等内部术语。
- 有可演进机会时，用一句话确认这条反馈可以沉淀为以后处理同类任务时的流程经验，
  是否需要我记录到相关 Skill？
- 用户确认后才进入工具流程；未确认时不要提交演进变更。
"""

EVOLUTION_PROTOCOL_PROMPT_EN = """## Evolution Protocol

### When To Recommend Evolution
- `/evolve` or fuzzy review means check for evolution opportunities; it does not mean an experience must be written.
- First decide whether there is a reusable Skill lesson: user correction, missing capability in the target
  Skill scope, outdated knowledge, a better recurring workflow, or an execution failure that reveals missing Skill
  precheck/fallback/verification/troubleshooting guidance.
- This review focuses on reusable Skill instruction gaps; concrete execution failures are evidence only when they
  suggest missing reusable Skill precheck, fallback, verification, parameter, or troubleshooting guidance.
- user_intent is review direction, not evidence. If no task record is available and user_intent
  is empty, submit no_evolution.
- Do not evolve for one-off task facts, personal preferences, temporary environment/permission/network issues,
  duplicate coverage by existing experiences, or low-confidence signals.

### Tool Flow
- Use top-level subject objects for all evolution tools, for example {"kind":"skill","name":"..."}.
- After consent, call prepare_skill_evolution(user_confirmed=true, user_intent=...).
- Then use evolve_review_task(evolution_review_ref=...) with the returned evolution_review_ref.
- Read selection from evolve_review_task.data.output.proposal_selection_for_submission,
  then call evolve_skill_experiences; do not copy or rewrite proposal content.
- Do not edit SKILL.md, evolutions.json, or evolution/*.md directly; submitting evolution changes may
  require user approval.
"""

EVOLUTION_FUZZY_REVIEW_PROMPT_EN = """
### Fuzzy Review Check
- Check recent context for reusable Skill lessons; this is not a request to handle the current error.
- When the user corrects prior-agent execution steps, order, confirmation gates, or delivery flow,
  treat it as a high-priority reusable workflow clue, not a mandatory evolution signal.
- Treat something as an evolution opportunity only when it is linked to a used or inferable Skill context
  from recent task context, attribution to Skill instructions, reusable guidance, and no duplicate existing
  coverage.
- If the user gives rule-style guidance such as “you should”, “should first”, “first...then...”,
  “confirm before...”, or “do not directly...”, and this guidance describes a reusable workflow
  or execution rule, first ask whether to distill it into a reusable Skill experience.
- For weak clues, no evolution opportunity, or failures that do not point to a reusable Skill instruction gap, do not
  ask the user to evolve. If you need to respond to this self-check, only say this skill evolution self-check
  did not find any Skill that needs updating.
- Do not ask to evolve for one-off preferences, non-reusable feedback, duplicate coverage, or feedback that cannot
  fit any related Skill context.
- If there is an evolution opportunity, ask in one sentence: This feedback can be distilled into a workflow lesson for
  similar future tasks. Should I record it in the related Skill?
- Enter the tool flow only after user confirmation; do not submit evolution changes before confirmation.
"""

EVOLUTION_PROTOCOL_PROMPT = {
    "cn": EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": EVOLUTION_PROTOCOL_PROMPT_EN,
}

TEAM_EVOLUTION_PROTOCOL_PROMPT_CN = """## 团队 Skill 演进关注点

- 优先判断问题属于 handoff、delegation、shared context、成员执行记录不一致，还是 collaboration
  protocol gap。
- 团队演进审查聚焦可复用的协作改进。
  成员出现执行失败时，仅在其指向团队协议、角色协作或共享上下文问题时才演进。
- 区分 role-local change 与 whole-swarm change：只影响某个成员角色时写入角色局部约束；
  影响团队协作协议时才写入整体 swarm 指令。
- 检查 leader/member 交接、任务声明、共享状态假设和结果汇总是否有可复用改进。
- 当用户用“你应该/应该先/先...再.../确认后再.../不要直接...”等表达描述可复用
  协作流程、角色交接或执行规则时，先询问是否沉淀为团队协作/交付流程经验。
- 一次性偏好、不可复用、已有经验覆盖或无法归入相关 Skill 场景时，不要询问演进。
- 普通成员局部工具失败或一次性任务事实不自动变成 swarm skill 经验。
- 不要仅因当前是团队任务就把普通 skill 写成 swarm-skill；subject.kind 必须来自目标 Skill
  定义。
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT_EN = """## Team Skill Evolution Focus

- First classify whether the issue is handoff, delegation, shared context, mismatch between member
  work records, or a collaboration protocol gap.
- The team evolution review focuses on reusable collaboration improvements and should evolve only when
  evidence points to a team protocol, role coordination, or shared-context problem.
- Distinguish role-local change from whole-swarm change: write role-local constraints when only one member role
  is affected; update the whole swarm only for team protocol changes.
- Check leader/member handoff, task claiming, shared-state assumptions, and result aggregation for reusable
  improvements.
- If the user gives rule-style guidance such as “you should”, “should first”, “first...then...”,
  “confirm before...”, or “do not directly...”, and it describes a reusable collaboration workflow,
  role handoff, or execution rule, first ask whether to distill it into a team collaboration or delivery
  workflow lesson.
- Do not ask to evolve for one-off preferences, non-reusable feedback, duplicate coverage, or feedback that cannot
  fit any related Skill context.
- Ordinary member-local tool failures or one-off task facts do not automatically become swarm skill experiences.
- Do not write a regular skill as swarm-skill just because this is a team task; subject.kind must come from the
  target Skill definition.
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT = {
    "cn": TEAM_EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": TEAM_EVOLUTION_PROTOCOL_PROMPT_EN,
}


def build_evolution_protocol_section(language: str = "cn", *, fuzzy_review: bool = True) -> PromptSection:
    """Build the stable agent-facing evolution protocol section."""
    content = EVOLUTION_PROTOCOL_PROMPT.get(language, EVOLUTION_PROTOCOL_PROMPT_CN)
    if fuzzy_review:
        fuzzy_content = EVOLUTION_FUZZY_REVIEW_PROMPT_EN if language == "en" else EVOLUTION_FUZZY_REVIEW_PROMPT_CN
        content = f"{content}\n{fuzzy_content}"
    return PromptSection(
        name=SectionName.EVOLUTION_PROTOCOL,
        content={language: content},
        priority=86,
    )


def build_team_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the team/swarm-specific evolution protocol section."""
    content = TEAM_EVOLUTION_PROTOCOL_PROMPT.get(language, TEAM_EVOLUTION_PROTOCOL_PROMPT_CN)
    return PromptSection(
        name=SectionName.EVOLUTION_TEAM_PROTOCOL,
        content={language: content},
        priority=87,
    )


__all__ = [
    "EVOLUTION_PROTOCOL_PROMPT",
    "TEAM_EVOLUTION_PROTOCOL_PROMPT",
    "build_evolution_protocol_section",
    "build_team_evolution_protocol_section",
]
