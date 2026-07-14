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
- 判断演进机会时必须结合近期任务中实际使用或可明确推断的 Skill 内容；
  表面纠正措辞不是充分条件。
- 可复用的执行规则、确认门禁、工具使用方式、质量标准、长期偏好或交付习惯，
  可以作为演进候选；当前任务的一次性事实、临时参数或孤立约束通常不应演进。
- 一次性任务事实、一次性偏好、临时环境/权限/网络问题、已有经验覆盖或低置信度候选，
  应不演进。

### 用户确认询问
- 询问用户时必须明确说“Skill 演进”，推荐句式：
  “这条反馈可以沉淀为以后处理同类任务时的流程经验，是否需要我发起 Skill 演进？”
- 如果上一条 assistant 消息刚询问是否发起 Skill 演进，且当前用户回复表示同意
  （如“发起演进”“可以”“确认”“是的”），且演进目标和意图足够明确，
  当前轮必须立即进入工具流程，不要只回复确认收到。
- 如果用户已确认但演进目标、关联 Skill 或意图仍不明确，只做最小必要澄清；
  澄清完成后必须进入工具流程，不要停留在确认或闲聊回复。

### 工具流程
- 所有演进工具都必须使用顶层 subject 参数对象，例如 {"kind":"skill","name":"..."}。
- 用户同意后，调用 prepare_skill_evolution(user_confirmed=true, user_intent=...)。
- 随后使用 evolve_review_task(evolution_review_ref=...) 和返回的 evolution_review_ref。
- 从 evolve_review_task.data.output.proposal_selection_for_submission 读取 selection，再调用
  evolve_skill_experiences；不要复制或改写 proposal 内容。
