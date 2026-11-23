# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded interrupt/resume turn aggregation shared by Jiuwen memory rails."""

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptKind
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    RESUME_INTERRUPT_ANSWERS_KEY,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.ask_user_rail import parse_ask_user_payload

_TURN_MAX_CHARS = 16_000
_TURN_MAX_SESSIONS = 128
_TURN_TTL = 1800.0
_TURN_TRUNCATION = "\n[交互轮次内容已截断]\n"


class MemorySource(TypedDict):
    id: str
    role: Literal["user", "agent", "assistant"]
    text: str
    selection: NotRequired[dict[str, str]]


@dataclass
class _MemoryTurn:
    """Bounded user-side material for one logical turn, never a chat history."""

    original: str
    additions: list[str] = field(default_factory=list)
    seen_answers: dict[str, None] = field(default_factory=dict)
    updated: float = field(default_factory=time.monotonic)
    truncated: bool = False
    sources: list[MemorySource] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.original = self._clip(self.original, _TURN_MAX_CHARS - len(_TURN_TRUNCATION))

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        self.truncated = True
        return text[:limit // 2] + text[-(limit - limit // 2):]

    def append(self, text: str) -> None:
        if not text:
            return
        self.updated = time.monotonic()
        budget = _TURN_MAX_CHARS - len(_TURN_TRUNCATION)
        # Reserve room for recent complete answers while retaining both ends
        # of an unusually large original request.
        if len(self.original) + len(text) + 2 > budget:
            self.original = self._clip(self.original, budget // 2)
        if len(self.original) + len(text) + 2 > budget:
            self.truncated = True
            return
        self.additions.append(text)
        while self.additions and len("\n\n".join([self.original, *self.additions])) > budget:
            self.additions.pop(0)
            self.truncated = True

    def render(self) -> str:
        text = "\n\n".join([self.original, *self.additions])
        return text + _TURN_TRUNCATION if self.truncated else text

    def record_source(
        self, role: Literal["user", "agent"], text: str, *, selection: dict[str, str] | None = None,
    ) -> None:
        budget = _TURN_MAX_CHARS - sum(len(source["text"]) for source in self.sources)
        if text.strip() and budget > 0 and len(self.sources) < 32:
            limit = min(budget, _TURN_MAX_CHARS // 2)
            source: MemorySource = {"id": str(len(self.sources)), "role": role, "text": text[:limit]}
            if selection is not None and len(text) <= limit:
                source["selection"] = selection
            self.sources.append(source)


class MemoryTurnBuffer:
    """Keep pending turns for one rail owner, isolated by actual session ID."""

    def __init__(
        self,
        *,
        resolve_user_text: Callable[[AgentCallbackContext], str],
        extract_output: Callable[[AgentCallbackContext], str],
        is_background: Callable[[AgentCallbackContext], bool],
        unwrap_envelope: Callable[[str], str],
        structured: bool = False,
    ) -> None:
        self.turns: dict[str, _MemoryTurn] = {}
        self._resolve_user_text_for_memory = resolve_user_text
        self._extract_assistant_output = extract_output
        self._is_background_run = is_background
        self._unwrap_jiuwenswarm_envelope = unwrap_envelope
        self._structured = structured
        # Different owners may share callback extras during nested invocation.
        self._context_key = f"_jiuwen_memory_turn.{id(self):x}"

    def clear(self) -> None:
        self.turns.clear()

    @staticmethod
    def _turn_session_id(ctx: AgentCallbackContext) -> str:
        session = getattr(ctx, "session", None)
        if session is not None:
            session_id = session.get_session_id()
            if isinstance(session_id, str) and session_id:
                return session_id
        conversation_id = getattr(ctx.inputs, "conversation_id", None)
        return conversation_id if isinstance(conversation_id, str) else ""

    @staticmethod
    def _interruption_state(ctx: AgentCallbackContext):
        get_state = getattr(getattr(ctx, "session", None), "get_state", None)
        return get_state(INTERRUPTION_KEY) if callable(get_state) else None

    @staticmethod
    def _human_input(ctx: AgentCallbackContext) -> str:
        run = getattr(ctx.inputs, "run_context", None) or ctx.extra.get("run_context")
        extras = run.get("extra") if isinstance(run, Mapping) else getattr(run, "extra", None)
        if not isinstance(extras, Mapping):
            return ""
        raw = extras.get("raw_query")
        return raw.strip() if extras.get("raw_query_origin") == "human" and isinstance(raw, str) else ""

    def begin(self, ctx: AgentCallbackContext) -> None:
        if self._is_background_run(ctx):
            return
        now = time.monotonic()
        for key, turn in list(self.turns.items()):
            if now - turn.updated >= _TURN_TTL:
                self.turns.pop(key, None)
        session_id = self._turn_session_id(ctx)
        if not session_id:
            return
        query = getattr(ctx.inputs, "query", None)
        resume = isinstance(query, InteractiveInput) or self._interruption_state(ctx) is not None
        turn = self.turns.get(session_id) if resume else None
        if not isinstance(query, InteractiveInput):
            text = self._resolve_user_text_for_memory(ctx)
            if turn is None and text:
                turn = _MemoryTurn(text)
            elif turn is not None:
                turn.append(text)
            if turn is not None and self._structured:
                human = self._human_input(ctx)
                turn.record_source("user" if human else "agent", human or text)
        if turn is None:
            self.turns.pop(session_id, None)
            return
        self.turns.pop(session_id, None)
        if len(self.turns) >= _TURN_MAX_SESSIONS:
            self.turns.pop(next(iter(self.turns)))
        turn.updated = now
        self.turns[session_id] = turn
        ctx.extra[self._context_key] = turn

    def capture_answer(self, ctx: AgentCallbackContext) -> None:
        """Capture accepted ask_user results, after interrupt ownership routing."""
        if self._is_background_run(ctx):
            return
        if (
            getattr(ctx.inputs, "tool_name", "") != "ask_user"
            or getattr(ctx, "exception", None) is not None
            or getattr(ctx, "is_paused", False)
        ):
            return
        turn = self.turns.get(self._turn_session_id(ctx))
        result = getattr(ctx.inputs, "tool_result", None)
        if turn is None or not isinstance(result, str) or not result:
            return
        call_id = getattr(getattr(ctx.inputs, "tool_call", None), "id", "")
        answers = ctx.extra.get(RESUME_INTERRUPT_ANSWERS_KEY, {}).get(call_id, {})
        for answer in answers.values():
            if answer.kind != InterruptKind.ASK_USER or answer.carried:
                continue
            answer_id = answer.request.interrupt_id
            if answer_id in turn.seen_answers:
                continue
            # Replace a plain-text supplement with the accepted question/answer
            # so values such as an authorization code retain their meaning.
            if answer.unaddressed and isinstance(answer.value, str):
                text = self._unwrap_jiuwenswarm_envelope(answer.value.strip())
                if turn.additions and turn.additions[-1] == text:
                    turn.additions.pop()
            turn.append("用户交互回答：\n" + result)
            if self._structured:
                self._record_answer_sources(turn, answer.request, answer.value)
            turn.seen_answers[answer_id] = None
            if len(turn.seen_answers) > 128:
                turn.seen_answers.pop(next(iter(turn.seen_answers)))

    def _record_answer_sources(self, turn: _MemoryTurn, request, value) -> None:
        questions = getattr(request, "questions", [])
        answers = parse_ask_user_payload(value, questions).answers
        for question in questions:
            prompt = question.get("question", "")
            text = self._unwrap_jiuwenswarm_envelope(answers.get(prompt, "").strip()).strip()
            if not text or not prompt:
                continue
            options = [option for option in (question.get("options") or ()) if option.get("label") == text]
            selection = None
            if len(options) == 1:
                description = options[0].get("description")
                selection = {
                    "question": prompt[:2000],
                    "description": description[:2000] if isinstance(description, str) else "",
                }
            turn.record_source("agent", prompt)
            turn.record_source("user", text, selection=selection)

    def finish(self, ctx: AgentCallbackContext) -> tuple[str, str] | None:
        """Keep paused turns; consume completed or failed turns once."""
        if self._is_background_run(ctx):
            return None

        session_id = self._turn_session_id(ctx)
        turn = ctx.extra.pop(self._context_key, None)
        result = getattr(ctx.inputs, "result", None)
        interrupted = isinstance(result, dict) and result.get("result_type") == "interrupt"
        output = "" if interrupted else self._extract_assistant_output(ctx)
        task = asyncio.current_task()
        cancelled = task is not None and task.cancelling() > 0
        has_pending_interrupt = (
            not output
            and self._interruption_state(ctx) is not None
            and getattr(ctx, "exception", None) is None
        )
        has_invocation_error = getattr(ctx, "exception", None) is not None
        has_error_result = isinstance(result, dict) and result.get("result_type") in {
            "error",
            "cancelled",
            "canceled",
        }
        if cancelled or has_invocation_error or has_error_result:
            if self.turns.get(session_id) is turn:
                self.turns.pop(session_id, None)
            return None
        # Streaming interrupts carry no terminal answer. An empty output alone
        # is insufficient: errors and cancelled streams must not become turns.
        if interrupted or has_pending_interrupt:
            if turn is not None:
                turn.updated = time.monotonic()
            return None
        if self.turns.get(session_id) is turn:
            self.turns.pop(session_id, None)

        query = turn.render() if turn is not None else (
            "" if isinstance(getattr(ctx.inputs, "query", None), InteractiveInput)
            else self._resolve_user_text_for_memory(ctx)
        )
        if not query or not output:
            return None
        return query, output

    def finish_sources(
        self, ctx: AgentCallbackContext, *, on_output: Callable[[str], None] | None = None,
    ) -> list[MemorySource]:
        """Submit only completed turns with evidence attributable to a human."""
        turn = ctx.extra.get(self._context_key)
        completed = self.finish(ctx)
        if completed is not None and on_output is not None:
            on_output(completed[1])
        if completed is None or turn is None or not any(s["role"] == "user" for s in turn.sources):
            return []
        return [*turn.sources, {"id": "answer", "role": "assistant", "text": completed[1][:_TURN_MAX_CHARS]}]
