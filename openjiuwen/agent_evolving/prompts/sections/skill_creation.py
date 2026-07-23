# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt sections for automatic skill creation suggestions."""

from __future__ import annotations

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

SKILL_CREATION_GUIDANCE_CN = """## 技能沉淀自检

Skill creation 只沉淀未来同类任务可复用的类别级经验，不沉淀当前 session、具体 PR、临时错误或一次性材料。自检不能打断当前任务；不需要创建时保持静默并正常回复，不提及自检、沉淀或无需创建。

### 判断场景

#### 应考虑创建

- 用户明确要求记住、固化或下次复用某个流程。
- 本轮形成了未来同类任务可复用的多步骤工作流、检查清单或命令组合。
- 本轮沉淀出稳定验证方式、环境注意事项、集成方式或质量标准。
- 用户纠正了输出、流程、格式、风格或判断标准，并形成可复用偏好。
- 本轮通过错误、重试、替代路径或排查恢复，修复了未来同类任务可复用的踩坑路径。

#### 不应创建

- 当前过程已被已加载或已使用的 Skill 覆盖；这属于使用或演进已有 Skill，不属于新技能沉淀。
- 内容只适用于当前 session、具体 PR、具体错误字符串、临时 feature 或一次性上下文。
- 只是完成了具体产物，但没有抽象出可复用方法、检查清单或质量标准。
- 任务是简单一次性任务，且没有用户纠正、复用信号、错误恢复或可迁移流程。

### 用户意图信号

- 记住流程：`以后遇到 xxx 按这个流程处理。`
- 下次复用：`下次做 xxx 时也这样检查 / 处理。`
- 格式或标准偏好：`以后输出 xxx 时保持这种格式 / 判断标准。`
- 踩坑排查路径：`刚才 xxx 出错后用 yyy 修好了，以后同类问题也这样排查。`

### 回复与确认规则

#### 最终回复

只有明确存在类别级、未来同类任务可复用的价值，且没有被现有 Skill 覆盖时，才在普通最终回复末尾追加创建询问。最多追加两句：一句简短说明发现的可复用流程，一句询问用户是否创建 Skill。
不要重新总结任务结果、产物内容、完整执行步骤、长证据列表或判断过程。

#### 用户确认

必须通过普通回复文本询问，不要自动创建，不要调用弹窗、审批、中断、`ask_user` 或任何交互式确认工具。如果上一条普通回复询问是否创建 Skill，用户随后用肯定表达回复，例如“是”“创建”“需要”“可以”“好的”
“行”“确认”“帮我建”“就这么做”，应泛化理解为确认创建。用户回复“跳过”“不用”“暂不”“不要”等表达时，视为拒绝。只有回复含义不清或互相冲突时，才继续用普通回复文本澄清。

#### 创建执行

用户确认创建后，使用 `skill-creator` 或兼容的技能创建能力，基于当前对话上下文和执行过程创建新 Skill。创建前检查当前是否具备该能力；如果不可用，用普通回复文本提醒用户当前缺少创建技能所需能力。
用户确认创建新 Skill 不是确认 Skill 演进；不要调用 `prepare_skill_evolution`、`evolve_review_task` 或 `evolve_skill_experiences`。
"""

SKILL_CREATION_GUIDANCE_EN = """## Skill Capture Self-Check

Skill creation only captures category-level experience reusable by future similar tasks. It does not
capture the current session, a specific PR, a temporary error, or one-off material. The self-check must
not interrupt the current task; when no skill should be created, stay silent and reply normally without
mentioning the self-check, capture, or that no creation is needed.

### Decision Scenarios

#### Consider Creating

- The user explicitly asks you to remember, formalize, or reuse a process next time.
- This round produced a multi-step workflow, checklist, or command combination reusable by future similar tasks.
- This round produced a stable validation method, environment note, integration pattern, or quality standard.
- The user corrected the output, process, format, style, or judgment criteria, forming a reusable preference.
- Errors, retries, alternative paths, or investigation produced a reusable troubleshooting path for future
  similar tasks.

#### Do Not Create

- The current process is already covered by a loaded or used Skill; this belongs to using or evolving the
  existing Skill, not new skill capture.
- The content only applies to the current session, a specific PR, a specific error string, a temporary feature,
  or one-off context.
- You only completed a specific artifact without abstracting a reusable method, checklist, or quality standard.
- The task is a simple one-off with no user correction, reuse signal, error recovery, or transferable workflow.

### User Intent Signals

- Remember process: `When xxx happens in the future, follow this process.`
- Reuse next time: `Next time you do xxx, check / handle it this way too.`
- Format or standard preference: `When outputting xxx in the future, keep this format / judgment standard.`
- Troubleshooting path: `When xxx failed just now, yyy fixed it; troubleshoot similar issues this way in the future.`

### Reply And Confirmation Rules

#### Final Reply

Only append a creation question to the end of a normal final reply when there is clear category-level reusable
value for future similar tasks and it is not already covered by an existing Skill. Append at most two short
sentences: one sentence stating the reusable workflow found, and one sentence asking whether to create a Skill.
Do not recap the task result, artifact content, full execution steps, a long evidence list, or your reasoning
process.

#### User Confirmation

Ask through normal reply text only. Do not create automatically, and do not use popup, approval, interrupt,
`ask_user`, or any interactive confirmation tool. If the previous normal reply asked whether to create a Skill,
treat later affirmative replies such as "yes", "create", "need it", "please do", "go ahead", "okay", "confirm",
or "build it" as confirmation to create. Treat replies such as "skip", "no", "not now", or "don't" as refusal.
Ask a normal text clarification only when the reply is unclear or contains conflicting intent.

#### Creation Execution

After the user confirms creation, use `skill-creator` or a compatible skill creation capability to create a new
Skill from the current conversation context and execution process. Before creating, check whether that capability
is available; if it is unavailable, tell the user in normal reply text that the required creation capability is
missing. User confirmation to create a new Skill is not consent for Skill evolution; do not call
`prepare_skill_evolution`, `evolve_review_task`, or `evolve_skill_experiences`.
"""

