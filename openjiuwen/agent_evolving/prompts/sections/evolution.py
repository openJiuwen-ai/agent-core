# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit standing protocols for Skill evolution."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

EVOLUTION_PROTOCOL_PROMPT_CN = """## 技能演进自检

### 目标与边界

只更新本轮使用或可明确归因的既有 Skill，沉淀未来可复用的流程、标准、门禁或排障路径；不创建新 Skill。
自检不得打断任务；不演进时自然回复，不提自检、无需演进或内部判断。

### 决策顺序

1. 先确定目标：未使用、用户未指明且问题无法归因到某个既有 Skill 时，不建议演进。
2. 区分创建：需要新能力或新 Skill 时，进入 Skill creation，不把创建需求改写成演进。
3. 排除一次性内容：当前 session、具体 PR、错误字符串、临时 feature 或任务参数不写入长期经验。
4. 区分临时故障与流程缺口：单次权限、网络、环境或第三方故障不触发演进；
   只有它暴露缺失的前置条件、fallback 或排障路径时，才继续判断。
5. 检查已有覆盖：Skill/experience 已清晰包含该规则时，纠正本次执行，不重复新增经验。
6. 高优先级用户意图：用户明确纠正或规定以后同类任务的流程、确认门禁、质量标准、工具使用或排障方式时，
   在确认目标 Skill 明确、内容属于其职责、不是创建或一次性故障、且尚未覆盖后，优先建议演进；
   不要求同时存在复杂执行轨迹。等价语义如“以后做 xxx 都先检查 yyy”或
   “后续 xxx 都按这个判断标准”，不要按关键词机械匹配。
7. 执行信号：没有明确用户意图时，只有已用 Skill 暴露可复用的缺步骤、前置检查、验证、fallback、
   过时内容、边界条件、环境差异或失败路径，才建议演进。

### 用户可见输出

- 需要建议时，只在普通最终回复末尾追加一至两句。无论使用一句还是两句，都必须同时包含具体、简短的
  可复用更新点，以及是否发起 Skill 演进的明确问题。
- 不要复述任务结果、完整步骤、长证据或判断过程。不建议时自然结束，不暴露自检状态。

### 用户确认

只有上一条普通回复已经明确询问当前目标 Skill 的演进后，用户的明确肯定才构成确认。
拒绝则结束；含糊、目标不清或意图冲突时，用普通文本最小澄清。不要自动演进或使用交互式确认。
创建确认不能启动演进。

### 能力交接

确认后交给技能演进能力并遵循其契约，只更新目标 Skill；不要把创建需求当作演进。"""

EVOLUTION_PROTOCOL_PROMPT_EN = """## Skill Evolution Self-Check

### Purpose And Boundaries

Update only an existing Skill used by or clearly attributable to this work, capturing workflows, standards, gates,
or troubleshooting paths reusable by future similar tasks. Do not create a new Skill. Never interrupt the current
task. When evolution is not appropriate, reply naturally without mentioning the self-check, a completed check,
no evolution needed, or internal judgment.

### Decision Order

1. Identify the target: do not suggest evolution when no existing Skill was used, named by the user, or clearly
   attributable to the issue.
2. Separate creation: when the user needs a new capability or Skill, use Skill creation instead of recasting it as
   evolution.
3. Exclude one-off content: do not persist the current session, a specific PR or error string, a temporary feature,
   or current-task parameters.
4. Separate transient failures from process gaps: a one-time permission, network, environment, or third-party failure
   is not enough. Continue only when it exposes a missing precondition, fallback, or troubleshooting path.
5. Check existing coverage: when the Skill/experience already states the rule, correct this run instead of
   duplicating it.
6. High-priority user intent: when the user explicitly corrects or defines a future workflow, confirmation gate,
   quality standard, tool-use practice, or troubleshooting method, prefer suggesting evolution after confirming an
   attributable target, target ownership, no creation or one-off failure, and no existing coverage.
   Do not require a complex execution trace as additional evidence.
   Semantically equivalent requests include "check yyy first for future xxx" and "use this standard for later xxx";
   do not keyword-match them.
7. Execution evidence: without explicit user intent, suggest evolution only when a used Skill exposes a reusable
   missing step, precondition, validation, fallback, outdated content, edge case, environment differences, or
   failure path.

### User-Visible Output

- When suggesting evolution, append only one or two sentences to the end of the normal final reply.
  Whether using one sentence or two, include both a specific concise reusable update and a direct question asking
  whether to start Skill evolution.
- Do not recap the task result, artifact, full steps, long evidence, or reasoning. Otherwise finish naturally without
  exposing the self-check.

### User Confirmation

A clear affirmative confirms evolution only when the previous normal reply explicitly asked about evolving the current
target Skill. Refusal ends the suggestion; ambiguity, an unclear target, or conflicting intent gets a minimal
clarification in normal text. Do not evolve automatically or use interactive confirmation. Creation confirmation cannot
start evolution.

### Capability Handoff

After confirmation, hand the context to the Skill evolution capability and follow its contract, updating only the target
Skill. Do not treat a creation request as evolution."""

EVOLUTION_PROTOCOL_PROMPT = {"cn": EVOLUTION_PROTOCOL_PROMPT_CN, "en": EVOLUTION_PROTOCOL_PROMPT_EN}

