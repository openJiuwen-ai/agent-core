# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Feed authenticated Team turns and accepted deliveries to the memory provider."""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.rails.memory.jiuwen_memory_coding_rail import JiuwenMemoryCodingRail
from openjiuwen.harness.rails.memory.memory_turn import MemorySource

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.events import TaskAcceptedEvent


@dataclass
class _DeliveryTurn:
    deliveries: set[tuple[str, str]] = field(default_factory=set)
    accepted: dict[tuple[str, str], TaskAcceptedEvent] = field(default_factory=dict)
    output: str = ""


def _delivery_text(text: str) -> str:
    text = text.strip()
    return "" if text.casefold().rstrip(".。!！") in {"done", "completed", "已完成", "完成"} else text


class JiuwenTeamMemoryRail(JiuwenMemoryCodingRail):
    """Reuse document Rail ownership and write lifecycle per Team responder."""

    _structured_turns = True

    def __init__(self, provider: Any, *, content_allowed: Callable[[str], bool] | None = None,
                 **identity: Any) -> None:
        super().__init__(provider, **identity)
        self._content_allowed = content_allowed or (lambda _text: True)
        self._accepted_versions: set[tuple[str, str]] = set()
        self._delivery_context: ContextVar[_DeliveryTurn | None] = ContextVar("team_memory_delivery", default=None)
        self._delivery_key = f"_jiuwen_team_delivery.{id(self):x}"
        self._active_deliveries: dict[tuple[str, str], _DeliveryTurn] = {}
        self._delivered_replies: dict[tuple[str, str], str] = {}

    async def before_invoke(self, ctx: Any) -> None:
        await super().before_invoke(ctx)
        if self._owner(ctx) is not None and self._initialized:
            turn = _DeliveryTurn()
            ctx.extra[self._delivery_key] = turn
            self._delivery_context.set(turn)

    def record_task_delivery(self, task_id: str, version: str) -> None:
        """Associate a committed completion or review submission with this execution."""
        turn = self._delivery_context.get()
        if turn is not None:
            key = task_id, version
            turn.deliveries.add(key)
            self._active_deliveries[key] = turn

    async def after_invoke(self, ctx: Any) -> None:
        try:
            await super().after_invoke(ctx)
        finally:
            turn = ctx.extra.pop(self._delivery_key, None)
            if turn is not None:
                if self._delivery_context.get() is turn:
                    self._delivery_context.set(None)
                for key in turn.deliveries:
                    if self._active_deliveries.get(key) is turn:
                        self._active_deliveries.pop(key)

    def _submit_finished_turn(self, owner, ctx: Any) -> None:
        turn = ctx.extra.get(self._delivery_key)
        if turn is None:
            self._submit_sources(owner.turns.finish_sources(ctx))
            return
        sources = owner.turns.finish_sources(ctx, on_output=lambda text: setattr(turn, "output", text))
        self._submit_sources(sources)
        reply = _delivery_text(turn.output) if len(turn.deliveries) == 1 else ""
        for key in turn.deliveries:
            if self._active_deliveries.get(key) is not turn:
                continue
            event = turn.accepted.get(key)
            if event is not None:
                self._submit_accepted_delivery(event, reply)
            elif reply:
                if len(self._delivered_replies) >= 128:
                    self._delivered_replies.pop(next(iter(self._delivered_replies)))
                self._delivered_replies[key] = reply[:16_000]

    def _submit_sources(self, sources: list[MemorySource]) -> bool:
        if not sources or not all(
            self._content_allowed(text)
            for item in sources for text in (item["text"], *item.get("selection", {}).values())
        ):
            return False
        self._enqueue_write(lambda: self._provider.sync_turn("", "", sources=sources, **self._identity))
        return True

    async def on_task_accepted(self, event: TaskAcceptedEvent) -> None:
        """Queue one write for this task acceptance on its author's Rail."""
        key = (event.task_id, event.acceptance_version)
        if key in self._accepted_versions:
            return
        turn = self._active_deliveries.get(key)
        if turn is not None:
            turn.accepted[key] = event
        elif await self._initialize():
            self._submit_accepted_delivery(event, self._delivered_replies.pop(key, ""))

    def _submit_accepted_delivery(self, event: TaskAcceptedEvent, reply: str) -> None:
        key = (event.task_id, event.acceptance_version)
        if key in self._accepted_versions:
            return
        delivery = []
        for label, text in (("summary", event.summary), ("note", event.note), ("reply", reply)):
            value = _delivery_text(text)
            if value:
                delivery.append((label, value))
        if not delivery:
            return
        sources: list[MemorySource] = []
        for label, text in (("assignment", event.content), *delivery):
            if text.strip():
                sources.append({
                    "id": f"{event.task_id}:{event.acceptance_version}:{label}",
                    "role": "agent" if label == "assignment" else "assistant",
                    "text": text.strip()[:16_000],
                })
        if self._submit_sources(sources):
            self._accepted_versions.add(key)

    def uninit(self, agent: Any) -> None:
        super().uninit(agent)
        if not self.attached_agents:
            self._accepted_versions.clear()
            self._active_deliveries.clear()
            self._delivered_replies.clear()
            self._delivery_context.set(None)