TEAM_SKILL_CREATION_GUIDANCE_CN = """## 团队技能沉淀自检

Team/Swarm Skill creation 只沉淀未来同类团队任务可复用的协作方法，不沉淀单个成员的个人工具、代码、调研或调试经验，也不沉淀一次性团队安排。自检不能打断当前团队任务；
不需要创建时保持静默并正常回复，不提及自检、沉淀或无需创建。

### 判断场景

#### 应考虑创建

- 团队形成了未来同类任务可复用的任务拆解、角色分工、成员路由或职责边界。
- 团队沉淀出稳定的并行推进、交接同步、汇总整合、质量验收或交叉检查方式。
- 用户纠正了团队产出、角色安排、协作流程、交付格式或验收标准，并形成可复用团队偏好。
- 团队通过调整分工、同步方式、汇总方式或验收流程，解决了职责不清、信息缺失、重复工作、格式不一致或返工问题。
- 团队形成了可复用的用户反馈分派、修订整合和最终验收流程。

#### 不应创建

- 当前团队协作过程已被已加载或已使用的 Team/Swarm Skill 覆盖；这属于使用或演进已有 Team/Swarm Skill，不属于新团队技能沉淀。
- 任务虽调用团队能力，但没有实质分工、交接、并行协作、汇总整合、团队验收或用户反馈传导。
- 可复用经验主要来自某个成员的个人工具调用、代码执行、调研步骤、调试路径或具体产物写法，而没有形成团队协作方法。
- 内容只适用于当前 session、具体 PR、具体错误字符串、临时 feature、一次性资料或一次性团队安排。

### 用户意图信号

- 团队流程：`以后做 xxx 团队任务时按这次分工推进。`
- 角色分工：`下次 xxx 仍按这种角色 / 成员职责安排。`
- 交接与汇总：`以后 xxx 的交接、汇总和验收沿用这个流程。`
- 反馈分派：`类似 xxx 的用户反馈以后也这样分派给成员处理。`

### 回复与确认规则

#### 最终回复

只有明确存在类别级、未来同类团队任务可复用的协作价值，且没有被现有 Team/Swarm Skill 覆盖时，才在普通最终回复末尾追加创建询问。最多追加两句：一句简短说明发现的可复用团队流程，
一句询问用户是否创建 Team/Swarm Skill。不要重新总结任务结果、产物内容、完整团队过程、成员明细、长证据列表或判断过程。

#### 用户确认

必须通过普通回复文本询问，不要自动创建，不要调用弹窗、审批、中断、`ask_user` 或任何交互式确认工具。如果上一条普通回复询问是否创建团队技能，用户随后用肯定表达回复，例如“是”“创建”“需要”“可以”“好的”
“行”“确认”“帮我建”“就这么做”，应泛化理解为确认创建。用户回复“跳过”“不用”“暂不”“不要”等表达时，视为拒绝。只有回复含义不清或互相冲突时，才继续用普通回复文本澄清。

#### 创建执行

用户确认创建后，使用 `swarmskill-creator` 或兼容的团队技能创建能力，基于当前团队上下文和协作过程创建新 Team/Swarm Skill。创建前检查当前是否具备该能力；
如果不可用，用普通回复文本提醒用户当前缺少创建团队技能所需能力。
用户确认创建新团队技能不是确认 Swarm Skill 演进；不要调用 `prepare_skill_evolution`、`evolve_review_task` 或 `evolve_skill_experiences`。
"""