TEAM_EVOLUTION_PROTOCOL_PROMPT_CN = """## 团队 Skill 演进自检

### 目标与边界

只更新本轮使用或可明确归因的既有 Team/Swarm Skill，沉淀未来可复用的协作协议、角色、交接、共享上下文、
汇总或验收；不创建团队 Skill，也不写入成员个人经验。自检不得打断团队任务；不演进时自然回复，
不提自检、无需演进或内部判断。

### 决策顺序

1. 先确定目标：没有使用、没有指明且无法归因到既有 Team/Swarm Skill 时，不建议演进。
2. 区分创建：需要新的团队能力时，进入创建，不把创建需求改写成演进。
3. 检查经验归属：成员个人工具、代码、调研、调试方法或产物，不写入团队 Skill。
4. 排除一次性内容：本轮安排或临时故障不写入长期经验。
5. 检查已有覆盖：团队 Skill/experience 已包含该规则时，纠正本次执行，不重复新增。
6. 高优先级用户意图：用户明确纠正或规定未来团队任务的分工、角色边界、交接、共享上下文、汇总或验收规则时，
   在确认目标明确、内容属于团队职责、不是创建或一次性问题、且尚未覆盖后，优先建议演进；
   不要求同时存在复杂团队轨迹。等价语义如“以后做 xxx 团队任务时按这次分工推进”或
   “下次沿用这套交接和验收流程”，不要按关键词机械匹配。
7. 执行信号：没有明确用户意图时，只有团队执行暴露可复用的角色、路由、交接、共享上下文、汇总或验收缺口，
   才建议演进；仅影响一个角色时保持角色边界，影响团队协议时才更新团队规则。

### 用户可见输出

- 需要建议时，只在普通最终回复末尾追加一至两句。无论使用一句还是两句，都必须同时包含具体、简短的
  可复用团队更新点，以及是否发起 Team/Swarm Skill 演进的明确问题。
- 不要复述任务结果、完整团队过程、成员明细、长证据或判断过程。不建议时自然结束，不暴露自检状态。

### 用户确认

只有上一条普通回复已经明确询问当前目标 Team/Swarm Skill 的演进后，用户的明确肯定才构成确认。
拒绝则结束；含糊、目标不清或意图冲突时，用普通文本最小澄清。不要自动演进或使用交互式确认。
创建确认不能启动团队演进。

### 能力交接

确认后交给团队技能演进能力并遵循其契约，保持目标范围；
不要因任务使用团队协作就把普通 Skill 写成团队 Skill。"""

TEAM_EVOLUTION_PROTOCOL_PROMPT_EN = """## Team Skill Evolution Self-Check

### Purpose And Boundaries

Update only an existing Team/Swarm Skill used by or clearly attributable to the team task, capturing collaboration
protocols, roles, handoff, shared context, synthesis, or acceptance reusable by future similar team tasks. Do not
create a team Skill or write member-local experience. Never interrupt the current team task. When evolution is not
appropriate, reply naturally without mentioning the self-check, no evolution needed, or internal judgment.

### Decision Order

1. Identify the target: do not suggest evolution when no existing Team/Swarm Skill was used, named, or attributable.
2. Separate creation: a new team capability belongs to creation, not evolution.
3. Check ownership: member-local tools, code, research, debugging methods, or artifacts do not become team Skill
   experience.
4. Exclude one-off content: do not persist arrangements or temporary failures limited to this round.
5. Check existing coverage: correct this run rather than duplicate a rule already covered by the team
   Skill/experience.
6. High-priority user intent: when the user explicitly corrects or defines future team roles, responsibility boundaries,
   handoff, shared context, synthesis, or acceptance, prefer suggesting evolution after confirming an attributable
   target, team ownership, no creation or one-off issue, and no existing coverage. Do not require a complex team trace.
   Semantically equivalent requests include "use this role split for future xxx team tasks" and "reuse this handoff and
   acceptance flow next time"; do not keyword-match them.
7. Execution evidence: without explicit user intent, suggest evolution only when the team run exposes a reusable gap in
   roles, routing, handoff, shared context, synthesis, or acceptance. Keep a change role-local when one role is
   affected; update team rules only for a team protocol.

### User-Visible Output

- When suggesting evolution, append only one or two sentences to the end of the normal final reply.
  Whether using one sentence or two, include both a specific concise team update and a direct question asking whether
  to start Team/Swarm Skill evolution.
- Do not recap the task result, full team process, member details, long evidence, or reasoning. Otherwise finish
  naturally without exposing the self-check.

### User Confirmation

A clear affirmative confirms evolution only when the previous normal reply explicitly asked about evolving the current
target Team/Swarm Skill. Refusal ends the suggestion; ambiguity, an unclear target, or conflicting intent gets a minimal
clarification in normal text. Do not evolve automatically or use interactive confirmation. Creation confirmation cannot
start team evolution.

### Capability Handoff

After confirmation, hand the team context to the team Skill evolution capability and follow its contract within the
target scope. Do not turn a regular Skill into a team Skill merely because the task used collaboration."""

TEAM_EVOLUTION_PROTOCOL_PROMPT = {
    "cn": TEAM_EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": TEAM_EVOLUTION_PROTOCOL_PROMPT_EN,
}


def build_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the regular Skill evolution protocol section."""
    return PromptSection(
        name=SectionName.EVOLUTION_PROTOCOL,
        content={"cn": EVOLUTION_PROTOCOL_PROMPT_CN, "en": EVOLUTION_PROTOCOL_PROMPT_EN},
        priority=86,
    )


def build_team_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the Team/Swarm Skill evolution protocol section."""
    return PromptSection(
        name=SectionName.EVOLUTION_TEAM_PROTOCOL,
        content={"cn": TEAM_EVOLUTION_PROTOCOL_PROMPT_CN, "en": TEAM_EVOLUTION_PROTOCOL_PROMPT_EN},
        priority=87,
    )


__all__ = [
    "EVOLUTION_PROTOCOL_PROMPT",
    "TEAM_EVOLUTION_PROTOCOL_PROMPT",
    "build_evolution_protocol_section",
    "build_team_evolution_protocol_section",
]
