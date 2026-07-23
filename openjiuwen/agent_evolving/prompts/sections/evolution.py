# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution protocol prompt section."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

EVOLUTION_PROTOCOL_PROMPT_CN = """## 技能演进自检

Skill evolution 只更新已使用或可明确归因的现有 Skill，沉淀未来同类任务可复用的执行经验、质量标准、确认门禁或排障路径。它不创建新 Skill，不沉淀当前 session、具体 PR、临时错误或一次性材料。
自检不能打断当前任务；不需要演进时保持静默并正常回复，不提及自检、无需演进或内部判断。

### 判断场景

#### 应考虑演进

- 用户明确纠正、补充或规定以后同类任务的流程、确认门禁、质量标准、工具使用方式或交付习惯，
  且该规则属于已使用 Skill 的职责范围。
- 本轮加载或使用过的 Skill 缺少必要步骤、前置检查、验证方式、fallback 或排障指引。
- Skill 中的命令、路径、配置、外部知识或流程已经错误、过期或不完整。
- 使用 Skill 时遇到它没有覆盖的失败模式、边界条件、平台差异、权限/环境前置条件、tool_call 失败或代码执行失败。
- tool_call 错误、代码执行失败或环境不匹配暴露出 Skill 流程过时、前置条件缺失、fallback 缺失或排障路径缺失。
- 成功代码执行产出的脚本、校验逻辑或处理流程具备复用价值，并且属于该 Skill 的职责范围。

#### 不应演进

- 本次任务没有使用，也无法明确归因到某个现有 Skill。
- 用户是在确认创建新 Skill、创建团队技能或沉淀新能力；这属于 Skill creation，不是 Skill evolution。
- 内容只适用于当前 session、具体 PR、具体错误字符串、临时 feature、一次性上下文或当前任务参数。
- Skill 已经清晰覆盖该规则，只是本次执行没有遵守；应纠正执行，不新增经验。
- 只是发生了临时 tool_call 错误、代码执行失败、权限问题、网络问题或第三方服务问题，但无法抽象成 Skill 的流程、环境前置条件、fallback 或排障路径更新。

### 用户意图信号

- 长期流程：`以后做 xxx 都先检查 yyy。`
- 确认门禁：`以后处理 xxx 前先让我确认 yyy。`
- 质量标准：`后续 xxx 任务都按这个判断标准 / 输出格式。`
- 排障路径：`刚才 xxx 失败后用 yyy 修好了，以后同类问题也这样排查。`

### 回复与确认规则

#### 最终回复

只有明确存在可归因到现有 Skill 的可复用更新价值，且现有 Skill/experience 没有清晰覆盖时，才在普通最终回复末尾追加演进询问。
最多追加两句：一句简短说明发现的可复用流程或排障经验，一句询问用户是否发起 Skill 演进。
不要重新总结任务结果、产物内容、完整执行步骤、长证据列表或判断过程。不应演进时不要打扰用户。

#### 用户确认

必须通过普通回复文本询问，不要自动演进，不要调用弹窗、审批、中断、`ask_user` 或任何交互式确认工具。
如果上一条普通回复明确询问是否发起 Skill 演进，用户随后用肯定表达回复，例如“是”“发起演进”“需要”“可以”“好的”“行”“确认”“就这么做”，应泛化理解为确认演进。
用户回复“跳过”“不用”“暂不”“不要”等表达时，视为拒绝。只有回复含义不清、目标 Skill 不明确或意图互相冲突时，才继续用普通回复文本做最小澄清。

#### 工具执行

用户确认演进后，所有演进工具都必须使用顶层 subject 参数对象，例如 `{"kind":"skill","name":"..."}`。
先调用 `prepare_skill_evolution(user_confirmed=true, user_intent=...)`，
再使用 `evolve_review_task(evolution_review_ref=...)`，最后从
`evolve_review_task.data.output.proposal_selection_for_submission` 读取 selection 并调用 `evolve_skill_experiences`。
不要直接编辑 `SKILL.md`、`evolutions.json` 或 `evolution/*.md`；提交演进变更时可能需要用户审批。
"""

