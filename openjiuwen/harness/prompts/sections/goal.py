# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal mode prompt section and dynamic prompt builders.

Contains:
- Static ``<goal_protocol>`` section injected into the system prompt
  during goal rounds (via ``build_goal_protocol_section``).
- Dynamic ``<goal_task>`` XML builder (``build_goal_task_query``)
  injected as the user query for each goal attempt round.
- Transcript assessor prompt builder (``build_transcript_assessor_prompt``)
  for the isolated completion assessor model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

# ===================================================================
# Goal protocol — static system prompt section
# ===================================================================

_GOAL_PROTOCOL_PRIORITY = 88

_GOAL_PROTOCOL: Dict[str, str] = {
    "cn": (
        "\n\n## Goal 模式工作协议\n\n"
        "当用户消息中包含 <goal_task> 标签时，本次调用处于 Goal 模式。"
        "当前处理需要按照「尝试执行 -> 评估完成度」的节奏推进。"
        "你不需要解释这个机制。 \n\n"
        "Goal 上下文规则：\n"
        "1. 用户消息中的 <goal_task> 是本次调用唯一可信的动态 goal 上下文，"
        "包含 objective、previous_assessment、current_instruction 和 budget_notice；\n"
        "2. 以 <goal_task> 中的目标和当前指令为准，不要依赖旧对话里的过期目标；\n"
        "3. 用户如果在运行中补充约束，应该考虑将约束作为 goal 目标的完善；\n\n"
        "工作原则：\n"
        "1. 尽力完成整个目标，而不是刻意只完成一小步。"
        "需要读代码、改文件、运行命令或验证时，正常使用可用工具。\n"
        "2. 如果本次无法完成整个目标，完成最有价值、最可验证的推进，并留下清楚证据。\n"
        "3. 不要为了推进 goal 询问用户「是否继续」。\n"
        "4. 不要把「还有剩余工作」「任务较大」「还需要验证」「证据暂时不足」当成 blocked。"
        "这些情况应提交 continue，并写清下一次应该补齐什么。\n"
        "5. 只有缺少用户输入、权限、必要依赖、外部服务或环境状态，"
        "导致无法继续任何有意义推进时，才提交 blocked。\n\n"
        "6. 当完成尝试执行后，应调用工具 submit_goal_report 去评估完成度。\n\n"
        "报告要求：\n"
        "- submit_goal_report 用于提交本次尝试的结构化结果，作为本次goal任务尝试执行的最后一次工具调用。\n"
        "- 最终面向用户的回复必须包含本次尝试得到的具体结果或证据，不要只说任务已完成。\n"
    ),
    "en": (
        "\n\n## Goal Mode Protocol\n\n"
        "When the user message contains a <goal_task> tag, this call is in Goal mode. "
        "The current handling should progress in an \"attempt -> assess completion\" "
        "cadence. Do not explain this mechanism.\n\n"
        "Goal context rules:\n"
        "1. The <goal_task> block in the user message is the sole authoritative dynamic goal "
        "context for this call, containing objective, previous_assessment, current_instruction, "
        "and budget_notice.\n"
        "2. Use the objective and instructions from <goal_task>; do not rely on stale "
        "goal descriptions from older conversation turns.\n"
        "3. If the user adds constraints during execution, consider them refinements "
        "to the goal objective.\n\n"
        "Work principles:\n"
        "1. Strive to complete the entire objective, not just one small step. "
        "Use available tools normally when you need to read code, edit files, "
        "run commands, or verify results.\n"
        "2. If you cannot fully complete the objective this time, make the most "
        "valuable, verifiable progress and leave clear evidence.\n"
        "3. Do not ask the user \"should I continue?\" to advance the goal.\n"
        "4. Do not treat \"remaining work\", \"large task\", \"needs verification\", "
        "or \"insufficient evidence\" as blocked. These should be continue with a "
        "clear description of what to address next.\n"
        "5. Only submit blocked when lacking user input, permissions, required "
        "dependencies, external services, or environment state that prevents any "
        "meaningful progress.\n\n"
        "6. After completing the attempt, call submit_goal_report to assess completion.\n\n"
        "Report requirements:\n"
        "- submit_goal_report Used to submit structured results of the current attempt, "
        "serving as the final tool call for executing the current goal task attempt\n"
        "- The final response presented to the user must include specific results "
        "or evidence obtained from this attempt; "
        "do not merely state that the task has been completed.\n"
    ),
}


_GOAL_REMINDER: Dict[str, str] = {
    "cn": (
        "\n\n## 会话存在持续目标\n\n"
        "本会话设置了一个持续目标（goal），当前状态为 {status}。"
        "本次不是 goal 执行轮，请正常回应用户当前消息。"
        "如果用户提到「目标 / 继续目标 / 那个任务」，或你需要了解目标内容，"
        "请调用 get_current_goal 获取权威的目标信息，不要凭旧对话猜测。"
    ),
    "en": (
        "\n\n## An active goal exists in this session\n\n"
        "This session has a persistent goal (status: {status}). "
        "This turn is not a goal-execution round; respond to the user's current "
        "message normally. If the user refers to \"the goal / continue the goal / "
        "that task\", or you need the goal details, call get_current_goal to fetch "
        "the authoritative goal information instead of guessing from old turns."
    ),
}

