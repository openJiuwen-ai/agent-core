# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task-scoped trajectory capture shared by Work and Coding memory rails."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.ability_manager import resolve_tool_result_text
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.memory.memory_turn import get_turn_interruption_state, get_turn_session_id


_CAPTURE_TTL = 3600
_MAX_TOOL_RECORDS = 100
_MAX_FIELD_LENGTH = 8192
_MAX_SUSPENDED = 32
STATUS_TOOL = "set_task_status"
_WAITING = "waiting_for_user"
_COMPLETED = "completed"


async def _set_task_status(status: str) -> dict[str, str]:
    if status == _WAITING:
        return {"status": status, "next": "Use ask_user for the missing information to resume this task."}
    if status == _COMPLETED:
        return {"status": status}
    return {"error": "Expected waiting_for_user or completed."}


@dataclass
class _Execution:
    query: str
    trajectory_id: str = field(default_factory=lambda: f"agent_{uuid4().hex}")
    updated_at: float = field(default_factory=time.monotonic)
    status: str = ""
    requires_completion: bool = False
    activities: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_MAX_TOOL_RECORDS),
    )


class ExperienceSync(Protocol):
    """Optional provider callback receiving a completed logical execution."""

    def __call__(
        self,
        trajectory_id: str,
        query: str,
        output: str,
        activities: list[dict[str, Any]],
        session_id: str,
    ) -> Awaitable[None]:
        ...


def _field_text(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return value[:_MAX_FIELD_LENGTH]


def register_status_tool(agent: Any) -> ToolCard | None:
    """Register the status tool and return its card when owned by this rail."""
    if not hasattr(agent, "ability_manager"):
        return None
    card = ToolCard(
        id=STATUS_TOOL,
        name=STATUS_TOOL,
        description=(
            "Mark the current task waiting_for_user before using ask_user, "
            "or completed after finishing the resumed task."
        ),
        input_params={
            "type": "object",
            "properties": {"status": {"type": "string", "enum": [_WAITING, _COMPLETED]}},
            "required": ["status"],
        },
    )
    result = agent.ability_manager.add_ability(card, LocalFunction(card=card, func=_set_task_status))
    return card if result.added else None


async def drain_experience_writes(writes: set[asyncio.Task[None]]) -> None:
    """Give submitted trajectories time to finish before provider shutdown."""
    if writes:
        _, pending = await asyncio.wait(writes, timeout=10)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class ExperienceCapture:
    """Keep logical task evidence isolated for one rail owner."""

    def __init__(
        self,
        sync_experience: ExperienceSync,
        writes: set[asyncio.Task[None]],
        *,
        resolve_user_text: Callable[[AgentCallbackContext], str],
        extract_output: Callable[[AgentCallbackContext], str],
        is_background: Callable[[AgentCallbackContext], bool],
    ) -> None:
        if not callable(sync_experience):
            raise TypeError("sync_experience must be callable or None")
        self._sync_experience = sync_experience
        self._experience_writes = writes
        self._resolve_user_text_for_memory = resolve_user_text
        self._extract_assistant_output = extract_output
        self._is_background_run = is_background
        self._experience_active: dict[str, _Execution] = {}
        self._experience_suspended: dict[str, _Execution] = {}

    def clear(self) -> None:
        self._experience_active.clear()
        self._experience_suspended.clear()

    def begin(self, ctx: AgentCallbackContext) -> None:
        if self._is_background_run(ctx):
            return
        sid = get_turn_session_id(ctx)
        if not sid:
            return
        now = time.monotonic()
        for key, previous in list(self._experience_suspended.items()):
            if now - previous.updated_at >= _CAPTURE_TTL:
                self._experience_suspended.pop(key)
        query = getattr(ctx.inputs, "query", None)
        if isinstance(query, InteractiveInput) or get_turn_interruption_state(ctx) is not None:
            suspended = self._experience_suspended.pop(sid, None)
            if suspended is not None:
                suspended.status = ""
                self._experience_active[sid] = suspended
            return
        text = self._resolve_user_text_for_memory(ctx)
        if text:
            self._experience_active[sid] = _Execution(text[:_MAX_FIELD_LENGTH])

    def capture_tool(self, ctx: AgentCallbackContext, *, owns_status_tool: bool) -> None:
        if self._is_background_run(ctx):
            return
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        capture = self._experience_active.get(get_turn_session_id(ctx))
        if inputs.tool_name == STATUS_TOOL:
            if capture is not None and ctx.exception is None and owns_status_tool:
                args = inputs.tool_args
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        return
                status = args.get("status") if isinstance(args, dict) else None
                if status in (_WAITING, _COMPLETED):
                    capture.status = status
                    if status == _WAITING:
                        capture.requires_completion = True
            return
        call_id = getattr(inputs.tool_call, "id", None)
        if capture is None or not call_id or not inputs.tool_name:
            return
        result = resolve_tool_result_text(inputs, ctx.exception)
        if result is None and inputs.tool_result is not None:
            result = str(inputs.tool_result)
        if result is None and ctx.exception is not None:
            result = str(ctx.exception)
        if result is None:
            return
        capture.activities.append({
            "type": "tool_call",
            "tool_call_id": str(call_id),
            "tool_name": inputs.tool_name,
            "content": _field_text(inputs.tool_args),
            "tool_result_preview": result[:_MAX_FIELD_LENGTH],
        })

    def finish(self, ctx: AgentCallbackContext) -> None:
        if self._is_background_run(ctx):
            return
        sid = get_turn_session_id(ctx)
        capture = self._experience_active.pop(sid, None)
        task = asyncio.current_task()
        cancelled = task is not None and task.cancelling() > 0
        if capture is None or ctx.exception is not None or cancelled:
            return
        result = getattr(ctx.inputs, "result", None)
        result_type = result.get("result_type") if isinstance(result, dict) else None
        if result_type in {"error", "cancelled", "canceled"}:
            return
        pending_interrupt = not result and get_turn_interruption_state(ctx) is not None
        interrupted = result_type == "interrupt" or pending_interrupt
        waiting = capture.requires_completion and capture.status != _COMPLETED
        if interrupted or waiting:
            capture.updated_at = time.monotonic()
            self._experience_suspended.pop(sid, None)
            if len(self._experience_suspended) >= _MAX_SUSPENDED:
                self._experience_suspended.pop(next(iter(self._experience_suspended)))
            self._experience_suspended[sid] = capture
            return
        if not capture.activities or result_type not in {None, "answer"}:
            return
        output = self._extract_assistant_output(ctx)
        if not output:
            return
        write = asyncio.create_task(self._submit_experience(sid, capture, output[:_MAX_FIELD_LENGTH]))
        self._experience_writes.add(write)
        write.add_done_callback(self._experience_writes.discard)

    async def _submit_experience(self, session_id: str, capture: _Execution, output: str) -> None:
        activities = list(capture.activities)
        try:
            for delay in (0, 1, 3):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    await self._sync_experience(
                        capture.trajectory_id, capture.query, output, activities, session_id,
                    )
                    return
                except OSError:
                    if delay == 3:
                        raise
        except Exception as exc:
            logger.warning("Agent experience submission failed: %s", type(exc).__name__)