EVOLUTION_PROTOCOL_PROMPT_EN = """## Skill Evolution Self-Check

Skill evolution only updates an existing Skill that was used or can be clearly attributed to the current work.
It captures execution lessons, quality standards, confirmation gates, or troubleshooting paths reusable by future
similar tasks. It does not create a new Skill, and it does not capture the current session, a specific PR,
a temporary error, or one-off material. The self-check must not interrupt the current task; when no evolution is
needed, stay silent and reply normally without mentioning the self-check, no evolution needed, or internal judgment.

### Decision Scenarios

#### Consider Evolving

- The user clearly corrects, supplements, or defines a future workflow, confirmation gate, quality standard,
  tool-use method, or delivery habit for similar tasks, and it belongs to the used Skill's responsibility scope.
- A loaded or used Skill is missing required steps, prechecks, validation, fallback, or troubleshooting guidance.
- A command, path, configuration, external knowledge, or workflow in the Skill is wrong, outdated, or incomplete.
- While using the Skill, the agent hit an uncovered failure mode, edge case, platform difference,
  permission/environment precondition, tool call failure, or code execution failure.
- A tool call error, code execution failure, or environment mismatch shows that the Skill workflow is outdated,
  or missing preconditions, fallback, or troubleshooting guidance.
- Successful code execution produced a reusable script, validation logic, or handling flow that belongs to
  the Skill's responsibility scope.

#### Do Not Evolve

- This task did not use, and cannot be clearly attributed to, an existing Skill.
- The user is confirming creation of a new Skill, team skill, or new capability capture; that is Skill creation,
  not Skill evolution.
- The content only applies to the current session, a specific PR, a specific error string, a temporary feature,
  one-off context, or current-task parameter.
- The Skill already clearly covers the rule and this run simply failed to follow it; correct execution instead
  of adding a new experience.
- A temporary tool call error, code execution failure, permission issue, network issue, or third-party service
  failure occurred, but it cannot be abstracted into a Skill workflow, environment precondition, fallback,
  or troubleshooting update.

### User Intent Signals

- Long-term workflow: `For future xxx tasks, check yyy first.`
- Confirmation gate: `Before handling xxx in the future, ask me to confirm yyy first.`
- Quality standard: `For later xxx tasks, use this judgment standard / output format.`
- Troubleshooting path: `When xxx failed just now, yyy fixed it; troubleshoot similar issues this way in the future.`

### Reply And Confirmation Rules

#### Final Reply

Only append an evolution question to the end of a normal final reply when there is clear reusable update value
attributable to an existing Skill and existing Skill/experience content does not already cover it. Append at most
two short sentences: one sentence stating the reusable workflow or troubleshooting lesson found, and one sentence
asking whether to start Skill evolution.
Do not recap the task result, artifact content, full execution steps, a long evidence list, or your reasoning
process. When evolution is not appropriate, do not bother the user.

#### User Confirmation

Ask through normal reply text only. Do not evolve automatically, and do not use popup, approval, interrupt,
`ask_user`, or any interactive confirmation tool. If the previous normal reply explicitly asked whether to start
Skill evolution, treat later affirmative replies such as "yes", "start evolution", "need it", "please do",
"go ahead", "okay", "confirm", or "do it" as confirmation to evolve. Treat replies such as "skip", "no",
"not now", or "don't" as refusal. Ask a normal text clarification only when the reply is unclear, the target Skill
is unclear, or the intent conflicts.

#### Tool Execution

After the user confirms evolution, use top-level subject objects for all evolution tools, for example
`{"kind":"skill","name":"..."}`. First call `prepare_skill_evolution(user_confirmed=true, user_intent=...)`,
then call `evolve_review_task(evolution_review_ref=...)`, then read selection from
`evolve_review_task.data.output.proposal_selection_for_submission` and call `evolve_skill_experiences`.
Do not edit `SKILL.md`, `evolutions.json`, or `evolution/*.md` directly; submitting evolution changes may require
user approval.
"""

EVOLUTION_PROTOCOL_PROMPT = {
    "cn": EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": EVOLUTION_PROTOCOL_PROMPT_EN,
}