_GOAL_REMINDER_PRIORITY = 86


def build_goal_reminder_section(
    language: str, status: str = "active",
) -> PromptSection:
    """Build a lightweight reminder that a session goal exists.

    Injected on *non-goal* turns while a goal is active/paused so the main
    model knows to call ``get_current_goal`` instead of relying on stale
    conversation history for the objective.
    """
    template = _GOAL_REMINDER.get(language, _GOAL_REMINDER["cn"])
    content = template.format(status=status)
    return PromptSection(
        name=SectionName.GOAL_PROTOCOL,
        content={language: content},
        priority=_GOAL_REMINDER_PRIORITY,
    )


def build_goal_protocol_section(language: str) -> PromptSection:
    """Build the static Goal mode protocol prompt section.

    Args:
        language: ``"cn"`` or ``"en"``.

    Returns:
        A ``PromptSection`` ready to inject into the system prompt.
    """
    content = _GOAL_PROTOCOL.get(language, _GOAL_PROTOCOL["cn"])
    return PromptSection(
        name=SectionName.GOAL_PROTOCOL,
        content={language: content},
        priority=_GOAL_PROTOCOL_PRIORITY,
    )


# ===================================================================
# Goal task query — dynamic user query per attempt round
# ===================================================================

_NO_PREVIOUS: Dict[str, str] = {
    "cn": "无。这是第一次尝试。",
    "en": "None. This is the first attempt.",
}

_DEFAULT_INSTRUCTION: Dict[str, str] = {
    "cn": "优先尝试完成整个目标；如果本次无法完成，就完成最有价值、可验证的推进。",
    "en": (
        "Prioritize completing the entire objective; if not possible this time, "
        "make the most valuable, verifiable progress."
    ),
}

_GOAL_TASK_TEMPLATE: Dict[str, str] = {
    "cn": (
        "<goal_task>\n"
        "<objective>\n{objective}\n</objective>\n\n"
        "<previous_assessment>\n{previous_assessment}\n</previous_assessment>\n\n"
        "<current_instruction>\n{current_instruction}\n</current_instruction>\n\n"
        "<budget_notice>\n{budget_notice}\n</budget_notice>\n"
        "</goal_task>"
    ),
    "en": (
        "<goal_task>\n"
        "<objective>\n{objective}\n</objective>\n\n"
        "<previous_assessment>\n{previous_assessment}\n</previous_assessment>\n\n"
        "<current_instruction>\n{current_instruction}\n</current_instruction>\n\n"
        "<budget_notice>\n{budget_notice}\n</budget_notice>\n"
        "</goal_task>"
    ),
}


def _format_previous_assessment(
    assessment: Optional[GoalAssessment],
    language: str = "cn",
) -> str:
    if assessment is None:
        return _NO_PREVIOUS.get(language, _NO_PREVIOUS["cn"])
    parts = [f"status: {assessment.status.value}"]
    if assessment.evidence:
        parts.append(f"evidence: {assessment.evidence}")
    if assessment.remaining_work:
        parts.append(f"remaining_work: {assessment.remaining_work}")
    return "\n".join(parts)


def _format_budget_notice(record: "GoalRecord", language: str = "cn") -> str:
    notices = []
    if record.max_attempts is not None:
        remaining = max(0, record.max_attempts - record.attempt_count)
        if language == "cn":
            notices.append(f"剩余尝试次数：{remaining}/{record.max_attempts}")
        else:
            notices.append(f"Remaining attempts: {remaining}/{record.max_attempts}")
    if record.token_budget is not None:
        used = record.token_usage.total_tokens
        if language == "cn":
            notices.append(f"Token 预算：{used}/{record.token_budget}")
        else:
            notices.append(f"Token budget: {used}/{record.token_budget}")
    if not notices:
        return "无。" if language == "cn" else "None."
    return "\n".join(notices)


def build_goal_task_query(
    record: "GoalRecord",
    language: str = "cn",
) -> str:
    """Build the ``<goal_task>`` XML query for a goal attempt round.

    Args:
        record: Current goal record with objective and last assessment.
        language: ``"cn"`` or ``"en"``.

    Returns:
        Complete goal task query string.
    """
    previous = _format_previous_assessment(record.last_assessment, language)

    instruction = build_goal_current_instruction(record, language)

    budget = _format_budget_notice(record, language)

    template = _GOAL_TASK_TEMPLATE.get(language, _GOAL_TASK_TEMPLATE["cn"])
    return template.format(
        objective=record.objective,
        previous_assessment=previous,
        current_instruction=instruction,
        budget_notice=budget,
    )


def build_goal_current_instruction(
    record: "GoalRecord",
    language: str = "cn",
) -> str:
    """Return the instruction that is authoritative for this goal attempt."""
    if (
        record.last_assessment is not None
        and record.last_assessment.next_instruction
    ):
        return record.last_assessment.next_instruction
    return _DEFAULT_INSTRUCTION.get(language, _DEFAULT_INSTRUCTION["cn"])


