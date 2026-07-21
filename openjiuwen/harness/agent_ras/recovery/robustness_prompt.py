# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Robustness prompt templates and recovery/steering message rendering."""
from __future__ import annotations

from typing import Any

from openjiuwen.harness.agent_ras.models import Anomaly, AnomalyKind
from openjiuwen.harness.agent_ras.recovery.state import PendingRecovery

# --- robustness_prompts_cn ---

robustness_prompts_cn: dict[str, str] = {
    "llm_loop_literal_low": (
        "检测到 LLM 输出末尾的严格周期重复 {repeats} 次。"
    ),
    "llm_loop_progressive_medium": (
        "检测到 LLM 输出中出现 {repeats} 个高度相似的子句，疑似模板循环。"
    ),
    "llm_loop_literal_high": (
        "检测到 LLM 输出长时间循环（{repeats} 次），"
        "请立即更换策略或结束任务。"
    ),
    "llm_loop_global_critical": (
        "全局 LLM 循环：增量输出已超过阈值且无进展，强制中断。"
    ),
    "llm_dead_loop_agent_high": (
        "LLM 思考流可能陷入语义死循环（重复 {repeats} 次），请更换思路。"
    ),
    "llm_dead_loop_agent_critical": (
        "LLM 思考流被判定为语义死循环，已自动中断并执行恢复。"
    ),
    "repeat_tool_generic_warning": (
        "工具 {tool_name} 已重复调用 {count} 次，请检查是否有效。"
    ),
    "repeat_tool_generic_user": (
        "[工具调用异常] 工具 {tool_name} 已重复调用 {count} 次。"
    ),
    "repeat_tool_generic_steering": (
        "你已多次使用完全相同参数重复调用同一工具，且未取得进展。\n"
        "重复工具调用检测结果：\n"
        "- 工具：{tool_name}\n"
        "- 重复次数：{count}\n"
        "- 参数：{tool_arguments}\n"
        "上述重复调用未产生有效进展。请勿再次使用完全相同的工具与参数进行调用。\n"
        "请分析导致工具重复调用的原因，并采取应对方案：立刻停止工具重复调用，"
        "调整参数、更换工具/策略，或在已有证据充分时结束任务。"
    ),
    "repeat_tool_pingpong_warning": (
        "检测到 {tool_name} 与另一工具的交替循环（{count} 轮），"
        "请合并步骤或更换策略。"
    ),
    "repeat_tool_pingpong_user": (
        "[工具调用异常] Ping-Pong 交替调用已持续 {count} 轮。"
    ),
    "repeat_tool_pingpong_steering": (
        "检测到 Ping-Pong 交替工具调用且未取得进展。\n"
        "- 交替轮次：{count}\n"
        "- 最新工具：{tool_name}\n"
        "请停止 A↔B 工具循环。合并步骤、更换策略或换路径；"
        "若已有证据充分，请结束任务。"
    ),
    "repeat_tool_unknown_tool_warning": (
        "未知工具 {tool_name} 已连续失败 {count} 次，请停止重试。"
    ),
    "repeat_tool_unknown_user": (
        "[工具调用异常] 工具 {tool_name} 已连续失败 {count} 次。"
    ),
    "repeat_tool_unknown_steering": (
        "未知或失败的工具已被连续多次调用，且未取得进展。\n"
        "重复工具调用检测结果：\n"
        "- 工具：{tool_name}\n"
        "- 重复次数：{count}\n"
        "- 参数：{tool_arguments}\n"
        "上述重复调用未产生有效进展。请勿再次使用完全相同的工具与参数进行调用。\n"
        "请分析导致工具重复调用的原因，并采取应对方案：立刻停止工具重复调用，"
        "调整参数、更换工具/策略，或在已有证据充分时结束任务。"
    ),
    "repeat_tool_global_breaker_critical": (
        "全局断路器: {tool_name} 连续 {count} 次无进展"
    ),
    "repeat_tool_pingpong_critical": (
        "Ping-Pong 循环: {count} 轮交替无进展，阻断"
    ),
    "repeat_tool_unknown_tool_critical": (
        "未知工具 {tool_name} 连续调用 {count} 次，停止重试"
    ),
    "thinking_loop_lock_judge_intro": (
        "检测到思考/输出陷入重复循环（检测模式={mode}，重复 **{count}** 次）。"
        "请阅读下方重复片段并判断是否属于正常表达："
    ),
    "thinking_loop_lock_repeat_block_header": "问题输出原文（自重复起点起）",
    "thinking_loop_lock_judge_question": "上述重复内容是否正常？",
    "thinking_loop_lock_judge_yes": "是，正常继续",
    "thinking_loop_lock_judge_yes_desc": "重复符合预期，按当前思路继续",
    "thinking_loop_lock_judge_no": "否，需要恢复",
    "thinking_loop_lock_judge_no_desc": "重复异常，触发恢复策略",
    "thinking_loop_lock_steering_recovery": (
        "[思考循环锁定] 系统判定重复内容异常（模式={mode}，重复{count}次）。\n"
        "请严格按以下顺序执行：\n"
        "1. 立刻停止：停止复读与沿旧路径继续生成，勿沿用刚才导致卡住的措辞与思路。\n"
        "2. 执行策略：更换全新切入点，或基于已有信息直接给出最简下一步/结论；"
        "禁止再铺垫与中间空转。\n"
        "3. 再评估是否继续任务：若请求实质是测试/压测/故意诱导循环，立刻停止且勿继续生成；"
        "否则按新策略推进。"
    ),
    "thinking_loop_lock_steering_continue_normal": (
        "[思考循环锁定-继续] 用户确认刚才的输出属于正常表达。"
        "请从中断处继续原任务，不要再次询问用户是否正常，也不要复述上述判定过程。"
    ),
    "thinking_loop_lock_no_user_input": "思考循环锁定：请选择「是」或「否」。",
    "thinking_loop_lock_ask_failed": "思考循环锁定：无法获取用户判断（{detail}）。",
    "plan_exec_judge_intro": (
        "检测到思考过程可能陷入混乱或重复循环（LLM 语义判定异常）。"
        "请阅读下方思考片段并判断是否属于正常表达："
    ),
    "plan_exec_thinking_block_header": "思考过程片段（待判定）",
    "plan_exec_judge_question": "上述思考内容是否正常？",
    "plan_exec_steering_recovery": (
        "[思考循环锁定] 系统判定思考内容异常。\n"
        "请严格按以下顺序执行：\n"
        "1. 立刻停止：停止沿当前路径继续生成，勿沿用刚才导致卡住的措辞与思路。\n"
        "2. 执行策略：更换全新切入点，或基于已有信息直接给出最简下一步/结论；"
        "禁止再铺垫与中间空转。\n"
        "3. 再评估是否继续任务：若请求实质是测试/压测/故意诱导循环，立刻停止且勿继续生成；"
        "否则按新策略推进。"
    ),
    "plan_exec_semantic_deadlock_judge_intro": (
        "检测到思考过程可能在同一组条件/对象上反复权衡、结论无法前进（**语义死锁**）。"
        "请阅读下方思考片段并判断是否属于正常表达："
    ),
    "plan_exec_semantic_deadlock_steering_recovery": (
        "[思考循环锁定] 系统判定思考内容异常（语义死锁）。\n"
        "请严格按以下顺序执行：\n"
        "1. 立刻停止：停止在同一前提下反复比较与空转权衡。\n"
        "2. 执行策略：明确做出取舍或临时假设并标注；已多次无法得出结论的方向视为死路，"
        "直接给出最简下一步行动或结论。\n"
        "3. 再评估是否继续任务：若请求实质是测试/压测/故意诱导循环，立刻停止且勿继续生成；"
        "否则按新策略推进。"
    ),
    "plan_exec_semantic_deadlock_recovery_user_notice": (
        "检测到思考语义死锁异常，已执行恢复操作"
    ),
    "plan_exec_text_degradation_judge_intro": (
        "检测到思考/输出出现断裂、粘连、乱码或词语/URL 被撕碎拼接（**文本崩坏**）。"
        "请阅读下方片段并判断是否属于正常表达："
    ),
    "plan_exec_text_degradation_steering_recovery": (
        "[思考循环锁定] 系统判定思考内容异常（文本崩坏）。\n"
        "请严格按以下顺序执行：\n"
        "1. 立刻停止：停止继续生成混乱、断裂或碎片化内容。\n"
        "2. 执行策略：用完整、可读句子重写当前要点；信息不足时直接说明缺口，勿拼接碎片。\n"
        "3. 再评估是否继续任务：若请求实质是测试/压测/故意诱导循环，立刻停止且勿继续生成；"
        "否则按新策略推进。"
    ),
    "plan_exec_text_degradation_recovery_user_notice": (
        "检测到思考文本崩坏异常，已执行恢复操作"
    ),
    "plan_exec_overthinking_judge_intro": (
        "检测到思考过程冗长纠结、迟迟不收敛（**过度思考**）。"
        "请阅读下方片段并判断是否属于正常表达："
    ),
    "plan_exec_overthinking_steering_recovery": (
        "[思考循环锁定] 系统判定思考内容异常（过度思考）。\n"
        "请严格按以下顺序执行：\n"
        "1. 立刻停止：停止冗长纠结与重复论证。\n"
        "2. 执行策略：已确认事实与待验证假设各不超过 3 条；跳过铺垫，"
        "直接输出最简下一步或阶段性结论。\n"
        "3. 再评估是否继续任务：若请求实质是测试/压测/故意诱导循环，立刻停止且勿继续生成；"
        "否则按新策略推进。"
    ),
    "plan_exec_overthinking_recovery_user_notice": (
        "检测到过度思考异常，已执行恢复操作"
    ),
    "thinking_loop_recovery_user_notice": (
        "检测到思考循环异常，已执行恢复操作"
    ),
    "plan_exec_loop_recovery_user_notice": (
        "检测到计划执行循环异常，已执行恢复操作"
    ),
    "generic_recovery_user_notice": (
        "检测到 {kind_label} 异常，已执行恢复操作"
    ),
}

