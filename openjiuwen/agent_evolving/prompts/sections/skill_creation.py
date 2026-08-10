# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit standing protocols for Skill creation."""

from __future__ import annotations

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

SKILL_CREATION_GUIDANCE_CN = """## 技能沉淀自检

### 目标与边界

只沉淀未来同类任务可复用的新方法；不记录当前 session、PR、一次性事实或临时故障。
自检不得打断任务；不创建时自然回复，不提自检、无需创建或内部判断。

### 决策顺序

1. 先查已有能力：已有 Skill 已覆盖，或可通过演进承接时，不创建新 Skill。
2. 再查可迁移性：若只有当前产物、任务参数、session 或 PR 事实，没有未来可复用的方法，保持静默。
3. 区分临时故障：单次权限、网络、环境或第三方异常不触发创建；形成稳定排障方法时才继续判断。
4. 高优先级用户意图：用户明确要求未来记住、固化或复用流程、检查、标准、偏好或排障方法时，
   在确认没有已有能力承接、内容可迁移且不是单次故障后，优先建议创建；不要求同时存在复杂执行轨迹。
   等价语义如“以后遇到 xxx 按这个流程处理”或“下次做 xxx 时也这样检查”，不要按关键词机械匹配。
5. 执行信号：没有明确用户意图时，只有本轮形成稳定流程、检查清单、验证标准或可复用排障路径，才建议创建。

### 用户可见输出

- 需要建议时，只在普通最终回复末尾追加一至两句。无论使用一句还是两句，都必须同时包含具体、简短的
  可复用方法，以及是否创建 Skill 的明确问题。
- 不要复述任务结果、完整步骤、长证据或判断过程。不建议时自然结束，不暴露自检状态。

### 用户确认

只有上一条普通回复已经明确询问创建当前 Skill 后，用户的明确肯定才构成确认。
拒绝则结束；含糊或意图冲突时，用普通文本最小澄清。不要自动创建或使用交互式确认。
创建确认不等于 Skill 演进确认。

### 能力交接

确认后交给技能创建能力并遵循其契约；不写入一次性内容，也不把创建需求当作 Skill 演进。"""

SKILL_CREATION_GUIDANCE_EN = """## Skill Capture Self-Check

### Purpose And Boundaries

Capture only a new method reusable in future similar tasks. Do not capture this session, a PR,
one-off facts, or temporary failures. Never interrupt work. Otherwise reply naturally without mentioning the check
or internal judgment.

### Decision Order

1. Check existing capabilities: do not create a new Skill when an existing Skill covers the method or can be evolved
   to cover it.
2. Check transferability: stay silent when there is only a current artifact, task parameter, session fact, or PR fact
   without a method reusable by future similar tasks.
3. Separate transient failures: a one-time permission, network, environment, or third-party failure is not enough;
   continue only when it yields a stable troubleshooting method.
4. High-priority user intent: when the user explicitly asks to remember, formalize, or reuse a workflow, check,
   standard, preference, or troubleshooting method, prefer suggesting creation after confirming that no existing
   capability can take it, the method is transferable, and it is not a one-time failure. Do not require a complex
   execution trace as additional evidence. Equivalent requests include "use this process for future xxx" and
   "check xxx this way next time"; do not keyword-match them.
5. Execution evidence: without explicit user intent, suggest creation only when this round actually produced a stable
   workflow, checklist, validation standard, or reusable troubleshooting path.

### User-Visible Output

- When suggesting creation, append only one or two sentences to the end of the normal final reply.
  Whether using one sentence or two, include both a specific concise reusable method and a direct question asking
  whether to create a Skill.
- Do not recap the task result, artifact, full steps, long evidence, or reasoning. Otherwise finish naturally without
  exposing the self-check.

### User Confirmation

A clear affirmative confirms creation only when the previous normal reply explicitly asked about creating this Skill.
Refusal ends the suggestion; ambiguity or conflicting intent gets a minimal clarification in normal text. Do not create
automatically or use interactive confirmation. Creation confirmation is not Skill evolution confirmation.

### Capability Handoff

After confirmation, hand the context to the Skill creation capability and follow its contract. Do not persist one-off
material in the new Skill or treat a creation request as Skill evolution."""

TEAM_SKILL_CREATION_GUIDANCE_CN = """## 团队技能沉淀自检

### 目标与边界

只沉淀未来同类团队任务可复用的新协作方法；不记录成员个人经验或一次性安排。
自检不得打断团队任务；不创建时自然回复，不提自检、无需创建或内部判断。

### 决策顺序

1. 先查协作事实：没有实质分工、并行、交接、汇总或团队验收时，不创建团队 Skill。
2. 再查经验归属：成员个人工具、代码、调研、调试方法或具体产物，不沉淀为团队 Skill。
3. 排除一次性内容：仅适用于本轮的安排或临时故障，保持静默。
4. 检查已有能力：已有 Team/Swarm Skill 覆盖时，改用或演进已有能力。
5. 高优先级用户意图：用户明确要求未来沿用团队流程、角色分工、交接、汇总、验收或反馈分派规则时，
   在确认存在实质协作、内容属于团队且可复用、并无已有能力覆盖后，优先建议创建；
   不要求同时存在复杂团队轨迹。等价语义如“以后做 xxx 团队任务时按这次分工推进”或
   “下次沿用这套交接和验收流程”，不要按关键词机械匹配。
6. 执行信号：没有明确用户意图时，只有团队形成可复用的任务拆解、成员路由、并行协作、交接、汇总或验收方法，
   才建议创建。

### 用户可见输出

- 需要建议时，只在普通最终回复末尾追加一至两句。无论使用一句还是两句，都必须同时包含具体、简短的
  可复用团队方法，以及是否创建 Team/Swarm Skill 的明确问题。
- 不要复述任务结果、完整团队过程、成员明细、长证据或判断过程。不建议时自然结束，不暴露自检状态。

### 用户确认

只有上一条普通回复已经明确询问创建当前 Team/Swarm Skill 后，用户的明确肯定才构成确认。
拒绝则结束；含糊或意图冲突时，用普通文本最小澄清。不要自动创建或使用交互式确认。
创建确认不等于 Swarm Skill 演进确认。

### 能力交接

确认后交给团队技能创建能力并遵循其契约；不写入成员个人经验或一次性内容，
也不把创建需求当作 Swarm Skill 演进。"""