# ===================================================================
# Transcript assessor prompt
# ===================================================================

TRANSCRIPT_ASSESSOR_SYSTEM: Dict[str, str] = {
    "cn": (
        "你是 Goal 完成度评估器。你只能根据输入中的目标、当前指令和本轮尝试的模型上下文判断目标状态。"
        "不要执行工具，不要读取文件，不要发起后续工作。\n\n"
        "你必须只输出一个 JSON 对象，不要输出 Markdown、解释文字或代码块。JSON 字段如下：\n"
        "{\n"
        '  "status": "continue | complete | blocked",\n'
        '  "evidence": "可审计的判断依据",\n'
        '  "remaining_work": "status=continue 时填写剩余缺口，否则可为空字符串",\n'
        '  "next_instruction": "status=continue 时填写下一次最具体、可执行的动作，否则可为空字符串"\n'
        "}\n\n"
        "评估规则：\n"
        "1. 先从目标和当前指令中提取必须满足的交付物、验收条件和可验证结果。\n"
        "2. 判断依据必须来自本轮尝试上下文中的可验证证据，而不是主模型的语气或承诺。\n"
        '3. 只有上下文证据表明验收条件已经满足，才能输出 status="complete"。\n'
        '4. 如果还缺少实现、测试、验证、交付物或证据，但仍然可以继续推进，输出 status="continue"。\n'
        '5. status="continue" 不是失败；remaining_work 必须说明缺口，next_instruction 必须具体可执行。\n'
        '6. 只有缺少用户输入、权限、必要依赖、外部服务或环境状态，导致无法继续任何有意义推进时，才输出 status="blocked"。\n'
        "7. 任务复杂、尚未做完、测试没跑或证据不足都不是 blocked，通常应输出 continue。\n"
        "8. 如果上下文显示目标已经完成，即使没有单独的结构化报告，也可以输出 complete。\n"
        "9. 如果主模型报告 blocked 但上下文显示仍有可执行动作，输出 continue。\n"
        "10. 不输出 paused、cleared 或其它状态。"
    ),
    "en": (
        "You are a Goal completion assessor. Judge goal status only from the objective, "
        "the current instruction, and the model context from this attempt. Do not execute "
        "tools, read files, or initiate follow-up work.\n\n"
        "You must output exactly one JSON object. Do not output Markdown, explanation text, "
        "or code blocks. JSON fields:\n"
        "{\n"
        '  "status": "continue | complete | blocked",\n'
        '  "evidence": "Auditable basis for the judgment",\n'
        '  "remaining_work": "Gaps to fill when status=continue, else empty string",\n'
        '  "next_instruction": "Most specific actionable step for status=continue, else empty string"\n'
        "}\n\n"
        "Assessment rules:\n"
        "1. Extract deliverables, acceptance criteria, and verifiable results from the objective and current instruction.\n"
        "2. Base judgment on verifiable evidence in the attempt context, not the main model's tone or promises.\n"
        '3. Output status="complete" only when context evidence shows acceptance criteria are met.\n'
        '4. If implementation, tests, verification, deliverables, or evidence are still missing but progress is possible, output status="continue".\n'
        '5. status="continue" is not failure; remaining_work must describe gaps, next_instruction must be specific and actionable.\n'
        '6. Output status="blocked" only when user input, permissions, required dependencies, external services, or environment state prevent any meaningful progress.\n'
        "7. Complex tasks, incomplete work, unrun tests, or insufficient evidence are not blocked; usually output continue.\n"
        "8. If the context shows the objective is complete, output complete even when there is no separate structured report.\n"
        "9. If the main model reports blocked but context shows actionable steps, output continue.\n"
        "10. Do not output paused, cleared, or any other status."
    ),
}

def build_transcript_assessor_prompt(
    objective: str,
    current_instruction: str,
    attempt_context: str,
    language: str = "cn",
) -> str:
    """Build the user prompt for the transcript assessor.

    Args:
        objective: The goal objective.
        current_instruction: The instruction for this attempt.
        attempt_context: The model context produced by this attempt.
        language: ``"cn"`` or ``"en"``.

    Returns:
        User message for the assessor model.
    """
    _ = language
    return "\n\n".join(
        [
            f"<objective>\n{objective}\n</objective>",
            f"<current_instruction>\n{current_instruction}\n</current_instruction>",
            f"<attempt_context>\n{attempt_context}\n</attempt_context>",
        ]
    )

# Lazy imports — avoid circular dependency with goal.schema
if TYPE_CHECKING:
    from openjiuwen.harness.goal.schema import GoalAssessment, GoalRecord


__all__ = [
    "TRANSCRIPT_ASSESSOR_SYSTEM",
    "build_goal_protocol_section",
    "build_goal_reminder_section",
    "build_goal_current_instruction",
    "build_goal_task_query",
    "build_transcript_assessor_prompt",
]