# --- robustness_prompts_en ---

robustness_prompts_en: dict[str, str] = {
    "llm_loop_literal_low": (
        "Detected a strict suffix cycle of {repeats} repeats in the LLM output."
    ),
    "llm_loop_progressive_medium": (
        "Detected {repeats} highly similar clauses in the LLM output; possible template loop."
    ),
    "llm_loop_literal_high": (
        "Detected a sustained literal loop in the LLM output "
        "({repeats} repeats). Change strategy or end the task."
    ),
    "llm_loop_global_critical": (
        "Global LLM loop: incremental output exceeded the threshold with no progress; terminating."
    ),
    "llm_dead_loop_agent_high": (
        "The LLM reasoning stream may be in a semantic dead loop "
        "({repeats} repeats). Try a new angle."
    ),
    "llm_dead_loop_agent_critical": (
        "The LLM reasoning stream was judged a semantic dead loop; "
        "stream aborted and automatic recovery applied."
    ),
    "repeat_tool_generic_warning": (
        "Tool {tool_name} has been repeated {count} times; please verify it is effective."
    ),
    "repeat_tool_generic_user": (
        "[Tool Call Anomaly] Tool {tool_name} has been repeated {count} times."
    ),
    "repeat_tool_generic_steering": (
        "You have repeatedly called the same tool with identical parameters many times.\n"
        "Repeated tool call detected:\n"
        "- tool: {tool_name}\n"
        "- repeated_times: {count}\n"
        "- arguments: {tool_arguments}\n"
        "The previous repeated calls did not make progress. "
        "Do not call this exact same tool with the exact same arguments again.\n"
        "Analyze why the tool is being called repeatedly and take corrective action: "
        "stop repeating the same tool call immediately, adjust parameters, switch tools/strategy, "
        "or finish the task if enough evidence has been gathered."
    ),
    "repeat_tool_pingpong_warning": (
        "Detected ping-pong alternation between {tool_name} and another tool "
        "({count} rounds). Merge steps or change strategy."
    ),
    "repeat_tool_pingpong_user": (
        "[Tool Call Anomaly] Ping-pong alternating calls have continued for {count} rounds."
    ),
    "repeat_tool_pingpong_steering": (
        "Ping-pong alternating tool calls detected with no progress.\n"
        "- rounds: {count}\n"
        "- latest tool: {tool_name}\n"
        "Stop the A↔B tool loop. Merge steps, change approach, "
        "or finish the task if enough evidence has been gathered."
    ),
    "repeat_tool_unknown_tool_warning": (
        "Unknown tool {tool_name} has failed {count} times in a row; please stop retrying."
    ),
    "repeat_tool_unknown_user": (
        "[Tool Call Anomaly] Tool {tool_name} has failed {count} times in a row."
    ),
    "repeat_tool_unknown_steering": (
        "An unknown or failing tool has been called repeatedly with no progress.\n"
        "Repeated tool call detected:\n"
        "- tool: {tool_name}\n"
        "- repeated_times: {count}\n"
        "- arguments: {tool_arguments}\n"
        "The previous repeated calls did not make progress. "
        "Do not call this exact same tool with the exact same arguments again.\n"
        "Analyze why the tool is being called repeatedly and take corrective action: "
        "stop repeating the same tool call immediately, adjust parameters, switch tools/strategy, "
        "or finish the task if enough evidence has been gathered."
    ),
    "repeat_tool_global_breaker_critical": (
        "Circuit breaker: {tool_name} made no progress for {count} consecutive calls"
    ),
    "repeat_tool_pingpong_critical": (
        "Ping-pong loop: {count} alternating calls with no progress, blocked"
    ),
    "repeat_tool_unknown_tool_critical": (
        "Unknown tool {tool_name} called {count} times in a row, stopping retries"
    ),
    "thinking_loop_lock_judge_intro": (
        "Thinking/output stuck in a repeat loop (mode={mode}, **{count}** repeats). "
        "Review the repeated excerpt below and judge whether it is expected:"
    ),
    "thinking_loop_lock_repeat_block_header": "Original output (from repeat start)",
    "thinking_loop_lock_judge_question": "Is this repetition normal?",
    "thinking_loop_lock_judge_yes": "Yes, continue",
    "thinking_loop_lock_judge_yes_desc": "Expected repetition; continue as-is",
    "thinking_loop_lock_judge_no": "No, recover",
    "thinking_loop_lock_judge_no_desc": "Abnormal repetition; apply recovery",
    "thinking_loop_lock_steering_recovery": (
        "[Thinking Loop Lock] System judged repetition abnormal "
        "(mode={mode}, count={count}).\n"
        "Follow these steps in order:\n"
        "1. Stop immediately: stop repeating and stop continuing along the old path; "
        "do not reuse the wording or reasoning that just caused you to get stuck.\n"
        "2. Execute strategy: switch to a fresh angle, or give the most concise next "
        "step/conclusion from what you already know; no more setup or idle churn.\n"
        "3. Re-evaluate whether to continue: if the request is essentially a "
        "test/stress/adversarial loop-inducing task, stop and do not continue "
        "generating; otherwise proceed with the new strategy."
    ),
    "thinking_loop_lock_steering_continue_normal": (
        "[Thinking Loop Lock Continue] The user confirmed the recent output "
        "is expected/normal. Resume the original task from the interruption "
        "point. Do not ask again whether it is normal, and do not restate "
        "this confirmation."
    ),
    "thinking_loop_lock_no_user_input": (
        "Thinking loop lock: please choose Yes or No."
    ),
    "thinking_loop_lock_ask_failed": (
        "Thinking loop lock: could not obtain user judgment ({detail})."
    ),
    "plan_exec_judge_intro": (
        "Thinking may be confused or stuck in a loop (LLM semantic judge: abnormal). "
        "Review the excerpt below and judge whether it is expected:"
    ),
    "plan_exec_thinking_block_header": "Reasoning excerpt (for review)",
    "plan_exec_judge_question": "Is this reasoning normal?",
    "plan_exec_steering_recovery": (
        "[Thinking Loop Lock] System judged reasoning abnormal.\n"
        "Follow these steps in order:\n"
        "1. Stop immediately: stop continuing along the current path; do not reuse "
        "the wording or reasoning that just caused you to get stuck.\n"
        "2. Execute strategy: switch to a fresh angle, or give the most concise next "
        "step/conclusion from what you already know; no more setup or idle churn.\n"
        "3. Re-evaluate whether to continue: if the request is essentially a "
        "test/stress/adversarial loop-inducing task, stop and do not continue "
        "generating; otherwise proceed with the new strategy."
    ),
    "plan_exec_semantic_deadlock_judge_intro": (
        "Thinking may be stuck weighing the same objects/conditions without "
        "progress (**semantic deadlock**). Review the excerpt below and judge "
        "whether it is expected:"
    ),
    "plan_exec_semantic_deadlock_steering_recovery": (
        "[Thinking Loop Lock] System judged reasoning abnormal (semantic deadlock).\n"
        "Follow these steps in order:\n"
        "1. Stop immediately: stop comparing under the same premises and idle "
        "weighing loops.\n"
        "2. Execute strategy: make a clear trade-off or state a temporary "
        "assumption; treat repeatedly dead-end directions as closed, and output "
        "only the most concise next action or conclusion.\n"
        "3. Re-evaluate whether to continue: if the request is essentially a "
        "test/stress/adversarial loop-inducing task, stop and do not continue "
        "generating; otherwise proceed with the new strategy."
    ),
    "plan_exec_semantic_deadlock_recovery_user_notice": (
        "Detected a semantic deadlock anomaly; recovery has been applied"
    ),
    "plan_exec_text_degradation_judge_intro": (
        "Thinking/output may show breaks, garbling, or shredded URL/word "
        "fragments (**text degradation**). Review the excerpt below and judge "
        "whether it is expected:"
    ),
    "plan_exec_text_degradation_steering_recovery": (
        "[Thinking Loop Lock] System judged reasoning abnormal (text degradation).\n"
        "Follow these steps in order:\n"
        "1. Stop immediately: stop generating garbled, broken, or fragmented text.\n"
        "2. Execute strategy: rewrite the current point in complete, readable "
        "sentences; if information is missing, state the gap—do not stitch "
        "fragments together.\n"
        "3. Re-evaluate whether to continue: if the request is essentially a "
        "test/stress/adversarial loop-inducing task, stop and do not continue "
        "generating; otherwise proceed with the new strategy."
    ),
    "plan_exec_text_degradation_recovery_user_notice": (
        "Detected a text degradation anomaly; recovery has been applied"
    ),
    "plan_exec_overthinking_judge_intro": (
        "Thinking may be verbose and indecisive without converging "
        "(**overthinking**). Review the excerpt below and judge whether it is "
        "expected:"
    ),
    "plan_exec_overthinking_steering_recovery": (
        "[Thinking Loop Lock] System judged reasoning abnormal (overthinking).\n"
        "Follow these steps in order:\n"
        "1. Stop immediately: stop verbose indecision and repeated argumentation.\n"
        "2. Execute strategy: at most 3 confirmed facts and 3 open hypotheses; "
        "skip setup and output the most concise next step or interim conclusion.\n"
        "3. Re-evaluate whether to continue: if the request is essentially a "
        "test/stress/adversarial loop-inducing task, stop and do not continue "
        "generating; otherwise proceed with the new strategy."
    ),
    "plan_exec_overthinking_recovery_user_notice": (
        "Detected an overthinking anomaly; recovery has been applied"
    ),
    "thinking_loop_recovery_user_notice": (
        "Detected a thinking loop anomaly; recovery has been applied"
    ),
    "plan_exec_loop_recovery_user_notice": (
        "Detected a plan-execution loop anomaly; recovery has been applied"
    ),
    "generic_recovery_user_notice": (
        "Detected a {kind_label} anomaly; recovery has been applied"
    ),
}