TEAM_EVOLUTION_PROTOCOL_PROMPT_CN = """## 团队 Skill 演进自检

Swarm Skill evolution 只更新已使用或可明确归因的现有 Team/Swarm Skill，沉淀未来同类团队任务可复用的协作协议、角色分工、交接方式、共享上下文、结果汇总或验收流程。
它不创建新团队技能，不沉淀单个成员的个人工具、代码、调研或调试经验，也不沉淀一次性团队安排。
自检不能打断当前团队任务；不需要演进时保持静默并正常回复，不提及自检、无需演进或内部判断。

### 判断场景

#### 应考虑演进

- 用户明确纠正团队协作、角色分工、handoff、确认门禁、交付流程或验收标准，且该规则属于已使用 Team/Swarm Skill 的职责范围。
- 本轮加载或使用过的 Team/Swarm Skill 缺少团队任务拆解、成员路由、交接同步、共享上下文、汇总整合或质量验收规则。
- 团队执行暴露了角色职责不清、共享状态假设错误、成员执行记录不一致、重复工作或返工问题，且可抽象为团队协作协议更新。
- tool_call 错误、代码执行失败或环境不匹配体现可复用团队协议、角色协作、共享上下文、交接校验或结果验收问题。
- 需要区分 role-local change 与 whole-swarm change；只影响某个成员角色时写入角色局部约束，影响团队协作协议时才写入整体 Swarm Skill。

#### 不应演进

- 本次团队任务没有使用，也无法明确归因到某个现有 Team/Swarm Skill。
- 用户是在确认创建团队技能或创建 Team/Swarm Skill；这属于 Team/Swarm Skill creation，不是 Swarm Skill evolution。
- 可复用经验主要来自某个成员的个人工具调用、代码执行、调研步骤、调试路径或具体产物写法，而没有形成团队协作方法。
- 内容只适用于当前 session、具体 PR、具体错误字符串、临时 feature、一次性资料或一次性团队安排。
- 只是发生了临时 tool_call 错误、代码执行失败、权限问题、网络问题或第三方服务问题，但无法抽象成团队协议、角色协作、共享上下文、交接校验或结果验收更新。
- 不要仅因当前是团队任务就把普通 Skill 写成 swarm-skill；`subject.kind` 必须来自目标 Skill 定义。

### 用户意图信号

- 团队流程：`以后做 xxx 团队任务时按这次分工推进。`
- 角色分工：`下次 xxx 仍按这种角色 / 成员职责安排。`
- 交接与汇总：`以后 xxx 的交接、汇总和验收沿用这个流程。`
- 反馈分派：`类似 xxx 的用户反馈以后也这样分派给成员处理。`

### 回复与确认规则

#### 最终回复

只有明确存在可归因到现有 Team/Swarm Skill 的可复用团队协作更新价值，且现有 Team/Swarm Skill/experience 没有清晰覆盖时，才在普通最终回复末尾追加演进询问。
最多追加两句：一句简短说明发现的可复用团队协作、交付流程或排障经验，一句询问用户是否发起 Swarm Skill 演进。
不要重新总结任务结果、产物内容、完整团队过程、成员明细、长证据列表或判断过程。不应演进时不要打扰用户。

#### 用户确认

必须通过普通回复文本询问，不要自动演进，不要调用弹窗、审批、中断、`ask_user` 或任何交互式确认工具。
如果上一条普通回复明确询问是否发起 Swarm Skill 演进，用户随后用肯定表达回复，例如“是”“发起演进”“需要”“可以”“好的”“行”“确认”“就这么做”，应泛化理解为确认演进。
用户回复“跳过”“不用”“暂不”“不要”等表达时，视为拒绝。只有回复含义不清、目标 Team/Swarm Skill 不明确或意图互相冲突时，才继续用普通回复文本做最小澄清。

#### 工具执行

用户确认演进后，所有演进工具都必须使用顶层 subject 参数对象，例如 `{"kind":"swarm-skill","name":"..."}`。
先调用 `prepare_skill_evolution(user_confirmed=true, user_intent=...)`，
再使用 `evolve_review_task(evolution_review_ref=...)`，最后从
`evolve_review_task.data.output.proposal_selection_for_submission` 读取 selection 并调用 `evolve_skill_experiences`。
不要直接编辑 `SKILL.md`、`evolutions.json` 或 `evolution/*.md`；提交演进变更时可能需要用户审批。
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT_EN = """## Team Skill Evolution Self-Check

Swarm Skill evolution only updates an existing Team/Swarm Skill that was used or can be clearly attributed to
the team task. It captures collaboration protocols, role splits, handoff, shared context, result synthesis,
or acceptance flow reusable by future similar team tasks. It does not create a new team skill, does not capture
one member's personal tool use, coding, research, or debugging experience, and does not capture one-off team
arrangements. The self-check must not interrupt the current team task; when no evolution is needed, stay silent
and reply normally without mentioning the self-check, no evolution needed, or internal judgment.

