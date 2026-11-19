# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task completion system prompt section.

Provides the completion-signal guidance injected into the system prompt when
``TaskCompletionRail`` is active.
"""
from __future__ import annotations

from typing import Dict

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# Completion-signal guidance
# ---------------------------------------------------------------------------

_PROMISE_GUIDANCE: Dict[str, str] = {
    "cn": (
        "\n\n## 完成信号\n"
        "任务完全完成后，在回复的最后一行输出 "
        "<promise>{promise}</promise>。\n"
        "在确认任务完成前，不要输出此标签。"
    ),
    "en": (
        "\n\n## Completion Signal\n"
        "When the task is fully completed, output "
        "<promise>{promise}</promise> as the final "
        "line of your response. Do not output this "
        "tag until you are confident the task is "
        "complete."
    ),
}

_COMPLETION_SIGNAL_PRIORITY = 85


def build_completion_signal_section(
        language: str,
        completion_promise: str,
) -> PromptSection:
    """Build the completion-signal prompt section.

    Args:
        language: Prompt language code, usually ``"cn"`` or ``"en"``.
        completion_promise: Token the model must emit inside
            ``<promise>...</promise>`` to signal completion.

    Returns:
        A ``PromptSection`` ready to inject into the system prompt.
    """
    template = _PROMISE_GUIDANCE.get(language, _PROMISE_GUIDANCE["cn"])
    return PromptSection(
        name=SectionName.COMPLETION_SIGNAL,
        content={language: template.format(promise=completion_promise)},
        priority=_COMPLETION_SIGNAL_PRIORITY,
    )


_BUDGET_HINT: Dict[str, str] = {
    "cn": (
        "\n\n## 迭代预算告急\n"
        "预算即将用完，请开始收敛：优先完成当前任务，"
        "停止扩张任务范围、不再委派新任务或开启新方向，"
        "收敛到当前最优产出并准备收尾。"
    ),
    "en": (
        "\n\n## Iteration Budget Warning\n"
        "The budget is nearly used up. Start converging: finish the "
        "current task first; stop expanding scope, delegating new "
        "tasks, or opening new threads of work. Converge on the best "
        "output you already have and prepare to wrap up."
    ),
}

_BUDGET_FINAL: Dict[str, str] = {
    "cn": (
        "\n\n## 最后一轮\n"
        "这是本轮任务的最后一次迭代，必须在本轮输出最终答复；"
        "不完整的结果也远好于没有结果。"
    ),
    "en": (
        "\n\n## Final Round\n"
        "This is the last iteration of this task. You MUST produce "
        "the final answer in this round; a partial result is far "
        "better than none."
    ),
}

_BUDGET_WRAP_UP: Dict[str, str] = {
    "cn": (
        "\n\n## 预算用完，进入收尾\n"
        "这轮的迭代预算已经用完，现在只做最后总结：\n"
        "1. 不能再派活：不要新建团队/成员、不要新建或改派任务、"
        "不要给成员发消息、不要开新的工作方向；\n"
        "2. 所有成员都已经停下。下面这些任务还没做完，已经退回待办清单，"
        "下一轮可以直接接着做：{tasks}；\n"
        "3. 任务板现状：待办 {pending} 个，被前置任务卡住 {blocked} 个，"
        "已完成 {completed} 个。\n"
        "4. 请如实写清三件事：已经做完了什么、哪些没做完"
        "（没做完的不要写成“进行中”）、下一轮怎么接着做。不要重新开工。"
    ),
    "en": (
        "\n\n## Budget Used Up: Wrap-up\n"
        "The iteration budget is used up; only the final summary remains:\n"
        "1. No new work: do not spawn members/teams, create or "
        "reassign tasks, message members, or open new directions;\n"
        "2. All members have stopped. These tasks are unfinished and "
        "have been put back on the to-do list for the next round: "
        "{tasks};\n"
        "3. Task board: {pending} to do, {blocked} blocked by "
        "prerequisites, {completed} done.\n"
        "4. State plainly what is done, what is not (do not call "
        "unfinished work \"in progress\"), and how the next round "
        "should continue. Do not restart work."
    ),
}

_ITERATION_BUDGET_PRIORITY = 86


def build_iteration_budget_section(
        language: str,
        remaining: int,
        max_rounds: int,
        *,
        final_round: bool = False,
        wrap_up: bool = False,
        repooled_tasks: list[str] | None = None,
        task_counts: dict[str, int] | None = None,
) -> PromptSection:
    """Build the iteration-budget soft-hint prompt section.

    Rendered into the system prompt per model call and never persisted
    into message history (section channel, not query rewrite).

    Args:
        language: Prompt language code, usually ``"cn"`` or ``"en"``.
        remaining: Rounds left after the current one.
        max_rounds: Configured round cap.
        final_round: True for the mandatory-wrap-up variant.
        wrap_up: True for the leader's ordered wrap-up round (lists the
            unfinished tasks and forbids expansion).
        repooled_tasks: Task ids returned to the to-do list before the
            wrap-up round.
        task_counts: Optional task-board counts with keys ``pending``,
            ``blocked`` and ``completed``; rendered so the summary matches
            the board instead of calling unfinished work "in progress".

    Returns:
        A ``PromptSection`` ready to inject into the system prompt.
    """
    if wrap_up:
        template = _BUDGET_WRAP_UP.get(language, _BUDGET_WRAP_UP["cn"])
        tasks = ", ".join(repooled_tasks or []) or (
            "无" if language == "cn" else "none"
        )
        counts = task_counts or {}
        text = template.format(
            tasks=tasks,
            pending=int(counts.get("pending", 0)),
            blocked=int(counts.get("blocked", 0)),
            completed=int(counts.get("completed", 0)),
        )
    elif final_round:
        template = _BUDGET_FINAL.get(language, _BUDGET_FINAL["cn"])
        text = template
    else:
        # Fixed two-state text: the section changes only when the agent
        # enters the warning / final stretch, not on every iteration. That
        # keeps the system prompt (and provider prefix cache) stable.
        template = _BUDGET_HINT.get(language, _BUDGET_HINT["cn"])
        text = template
    return PromptSection(
        name=SectionName.ITERATION_BUDGET,
        content={language: text},
        priority=_ITERATION_BUDGET_PRIORITY,
    )


__all__ = [
    "build_completion_signal_section",
    "build_iteration_budget_section",
]