# --- message_catalog ---


# Backward-compatible aliases (deprecated: use robustness_prompts_*).
cn_messages = robustness_prompts_cn
en_messages = robustness_prompts_en

_LOCALES: dict[str, dict[str, str]] = {
    "cn": robustness_prompts_cn,
    "zh": robustness_prompts_cn,
    "en": robustness_prompts_en,
}
_DEFAULT_LOCALE = "cn"

_STEERING_BY_EVIDENCE: dict[str, str] = {
    "generic_repeat": "repeat_tool_generic_steering",
    "ping_pong_warning": "repeat_tool_pingpong_steering",
    "unknown_tool_repeat_warning": "repeat_tool_unknown_steering",
}

_USER_WARNING_BY_EVIDENCE: dict[str, str] = {
    "generic_repeat": "repeat_tool_generic_user",
    "ping_pong_warning": "repeat_tool_pingpong_user",
    "unknown_tool_repeat_warning": "repeat_tool_unknown_user",
}

_CRITICAL_BY_EVIDENCE: dict[str, str] = {
    "global_circuit_breaker": "repeat_tool_global_breaker_critical",
    "unknown_tool_repeat": "repeat_tool_unknown_tool_critical",
    "ping_pong_critical": "repeat_tool_pingpong_critical",
}