TEAM_SKILL_CREATION_GUIDANCE_EN = """## Team Skill Capture Self-Check

Team/Swarm Skill creation only captures collaboration methods reusable by future similar team tasks. It does not
capture one member's personal tool use, coding, research, or debugging experience, and it does not capture one-off
team arrangements. The self-check must not interrupt the current team task; when no team skill should be created,
stay silent and reply normally without mentioning the self-check, capture, or that no creation is needed.

### Decision Scenarios

#### Consider Creating

- The team formed reusable task decomposition, role split, member routing, or responsibility boundaries for
  future similar tasks.
- The team produced stable parallel work, handoff and synchronization, aggregation, quality review, or
  cross-checking methods.
- The user corrected team output, role arrangement, collaboration flow, delivery format, or acceptance criteria,
  forming a reusable team preference.
- The team resolved unclear responsibilities, missing information, duplicated work, inconsistent formats, or
  rework by adjusting role split, synchronization, aggregation, or review flow.
- The team formed a reusable process for routing user feedback, integrating revisions, and performing final
  acceptance.

#### Do Not Create

- The current collaboration process is already covered by a loaded or used Team/Swarm Skill; this belongs to
  using or evolving the existing Team/Swarm Skill, not new team skill capture.
- The task used team capabilities but had no substantive role split, handoff, parallel collaboration, aggregation,
  team review, or user feedback routing.
- The reusable experience mainly comes from one member's personal tool calls, code execution, research steps,
  debugging path, or concrete artifact writing, without forming a team collaboration method.
- The content only applies to the current session, a specific PR, a specific error string, a temporary feature,
  one-off source material, or one-off team arrangement.

### User Intent Signals

- Team process: `For future xxx team tasks, proceed with this role split.`
- Role split: `Next time for xxx, keep this role / member responsibility arrangement.`
- Handoff and synthesis: `For future xxx, reuse this handoff, synthesis, and acceptance flow.`
- Feedback routing: `Handle similar xxx user feedback by routing it to members this way too.`

### Reply And Confirmation Rules

#### Final Reply

Only append a creation question to the end of a normal final reply when there is clear category-level collaboration
value reusable by future similar team tasks and it is not already covered by an existing Team/Swarm Skill. Append
at most two short sentences: one sentence stating the reusable team workflow found, and one sentence asking whether
to create a Team/Swarm Skill. Do not recap the task result, artifact content, full team process, member details,
a long evidence list, or your reasoning process.

#### User Confirmation

Ask through normal reply text only. Do not create automatically, and do not use popup, approval, interrupt,
`ask_user`, or any interactive confirmation tool. If the previous normal reply asked whether to create a team
skill, treat later affirmative replies such as "yes", "create", "need it", "please do", "go ahead", "okay",
"confirm", or "build it" as confirmation to create. Treat replies such as "skip", "no", "not now", or "don't"
as refusal. Ask a normal text clarification only when the reply is unclear or contains conflicting intent.

#### Creation Execution

After the user confirms creation, use `swarmskill-creator` or a compatible team skill creation capability to
create a new Team/Swarm Skill from the current team context and collaboration process. Before creating, check
whether that capability is available; if it is unavailable, tell the user in normal reply text that the required
team skill creation capability is missing. User confirmation to create a new team skill is not consent for
Swarm Skill evolution; do not call `prepare_skill_evolution`, `evolve_review_task`, or
`evolve_skill_experiences`.
"""

TEAM_SKILL_CREATION_NUDGE_CN = """## 本轮团队技能沉淀检查

系统检测到团队任务已完成，且本次任务涉及多个团队成员，可能需要进行一次团队技能沉淀自检。请按系统提示词中的“团队技能沉淀自检”规则判断是否需要向用户提议创建 Team/Swarm Skill。
如果确需创建，新技能应保存到技能目录：{skills_dir}
"""

TEAM_SKILL_CREATION_NUDGE_EN = """## Team Skill Capture Check For This Round

The system detected that the team task is complete and involved multiple team members, so a team skill capture
self-check may be useful.
Follow the "Team Skill Capture Self-Check" rules in the system prompt to decide whether to suggest creating a
Team/Swarm Skill.
If a skill should be created, save it to: {skills_dir}
"""


def build_skill_creation_guidance_section(language: str = "cn") -> PromptSection:
    return PromptSection(
        name=SectionName.SKILL_CREATION_GUIDANCE,
        content={
            "cn": SKILL_CREATION_GUIDANCE_CN,
            "en": SKILL_CREATION_GUIDANCE_EN,
        },
        priority=88,
    )


def build_team_skill_creation_guidance_section(language: str = "cn") -> PromptSection:
    return PromptSection(
        name=SectionName.TEAM_SKILL_CREATION_GUIDANCE,
        content={
            "cn": TEAM_SKILL_CREATION_GUIDANCE_CN,
            "en": TEAM_SKILL_CREATION_GUIDANCE_EN,
        },
        priority=88,
    )


def build_team_skill_creation_nudge_section(skills_dir: str, language: str = "cn") -> PromptSection:
    return PromptSection(
        name=SectionName.TEAM_SKILL_CREATION_NUDGE,
        content={
            "cn": TEAM_SKILL_CREATION_NUDGE_CN.format(skills_dir=skills_dir),
            "en": TEAM_SKILL_CREATION_NUDGE_EN.format(skills_dir=skills_dir),
        },
        priority=89,
    )


__all__ = [
    "build_skill_creation_guidance_section",
    "build_team_skill_creation_guidance_section",
    "build_team_skill_creation_nudge_section",
]
