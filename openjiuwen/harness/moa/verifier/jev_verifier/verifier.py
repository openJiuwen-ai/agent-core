# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded, invocation-local JEV decisions without progress scoring."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import agent_logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.llm.system_one import ChoiceAnswer, SystemOneResponse
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.moa.config import JevConfig
from openjiuwen.harness.moa.state import MoAState
from openjiuwen.harness.moa.verifier.redaction import redact
from openjiuwen.harness.prompts.moa_jev import build_jev_question
from openjiuwen.harness.schema.task import TodoItem
from openjiuwen.harness.tools.todo import TodoTool

_QUESTION_KEY = "consult_reference"


def _text(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        return redact(content)
    return redact(
        "\n".join(
            part if isinstance(part, str) else part["text"]
            for part in content
            if isinstance(part, str) or (isinstance(part, dict) and isinstance(part.get("text"), str))
        )
    )


def build_jev_state(
    messages: list[BaseMessage], recent_messages: int, todos: list[TodoItem] | None = None
) -> dict[str, Any]:
    """Detach the last k conversation messages and optional current todo state.

    Tool calls are serialized explicitly because System One's BaseMessage
    conversion retains only role/content. Non-text media and model/provider
    metadata are not decision evidence.
    """
    eligible = [
        message
        for message in messages
        if message.role in ("user", "assistant", "tool") and not message.metadata.get("transient")
    ]
    # Native ToolMessages commonly carry only the call ID. Recover names
    # before cutting the window, which may contain only parallel results.
    tool_names = {}
    for message in eligible:
        for call in getattr(message, "tool_calls", None) or []:
            if call.id is not None:
                tool_names[call.id] = call.name
    selected = eligible[-recent_messages:]
    transcript = []
    for message in selected:
        item: dict[str, Any] = {"role": message.role, "content": _text(message.content)}
        if message.name is not None:
            item["name"] = message.name
        calls = getattr(message, "tool_calls", None)
        if calls:
            item["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": redact(call.arguments)} for call in calls
            ]
        if message.role == "tool":
            item["tool_call_id"] = getattr(message, "tool_call_id", None)
            if message.name is None and item["tool_call_id"] in tool_names:
                item["name"] = tool_names[item["tool_call_id"]]
        transcript.append(item)
    state: dict[str, Any] = {"messages": transcript}
    if todos:
        # Forward task evidence, without arbitrary metadata or routing config.
        state["todos"] = [
            {
                "id": todo.id,
                "content": redact(todo.content),
                "activeForm": redact(todo.activeForm),
                "description": redact(todo.description),
                "status": todo.status.value,
                "depends_on": list(todo.depends_on),
                "result_summary": redact(todo.result_summary) if todo.result_summary is not None else None,
            }
            for todo in todos
        ]
    return state


async def _read_todos(agent: Any, session: Any) -> tuple[list[TodoItem] | None, str]:
    """Read through the currently registered native tool, without executing it."""
    get_session_id = getattr(session, "get_session_id", None)
    manager = getattr(agent, "ability_manager", None)
    if not callable(get_session_id) or manager is None:
        return None, "todo_unavailable"
    try:
        for card in manager.list():
            if not isinstance(card, ToolCard):
                continue
            tool = Runner.resource_mgr.get_tool(tool_id=card.id)
            if isinstance(tool, TodoTool) and tool.card is card:
                todos = await tool.load_todos(get_session_id())
                return todos, "todo_available" if todos else "todo_empty"
    except Exception:
        # Error bodies can contain paths or task text; log only this reason.
        return None, "todo_read_failed"
    return None, "todo_unavailable"


def _decode_decision(response: SystemOneResponse) -> bool:
    if not isinstance(response, SystemOneResponse) or not isinstance(response.answers, dict):
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_response")
    answer = response.answers.get(_QUESTION_KEY)
    if (
        not isinstance(answer, ChoiceAnswer)
        or not isinstance(answer.choice, str)
        or answer.choice not in {"CONSULT", "CONTINUE"}
    ):
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_answer")
    probabilities = answer.probabilities
    if not isinstance(probabilities, dict) or set(probabilities) != {"CONSULT", "CONTINUE"}:
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_probabilities")
    values = [answer.confidence, *probabilities.values()]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 1
        or not math.isfinite(value)
        for value in values
    ):
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_probabilities")
    if abs(math.fsum(probabilities.values()) - 1) > 0.01 + 1e-12:
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_distribution")
    if probabilities[answer.choice] + 1e-6 < max(probabilities.values()):
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="invalid_jev_winner")
    return answer.choice == "CONSULT"


async def decide_reference(
    config: JevConfig, state: MoAState, messages: list[BaseMessage], agent: Any, session: Any
) -> bool | None:
    """Spend one bounded decision attempt; None continues the primary model."""
    state.last_jev_decision = None
    if config.max_jev_calls is not None and state.jev_calls >= config.max_jev_calls:
        state.last_jev_reason = "decision_budget_exhausted"
        return None
    if config.max_reference_calls is not None and state.reference_calls >= config.max_reference_calls:
        state.last_jev_reason = "reference_budget_exhausted"
        return None
    state.jev_calls += 1
    try:
        async with asyncio.timeout(config.timeout):
            todos, todo_status = await _read_todos(agent, session)
            payload = build_jev_state(messages, config.recent_messages, todos)
            agent_logger.debug(
                "MoA JEV input: checkpoint=%s todo_status=%s messages=%s todos=%s",
                state.completed_turns,
                todo_status,
                len(payload["messages"]),
                len(todos or []),
            )
            response = await config.client.system_one(
                state=payload,
                questions={_QUESTION_KEY: build_jev_question()},
                request_timeout=config.timeout,
            )
    except TimeoutError:
        state.last_jev_reason = "jev_timeout"
    except Exception:
        state.last_jev_reason = "jev_request_failed"
    else:
        try:
            state.last_jev_decision = _decode_decision(response)
        except BaseError:
            state.last_jev_reason = "invalid_jev_response"
        else:
            state.last_jev_reason = "consult" if state.last_jev_decision else "continue"
    agent_logger.debug(
        "MoA JEV decision: checkpoint=%s calls=%s decision=%s reason=%s",
        state.completed_turns,
        state.jev_calls,
        state.last_jev_decision,
        state.last_jev_reason,
    )
    return state.last_jev_decision