_GENERIC_STEER_CN = (
    "检测到 {kind} 异常（{summary}），"
    "请尝试调整参数、更换工具/策略，"
    "或在已有证据充分时结束任务。"
)
_GENERIC_STEER_EN = (
    "Detected {kind} anomaly ({summary}). "
    "Please adjust parameters, switch tools/strategy, "
    "or finish the task if enough evidence has been gathered."
)

_PLAN_EXEC_FAULT_KEYS: dict[str, tuple[str, str, str]] = {
    "semantic_deadlock": (
        "plan_exec_semantic_deadlock_judge_intro",
        "plan_exec_semantic_deadlock_steering_recovery",
        "plan_exec_semantic_deadlock_recovery_user_notice",
    ),
    "text_degradation": (
        "plan_exec_text_degradation_judge_intro",
        "plan_exec_text_degradation_steering_recovery",
        "plan_exec_text_degradation_recovery_user_notice",
    ),
    "overthinking": (
        "plan_exec_overthinking_judge_intro",
        "plan_exec_overthinking_steering_recovery",
        "plan_exec_overthinking_recovery_user_notice",
    ),
}

_PLAN_EXEC_DEFAULT_KEYS = (
    "plan_exec_judge_intro",
    "plan_exec_steering_recovery",
    "plan_exec_loop_recovery_user_notice",
)


