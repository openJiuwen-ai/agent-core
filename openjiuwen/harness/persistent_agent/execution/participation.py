# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lightweight group-participation judgment before any window is loaded."""

import json
import re
from dataclasses import dataclass
from uuid import uuid4

from openjiuwen.core.common.logging import logger

from ..schema.context import GroupMessagesResult
from ..schema.events import EventType
from ..schema.port_results import BudgetAdmission, BudgetReport
from ..schema.protocol import ModelUsage

_ATTEMPTS = 3
_RECENT_LIMIT = 8
_DETAIL_LIMIT = 4096
_DECISION = re.compile(r"\{.*\}", re.DOTALL)
_INSTRUCTION = (
    "你负责判断是否参与当前群消息。只输出一个 JSON 对象，不要调用工具，不要起草回复。"
    '格式：{"decision":"ignore|respond|intervene","reason":"原因"}。'
    "ignore 表示不参与，respond 表示回应，intervene 表示主动插话。"
    "被提及是强信号，仍可以忽略。未被提及仍可以回应或插话。"
)


@dataclass(frozen=True)
class ParticipationOutcome:
    """Whether judgment ended the Request, and the decision event when execution continues."""

    terminal: bool
    event_id: str | None = None


def _clip(text: object) -> str:
    cleaned = " ".join(str(text).split())
    return (cleaned or "participation judgment failed")[:_DETAIL_LIMIT]


def _duty(spec) -> str:
    prompt = getattr(spec, "system_prompt", None)
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    card = getattr(spec, "card", None)
    name = getattr(card, "name", None) or getattr(card, "id", None)
    description = getattr(card, "description", None) or ""
    return f"{name}\n{description}".strip()


def _content(response) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _parse(text: str) -> tuple[str, str]:
    match = _DECISION.search(text)
    if match is None:
        raise ValueError("participation output is not JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("participation output is not an object")
    decision = data.get("decision")
    reason = data.get("reason")
    if decision not in ("ignore", "respond", "intervene"):
        raise ValueError("participation decision is missing")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("participation reason is missing")
    return decision, _clip(reason)


def _usage(response) -> ModelUsage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None or metadata.input_tokens is None or metadata.output_tokens is None:
        return ModelUsage(status="unknown", reason="model returned no usage")
    return ModelUsage(status="known", input_tokens=metadata.input_tokens, output_tokens=metadata.output_tokens)


def _current_block(source, agent_id: str) -> str:
    mentioned = "是" if agent_id in source.mention_ids else "否"
    attachments = ", ".join(source.attachment_refs) or "无"
    reply = source.reply_to or "无"
    body = source.text if source.text else "（无正文）"
    return (
        f"发送者: {source.sender_name} ({source.sender_id}, {source.sender_kind})\n"
        f"被提及: {mentioned}\n"
        f"reply_to: {reply}\n"
        f"附件: {attachments}\n"
        f"正文: {body}"
    )


def _recent_block(messages) -> str:
    if not messages:
        return "无"
    lines = []
    for message in messages:
        # An unreadable body is not a recall; telling them apart keeps the judgment on true facts.
        if message.status == "recalled":
            body = "已撤回"
        elif message.status == "unavailable":
            body = "正文暂缺"
        else:
            body = message.text if message.text else "（无正文）"
        label = "排队背景" if message.queued_request else "群消息"
        lines.append(f"- {label} {message.sender_name}: {body}")
    return "\n".join(lines)


async def _recent(port, execution, trigger_id: str):
    raw = await port.recent_messages(execution, None, _RECENT_LIMIT)
    result = GroupMessagesResult.model_validate(raw)
    result.assert_matches(execution)
    if result.status == "incomplete":
        raise RuntimeError(result.reason or "group history incomplete")
    return tuple(message for message in result.messages if message.message_id != trigger_id)


async def _report(budget, execution, model_call_id: str, usage: ModelUsage) -> None:
    try:
        report = BudgetReport.model_validate(await budget.report(execution, model_call_id, usage))
        report.assert_call(execution, model_call_id)
    except Exception as exc:
        logger.warning(
            "[PersistentAgent] participation budget report failed: execution=%s call=%s error=%s",
            execution.execution_id,
            model_call_id,
            exc,
        )


async def _finish_failed(emitter, reason: str) -> ParticipationOutcome:
    await emitter.emit_ending(EventType.EXECUTION_FINAL, {"status": "failed", "reason": _clip(reason)})
    return ParticipationOutcome(terminal=True)


async def judge_participation(spec, context, request, emitter) -> ParticipationOutcome:
    """Decide ignore, respond, or intervene. Ignore and failure end before the native agent is built."""
    execution = context.execution
    if getattr(spec, "model", None) is None:
        return await _finish_failed(emitter, "participation model unavailable")
    try:
        model = spec.model.build()
    except Exception as exc:
        return await _finish_failed(emitter, f"participation model unavailable: {exc}")

    budget = context.ports.budget
    last_error = "participation judgment failed"
    for _ in range(_ATTEMPTS):
        try:
            recent = await _recent(context.ports.group_chat, execution, request.source.message_id)
        except Exception as exc:
            last_error = f"group history unavailable: {exc}"
            continue
        model_call_id = "mc_" + uuid4().hex
        try:
            admission = BudgetAdmission.model_validate(await budget.admit(execution, model_call_id))
            admission.assert_call(execution, model_call_id)
        except Exception as exc:
            await emitter.emit_ending(
                EventType.EXECUTION_PAUSED,
                {"reason": "error", "detail": _clip(f"budget admission failed: {exc}")},
            )
            return ParticipationOutcome(terminal=True)
        if admission.status == "denied":
            await emitter.emit_ending(
                EventType.EXECUTION_PAUSED, {"reason": "budget_denied", "detail": admission.reason}
            )
            return ParticipationOutcome(terminal=True)
        try:
            response = await model.invoke(
                [
                    {"role": "system", "content": _INSTRUCTION},
                    {
                        "role": "user",
                        "content": (
                            f"职责:\n{_duty(spec)}\n\n"
                            f"当前消息:\n{_current_block(request.source, execution.agent_id)}\n\n"
                            f"最近群消息:\n{_recent_block(recent)}"
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:
            await _report(budget, execution, model_call_id, ModelUsage(status="unknown", reason="model call failed"))
            await emitter.emit(
                EventType.MODEL_COMPLETED,
                {"model_call_id": model_call_id, "config_version": execution.config_version, "usage": _unknown_usage()},
            )
            last_error = f"participation judgment failed: {exc}"
            continue
        usage = _usage(response)
        await _report(budget, execution, model_call_id, usage)
        await emitter.emit(
            EventType.MODEL_COMPLETED,
            {"model_call_id": model_call_id, "config_version": execution.config_version, "usage": usage},
        )
        try:
            decision, reason = _parse(_content(response))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"participation judgment failed: {exc}"
            continue
        refs = []
        for item in (request.source.message_id, *(message.message_id for message in recent)):
            if item not in refs:
                refs.append(item)
        decided = await emitter.emit(
            EventType.PARTICIPATION_DECIDED,
            {
                "decision": decision,
                "reason": reason,
                "evidence_refs": tuple(refs[:128]),
                "model_call_id": model_call_id,
            },
        )
        if decision == "ignore":
            await emitter.emit_ending(EventType.EXECUTION_FINAL, {"status": "ignored", "reason": reason})
            return ParticipationOutcome(terminal=True)
        return ParticipationOutcome(terminal=False, event_id=decided.event_id)
    return await _finish_failed(emitter, last_error)


def _unknown_usage() -> ModelUsage:
    return ModelUsage(status="unknown", reason="model call failed")