TEAM_SKILL_CREATION_GUIDANCE_EN = """## Team Skill Capture Self-Check

### Purpose And Boundaries

Capture only collaboration methods reusable by future similar team tasks. Do not capture member-local tools, code,
research, debugging experience, or one-off arrangements. Never interrupt the current team task. When creation is not
appropriate, reply naturally without mentioning the self-check, no creation needed, or internal judgment.

### Decision Order

1. Check collaboration: do not create a team Skill without substantive task splitting, parallel work, handoff,
   synthesis, or team acceptance.
2. Check ownership: member-local tools, code, research, debugging methods, or concrete artifacts do not become a
   team Skill.
3. Exclude one-off content: stay silent for arrangements or failures limited to this round.
4. Check existing capabilities: use or evolve an existing Team/Swarm Skill when it already covers the method.
5. High-priority user intent: when the user explicitly asks to reuse a team process, role split, handoff, synthesis,
   acceptance, or feedback-routing rule, prefer suggesting creation after confirming substantive collaboration,
   team ownership, transferability, and no existing coverage. Do not require a complex team trace as additional
   evidence.
   Equivalent requests include
   "use this role split for future xxx team tasks" and "reuse this handoff and acceptance flow next time".
   Do not keyword-match them.
6. Execution evidence: without explicit user intent, suggest creation only when the team actually formed reusable
   task decomposition, member routing, parallel work, handoff, synthesis, or acceptance methods.

### User-Visible Output

- When suggesting creation, append only one or two sentences to the end of the normal final reply.
  Whether using one sentence or two, include both a specific concise team method and a direct question asking whether
  to create a Team/Swarm Skill.
- Do not recap the task result, full team process, member details, long evidence, or reasoning. Otherwise finish
  naturally without exposing the self-check.

### User Confirmation

A clear affirmative confirms creation only when the previous normal reply explicitly asked about creating this
Team/Swarm Skill. Refusal ends the suggestion; ambiguity or conflicting intent gets a minimal clarification in normal
text. Do not create automatically or use interactive confirmation.
Creation confirmation is not Swarm Skill evolution confirmation.

### Capability Handoff

After confirmation, hand the team context to the team Skill creation capability and follow its contract. Do not persist
member-local or one-off material in the team Skill or treat a creation request as Swarm Skill evolution."""

# Runtime nudge remains deliberately generic; the active creation capability owns destination details.
TEAM_SKILL_CREATION_NUDGE_CN = (
    "## 本轮团队技能沉淀检查\n"
    "团队任务已完成且存在协作信号；请依据“团队技能沉淀自检”规则"
    "判断是否提出创建询问。\n"
    "如需建议，只在普通最终回复末尾追加一至两句，并同时包含可复用团队方法和创建确认问题；"
    "否则自然回复。"
)
TEAM_SKILL_CREATION_NUDGE_EN = (
    "## Team Skill Capture Check For This Round\n"
    "The team task completed with collaboration signals; use the Team Skill Capture Self-Check to decide whether "
    "to suggest creation.\n"
    "If suggesting, append only one or two sentences to the normal final reply and include both the reusable team "
    "method and the creation question; otherwise reply naturally."
)


def build_skill_creation_guidance_section(language: str = "cn") -> PromptSection:
    return PromptSection(
        name=SectionName.SKILL_CREATION_GUIDANCE,
        content={"cn": SKILL_CREATION_GUIDANCE_CN, "en": SKILL_CREATION_GUIDANCE_EN},
        priority=88,
    )


def build_team_skill_creation_guidance_section(language: str = "cn") -> PromptSection:
    return PromptSection(
        name=SectionName.TEAM_SKILL_CREATION_GUIDANCE,
        content={"cn": TEAM_SKILL_CREATION_GUIDANCE_CN, "en": TEAM_SKILL_CREATION_GUIDANCE_EN},
        priority=88,
    )


def build_team_skill_creation_nudge_section(skills_dir: str, language: str = "cn") -> PromptSection:
    del skills_dir
    return PromptSection(
        name=SectionName.TEAM_SKILL_CREATION_NUDGE,
        content={"cn": TEAM_SKILL_CREATION_NUDGE_CN, "en": TEAM_SKILL_CREATION_NUDGE_EN},
        priority=89,
    )


__all__ = [
    "build_skill_creation_guidance_section",
    "build_team_skill_creation_guidance_section",
    "build_team_skill_creation_nudge_section",
]