def _plan_exec_fault(pending: PendingRecovery) -> str | None:
    fault = str(pending.extra.get("primary_fault") or "").strip()
    return fault if fault in _PLAN_EXEC_FAULT_KEYS else None


def _plan_exec_message_keys(pending: PendingRecovery) -> tuple[str, str, str]:
    fault = _plan_exec_fault(pending)
    if fault is not None:
        return _PLAN_EXEC_FAULT_KEYS[fault]
    return _PLAN_EXEC_DEFAULT_KEYS


def _table(locale: str) -> dict[str, str]:
    loc = (locale or "cn").strip().lower()
    if loc == "zh":
        loc = "cn"
    return _LOCALES.get(loc) or _LOCALES[_DEFAULT_LOCALE]


def load_message(locale: str, key: str, **kwargs: Any) -> str:
    """Render a robustness prompt template (public API)."""
    table = _table(locale)
    template = table.get(key)
    if template is None:
        template = _LOCALES[_DEFAULT_LOCALE].get(key)
    if template is None:
        raise KeyError(
            f"prompt key {key!r} not found in locale {locale!r} "
            f"or fallback {_DEFAULT_LOCALE!r}"
        )
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def render(template_key: str, locale: str = "cn", **kwargs: Any) -> str:
    template = _table(locale).get(template_key) or template_key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