### Decision Scenarios

#### Consider Evolving

- The user clearly corrects team coordination, role split, handoff, confirmation gates, delivery flow,
  or acceptance criteria, and it belongs to the used Team/Swarm Skill's responsibility scope.
- A loaded or used Team/Swarm Skill is missing team task decomposition, member routing, handoff
  and synchronization, shared context, aggregation, or quality review rules.
- Team execution exposed unclear responsibilities, wrong shared-state assumptions, mismatch between member work
  records, duplicated work, or rework that can be abstracted into a team protocol update.
- Tool call errors, code execution failures, or environment mismatches reveal reusable team protocol,
  role coordination, shared-context, handoff validation, or result acceptance issues.
- Distinguish role-local change from whole-swarm change: write role-local constraints when only one member role
  is affected; update the whole Swarm Skill only for team protocol changes.

#### Do Not Evolve

- This team task did not use, and cannot be clearly attributed to, an existing Team/Swarm Skill.
- The user is confirming creation of a team skill or Team/Swarm Skill; that is Team/Swarm Skill creation,
  not Swarm Skill evolution.
- The reusable experience mainly comes from one member's personal tool calls, code execution, research steps,
  debugging path, or concrete artifact writing, without forming a team collaboration method.
- The content only applies to the current session, a specific PR, a specific error string, a temporary feature,
  one-off source material, or one-off team arrangement.
- A temporary tool call error, code execution failure, permission issue, network issue, or third-party service
  failure occurred, but it cannot be abstracted into team protocol, role coordination, shared-context,
  handoff validation, or result acceptance updates.
- Do not write a regular Skill as swarm-skill just because this is a team task; `subject.kind` must come from
  the target Skill definition.

### User Intent Signals

- Team process: `For future xxx team tasks, proceed with this role split.`
- Role split: `Next time for xxx, keep this role / member responsibility arrangement.`
- Handoff and synthesis: `For future xxx, reuse this handoff, synthesis, and acceptance flow.`
- Feedback routing: `Handle similar xxx user feedback by routing it to members this way too.`

### Reply And Confirmation Rules

#### Final Reply

Only append an evolution question to the end of a normal final reply when there is clear reusable team
collaboration update value attributable to an existing Team/Swarm Skill and existing Team/Swarm Skill/experience
content does not already cover it. Append at most two short sentences: one sentence stating the reusable team
collaboration, delivery workflow, or troubleshooting lesson found, and one sentence asking whether to start
Swarm Skill evolution.
Do not recap the task result, artifact content, full team process, member details, a long evidence list,
or your reasoning process. When evolution is not appropriate, do not bother the user.

#### User Confirmation

Ask through normal reply text only. Do not evolve automatically, and do not use popup, approval, interrupt,
`ask_user`, or any interactive confirmation tool. If the previous normal reply explicitly asked whether to start
Swarm Skill evolution, treat later affirmative replies such as "yes", "start evolution", "need it", "please do",
"go ahead", "okay", "confirm", or "do it" as confirmation to evolve. Treat replies such as "skip", "no",
"not now", or "don't" as refusal. Ask a normal text clarification only when the reply is unclear,
the target Team/Swarm Skill is unclear, or the intent conflicts.

#### Tool Execution

After the user confirms evolution, use top-level subject objects for all evolution tools, for example
`{"kind":"swarm-skill","name":"..."}`. First call
`prepare_skill_evolution(user_confirmed=true, user_intent=...)`, then call
`evolve_review_task(evolution_review_ref=...)`, then read selection from
`evolve_review_task.data.output.proposal_selection_for_submission` and call `evolve_skill_experiences`.
Do not edit `SKILL.md`, `evolutions.json`, or `evolution/*.md` directly; submitting evolution changes may require
user approval.
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT = {
    "cn": TEAM_EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": TEAM_EVOLUTION_PROTOCOL_PROMPT_EN,
}


def build_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the stable agent-facing evolution protocol section."""
    content = EVOLUTION_PROTOCOL_PROMPT.get(language, EVOLUTION_PROTOCOL_PROMPT_CN)
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