- 不要直接编辑 SKILL.md、evolutions.json 或 evolution/*.md；提交演进变更时可能需要用户审批。
"""

EVOLUTION_FUZZY_REVIEW_RULES_CN = """
- 判断用户反馈是否构成 Skill 演进机会时，不要只根据表面措辞触发，
  必须结合近期任务中实际使用或可明确推断的 Skill 内容判断。
- 可作为演进候选的反馈通常满足：
  1. 归属于相关 Skill 的职责范围；
  2. 指向可复用的执行规则、确认门禁、工具使用方式、质量标准、长期偏好或交付习惯；
  3. 现有 Skill 说明或经验没有清晰覆盖。
- 不要询问演进的情况：
  1. 只是当前任务的一次性事实、临时参数或孤立约束；
  2. 无法归因到相关 Skill；
  3. 已被现有 Skill 规则清晰覆盖，只是本次执行没有遵守。
- 对于页数、字数、格式、风格、数量、阈值等具体要求，默认视为当前任务参数；
  但如果用户明确表示这是以后同类任务的默认偏好，或该要求能抽象成相关 Skill
  的通用质量标准，可以作为演进候选。
- 对于“先做 X，确认后再做 Y”这类反馈，不要机械触发；只有当 X/Y 属于
  相关 Skill 在同类任务中反复需要处理的结构确认、风险确认、范围确认、
  输入校验或交付门禁时，才考虑询问演进。
- 弱信号、没有可演进机会或只是具体执行失败时，不要因为出现失败就询问用户演进。
- 如果你需要回复本次自检，只能说本次技能演进自检未发现可演进的 Skill，
  不要使用“候选”等内部术语。
- 有可演进机会时，用一句话确认这条反馈可以沉淀为以后处理同类任务时的流程经验，
  是否需要我发起 Skill 演进？
- 用户确认后才进入工具流程；未确认时不要提交演进变更。

示例：
- “先给我目录/大纲，我确认后你再继续制作。”
  判断：可能是演进候选。理由：调整的是确认门禁和交付流程；仍需检查相关 Skill 是否负责此类交付流程且未覆盖该规则。
- “以后生成周报都控制在 800 字以内。”
  判断：可能是演进候选。理由：用户表达了同类任务的长期偏好；仍需检查是否归属于相关 Skill。
- “这次报告控制在 800 字以内。”
  判断：通常不演进。理由：这是当前任务参数。
- “你刚才没按 Skill 里已有步骤先校验数据。”
  判断：通常不新增经验。理由：已有规则覆盖时应纠正执行；除非现有规则表达不清。
"""

EVOLUTION_FUZZY_REVIEW_PROMPT_CN = f"""
### 模糊演进自检
- 检查近期上下文是否有可复用 Skill 经验线索；这不是处理本次错误的请求。
{EVOLUTION_FUZZY_REVIEW_RULES_CN}
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
- When judging evolution opportunities, combine the feedback with Skill content actually used or clearly inferable
  from recent tasks; surface correction wording is not sufficient.
- Reusable execution rules, confirmation gates, tool-use methods, quality standards, long-term preferences, or
  delivery habits can be evolution candidates; current-task one-off facts, temporary parameters, or isolated
  constraints usually should not be evolved.
- Do not evolve for one-off task facts, one-off preferences, temporary environment/permission/network issues,
  duplicate coverage by existing experiences, or low-confidence signals.

### User Confirmation Prompt
- When asking the user, explicitly mention Skill evolution. Recommended wording:
  "This feedback can be distilled into a workflow lesson for similar future tasks.
  Should I start Skill evolution?"
- If the previous assistant message just asked whether to start Skill evolution and the current user message
  confirms it (for example "start evolution", "yes", "confirmed", or "go ahead"), and the target and intent are
  clear enough, immediately enter the tool flow in this turn; do not only acknowledge the confirmation.
- If the user has confirmed but the evolution target, related Skill, or intent is still unclear, ask only the
  minimum necessary clarification; after that clarification is answered, enter the tool flow instead of stopping
  at another acknowledgement or casual reply.

### Tool Flow
- Use top-level subject objects for all evolution tools, for example {"kind":"skill","name":"..."}.
- After consent, call prepare_skill_evolution(user_confirmed=true, user_intent=...).
- Then use evolve_review_task(evolution_review_ref=...) with the returned evolution_review_ref.
- Read selection from evolve_review_task.data.output.proposal_selection_for_submission,
  then call evolve_skill_experiences; do not copy or rewrite proposal content.
- Do not edit SKILL.md, evolutions.json, or evolution/*.md directly; submitting evolution changes may
  require user approval.
"""

EVOLUTION_FUZZY_REVIEW_RULES_EN = """
- When judging whether user feedback is a Skill evolution opportunity, do not trigger only from surface wording;
  combine it with Skill content actually used or clearly inferable from recent tasks.
- Evolution candidates usually satisfy all of these:
  1. The feedback belongs to a related Skill's responsibility scope;
  2. It points to a reusable execution rule, confirmation gate, tool-use method, quality standard, long-term
     preference, or delivery habit;
  3. Existing Skill instructions or experiences do not clearly cover it.
- Do not ask about evolution when:
  1. The feedback is only a current-task fact, temporary parameter, or isolated constraint;
  2. It cannot be attributed to a related Skill;
  3. It is already clearly covered by existing Skill rules and this run simply failed to follow them.
- For concrete requirements such as page count, word count, format, style, quantity, or thresholds, default to
  treating them as current-task parameters. If the user explicitly says this is a future default for similar tasks,
  or the requirement can be abstracted into a general quality standard for the related Skill, it can be an
  evolution candidate.
- For feedback like "do X first, then do Y after confirmation", do not trigger mechanically. Consider asking about
  evolution only when X/Y is a structure confirmation, risk confirmation, scope confirmation, input validation,
  or delivery gate that the related Skill repeatedly needs in similar tasks.
- For weak clues, no evolution opportunity, or concrete failures alone, do not ask the user to evolve just because
  something failed.
- If you need to respond to this self-check, only say this skill evolution self-check did not find any evolvable
  Skill. Do not use internal terms such as "candidate".
- If there is an evolution opportunity, ask in one sentence: This feedback can be distilled into a workflow lesson
  for similar future tasks. Should I start Skill evolution?
- Enter the tool flow only after user confirmation; do not submit evolution changes before confirmation.

Examples:
- "Give me the table of contents / outline first, and continue only after I confirm."
  Judgment: possible evolution candidate. Reason: it changes a confirmation gate and delivery flow; still check
  whether the related Skill owns this delivery flow and does not already cover the rule.
- "For future weekly reports, keep them within 800 words."
  Judgment: possible evolution candidate. Reason: the user expressed a long-term preference for similar tasks;
  still check whether it belongs to a related Skill.
- "Keep this report within 800 words."
  Judgment: usually do not evolve. Reason: this is a current-task parameter.
- "You just failed to follow the existing Skill step that says to validate data first."
  Judgment: usually do not add a new experience. Reason: when an existing rule covers it, correct execution
  instead, unless the existing rule is unclear.
"""

EVOLUTION_FUZZY_REVIEW_PROMPT_EN = f"""
### Fuzzy Review Check
- Check recent context for reusable Skill lessons; this is not a request to handle the current error.
{EVOLUTION_FUZZY_REVIEW_RULES_EN}
"""

EVOLUTION_PROTOCOL_PROMPT = {
    "cn": EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": EVOLUTION_PROTOCOL_PROMPT_EN,
}

TEAM_EVOLUTION_PROTOCOL_PROMPT_CN = """## 团队 Skill 演进关注点

### 演进范围判断
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

### 用户确认询问
- 询问用户时必须明确说“Swarm Skill 演进”，推荐句式：
  “这条反馈可以沉淀为以后处理同类团队任务时的团队协作/交付流程经验，
  是否需要我发起 Swarm Skill 演进？”
- 如果上一条 assistant 消息刚询问是否发起 Swarm Skill 演进，且当前用户回复表示同意
  （如“发起演进”“可以”“确认”“是的”），且演进目标和团队经验意图足够明确，
  当前轮必须立即进入 prepare_skill_evolution → evolve_review_task → evolve_skill_experiences
  工具流程；不要只回复确认收到。
- 如果用户已确认但关联 Swarm Skill、团队经验范围或意图仍不明确，只做最小必要澄清；
  澄清完成后必须进入 prepare_skill_evolution → evolve_review_task → evolve_skill_experiences
  工具流程，不要停留在确认或闲聊回复。
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT_EN = """## Team Skill Evolution Focus

### Evolution Scope
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

### User Confirmation Prompt
- When asking the user, explicitly mention Swarm Skill evolution. Recommended wording:
  "This feedback can be distilled into a team collaboration or delivery workflow lesson for similar future tasks.
  Should I start Swarm Skill evolution?"
- If the previous assistant message just asked whether to start Swarm Skill evolution and the current user message
  confirms it (for example "start evolution", "yes", "confirmed", or "go ahead"), and the target and team lesson
  intent are clear enough, immediately run prepare_skill_evolution -> evolve_review_task ->
  evolve_skill_experiences in this turn; do not only acknowledge the confirmation.
- If the user has confirmed but the related Swarm Skill, team lesson scope, or intent is still unclear, ask only
  the minimum necessary clarification; after that clarification is answered, run prepare_skill_evolution ->
  evolve_review_task -> evolve_skill_experiences instead of stopping at another acknowledgement or casual reply.
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