_STEERING_OPEN = "<system-reminder>"
_STEERING_CLOSE = "</system-reminder>"


def format_steering(body: str) -> str:
    """Wrap steering body in the standard ``<system-reminder>`` envelope.

    Idempotent: already-wrapped text is returned unchanged (aside from strip).
    Templates should store body only; callers should prefer this helper (or
    ``inject_steering``, which applies it by default).
    """
    text = (body or "").strip()
    if not text:
        return text
    if text.startswith(_STEERING_OPEN) and text.endswith(_STEERING_CLOSE):
        return text
    inner = text
    if inner.startswith(_STEERING_OPEN):
        inner = inner[len(_STEERING_OPEN):].lstrip("\n")
    if inner.endswith(_STEERING_CLOSE):
        inner = inner[: -len(_STEERING_CLOSE)].rstrip()
    return f"{_STEERING_OPEN}\n{inner}\n{_STEERING_CLOSE}"


def _repeat_tool_fields(anomaly: Anomaly) -> dict[str, Any]:
    evidence = anomaly.evidence or {}
    return {
        "tool_name": anomaly.member_name,
        "count": int(evidence.get("count") or 0),
        "tool_arguments": str(evidence.get("tool_arguments") or ""),
    }


def steer_text_for(anomaly: Anomaly, locale: str = "cn") -> str | None:
    evidence = anomaly.evidence or {}
    msg_key = evidence.get("msg_key") or ""
    template_key = _STEERING_BY_EVIDENCE.get(msg_key)
    if not template_key:
        return None
    return format_steering(
        render(template_key, locale, **_repeat_tool_fields(anomaly))
    )


def user_warning_text_for(anomaly: Anomaly, locale: str = "cn") -> str | None:
    evidence = anomaly.evidence or {}
    msg_key = evidence.get("msg_key") or ""
    template_key = _USER_WARNING_BY_EVIDENCE.get(msg_key)
    if not template_key:
        return None
    fields = _repeat_tool_fields(anomaly)
    fields.pop("tool_arguments", None)
    return render(template_key, locale, **fields)


def user_notice_text_for(anomaly: Anomaly, locale: str = "cn") -> str | None:
    """Short user-facing notice for any anomaly (detector-specific when available)."""
    specific = user_warning_text_for(anomaly, locale=locale)
    if specific:
        return specific
    return None


_KIND_LABELS: dict[str, dict[AnomalyKind, str]] = {
    "cn": {
        AnomalyKind.LLM_THINKING_LOOP: "思考循环",
        AnomalyKind.LLM_THINKING_DEAD_LOOP: "思考死循环",
        AnomalyKind.REPEAT_TOOL_CALL: "工具重复调用",
        AnomalyKind.TOOL_CALL_LOOP: "工具调用循环",
    },
    "en": {
        AnomalyKind.LLM_THINKING_LOOP: "thinking loop",
        AnomalyKind.LLM_THINKING_DEAD_LOOP: "thinking dead loop",
        AnomalyKind.REPEAT_TOOL_CALL: "repeat tool call",
        AnomalyKind.TOOL_CALL_LOOP: "tool call loop",
    },
}


def _kind_label_for(anomaly: Anomaly, locale: str) -> str:
    loc = (locale or "cn").strip().lower()
    if loc == "zh":
        loc = "cn"
    labels = _KIND_LABELS.get(loc) or _KIND_LABELS["cn"]
    return labels.get(anomaly.kind, anomaly.kind.value)


def recovery_user_notice_for(pending: PendingRecovery, locale: str = "cn") -> str:
    """User-facing notice after automatic recovery is applied."""
    profile = pending.recovery_profile
    if profile == "thinking_loop_plan_exec" or pending.source == "plan_execution_loop_lock":
        _, _, notice_key = _plan_exec_message_keys(pending)
        return render(notice_key, locale)
    if profile == "thinking_loop_text_rep" or pending.source == "text_repetition":
        return render("thinking_loop_recovery_user_notice", locale)
    return render("thinking_loop_recovery_user_notice", locale)


def recovery_user_notice_for_anomaly(anomaly: Anomaly, locale: str = "cn") -> str:
    """Recovery notice from an anomaly (immediate apply path)."""
    pending = PendingRecovery.from_anomaly(anomaly)
    if pending.recovery_profile or pending.source in (
        "plan_execution_loop_lock",
        "text_repetition",
    ):
        return recovery_user_notice_for(pending, locale=locale)
    return render(
        "generic_recovery_user_notice",
        locale,
        kind_label=_kind_label_for(anomaly, locale),
    )


def critical_text_for(anomaly: Anomaly, locale: str = "cn") -> str:
    evidence = anomaly.evidence or {}
    msg_key = evidence.get("msg_key") or ""
    template_key = _CRITICAL_BY_EVIDENCE.get(msg_key, msg_key)
    return render(template_key, locale, **_repeat_tool_fields(anomaly))


def generic_steer_text_for(anomaly: Anomaly, locale: str = "cn") -> str:
    template = _GENERIC_STEER_EN if locale == "en" else _GENERIC_STEER_CN
    try:
        body = template.format(kind=anomaly.kind.value, summary=anomaly.summary)
    except (KeyError, IndexError):
        body = template
    return format_steering(body)


def recovery_steering_on_abnormal(pending: PendingRecovery, locale: str = "cn") -> str:
    """Steering injected after automatic recovery confirms an abnormal thinking loop."""
    profile = pending.recovery_profile
    if profile == "thinking_loop_plan_exec" or pending.source == "plan_execution_loop_lock":
        _, steering_key, _ = _plan_exec_message_keys(pending)
        return format_steering(render(steering_key, locale))
    return format_steering(
        render(
            "thinking_loop_lock_steering_recovery",
            locale,
            mode=pending.mode,
            count=pending.count,
        )
    )


def format_repeat_display(scanned_text: str, start_pos: int, max_len: int = 1200) -> str:
    if not scanned_text:
        return ""
    text = scanned_text[start_pos:] if start_pos > 0 else scanned_text
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def extract_repeat_unit(scanned_text: str, start_pos: int, max_unit: int = 200) -> str:
    unit = format_repeat_display(scanned_text, start_pos, max_len=max_unit)
    return unit or "(无法提取重复片段)"
