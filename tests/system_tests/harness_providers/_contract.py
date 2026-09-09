# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-neutral behavioral checks shared by the harness e2e suites.

Every check drives one real harness through the public protocol only:
``start`` / ``send`` / ``turn_events`` / ``events`` / ``abort`` / ``stop``
plus the host interaction handler.  A provider passes a check when its
events satisfy the protocol invariants (one STARTED, one terminal, ordered
sequence, matching state) and its answer carries the expected marker.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from openjiuwen.harness_protocol import (
    AbortMode,
    DeliveryMode,
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessProtocol,
    HarnessState,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    ItemEventKind,
    ItemLifecycleEvent,
    OutputChannel,
    OutputEvent,
    ResumePolicy,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    UserInputResponse,
    json_value_to_builtin,
)
from tests.test_logger import logger

TURN_TIMEOUT_S = 420.0


def make_context(
    *,
    agent_name: str = "e2e-agent",
    cwd: str | None = None,
    system_prompt: str = "",
    host_capabilities: frozenset[HostCapability] = frozenset(),
    interactions: Any = None,
    resume_policy: ResumePolicy = ResumePolicy.NEW,
    checkpoint: Any = None,
    host_session_id: str | None = None,
) -> HarnessContext:
    return HarnessContext(
        agent_name=agent_name,
        agent_id=f"e2e_{agent_name}",
        host_session_id=host_session_id or f"e2e-session-{uuid.uuid4().hex[:8]}",
        system_prompt=system_prompt,
        host_capabilities=host_capabilities,
        interactions=interactions,
        resume_policy=resume_policy,
        checkpoint=checkpoint,
        cwd=cwd,
    )


async def collect_turn(harness: HarnessProtocol, turn_id: str) -> list[HarnessEvent]:
    """Consume ``turn_events`` for ``turn_id`` under a timeout."""

    async def _collect() -> list[HarnessEvent]:
        return [event async for event in harness.turn_events(turn_id)]

    return await asyncio.wait_for(_collect(), timeout=TURN_TIMEOUT_S)


def terminal_of(events: list[HarnessEvent]) -> TurnLifecycleEvent:
    payload = events[-1].event
    assert isinstance(payload, TurnLifecycleEvent), f"last event is not a turn lifecycle event: {payload!r}"
    assert payload.kind in {TurnEventKind.FINISHED, TurnEventKind.ABORTED, TurnEventKind.FAILED}
    return payload


def answer_text(result: TurnResult | None, events: list[HarnessEvent]) -> str:
    """Return the turn answer: ``final_output`` or the concatenated answer outputs."""
    if result is not None:
        final = json_value_to_builtin(result.final_output)
        if isinstance(final, str) and final:
            return final
    pieces: list[str] = []
    for event in events:
        payload = event.event
        if isinstance(payload, OutputEvent) and payload.channel is OutputChannel.ANSWER:
            content = json_value_to_builtin(payload.content)
            if isinstance(content, str):
                pieces.append(content)
    return "".join(pieces)


def assert_turn_invariants(events: list[HarnessEvent], turn_id: str) -> None:
    lifecycle = [event.event for event in events if isinstance(event.event, TurnLifecycleEvent)]
    assert lifecycle[0].kind is TurnEventKind.STARTED
    assert sum(1 for item in lifecycle if item.kind is TurnEventKind.STARTED) == 1
    terminal = [item for item in lifecycle if item.kind is not TurnEventKind.STARTED]
    assert len(terminal) == 1, f"expected exactly one terminal event, got {[item.kind for item in terminal]}"
    assert all(event.turn_id == turn_id for event in events), "turn view leaked another turn's events"
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    assert all(event.host_session_id and event.agent_id for event in events)


def tool_items(events: list[HarnessEvent]) -> list[tuple[str | None, ItemEventKind]]:
    return [
        (event.item_id, event.event.kind)
        for event in events
        if isinstance(event.event, ItemLifecycleEvent) and event.event.item_type == "tool"
    ]


async def run_text_turn(harness: HarnessProtocol, context: HarnessContext) -> None:
    """One plain answer turn: started -> answer output -> FINISHED, back to IDLE."""
    await harness.start(context)
    assert harness.state is HarnessState.IDLE
    receipt = await harness.send(HarnessInput(content="Reply with exactly the single word PONG and nothing else."))
    assert receipt.accepted_mode is DeliveryMode.AUTO
    events = await collect_turn(harness, receipt.turn_id)
    assert_turn_invariants(events, receipt.turn_id)
    terminal = terminal_of(events)
    text = answer_text(terminal.result, events)
    logger.info("[%s] text turn -> %s (%s)", harness.card.name, terminal.kind.value, text)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert "PONG" in text.upper()
    assert any(isinstance(event.event, OutputEvent) for event in events), "no OutputEvent observed"
    assert terminal.result is not None and terminal.result.duration_ms is not None
    await harness.stop()
    assert harness.state is HarnessState.TERMINATED
    await harness.stop()


async def run_tool_turn(harness: HarnessProtocol, context: HarnessContext, workdir: Path) -> None:
    """A turn that must call a file-reading tool; tool items are observable."""
    token = f"TOKEN-{uuid.uuid4().hex[:10].upper()}"
    (workdir / "secret.txt").write_text(f"{token}\n", encoding="utf-8")
    await harness.start(context)
    receipt = await harness.send(
        HarnessInput(
            content=(
                "Use your file reading tool to read the file named secret.txt in the current working "
                "directory, then reply with exactly the token it contains and nothing else."
            )
        )
    )
    events = await collect_turn(harness, receipt.turn_id)
    assert_turn_invariants(events, receipt.turn_id)
    terminal = terminal_of(events)
    text = answer_text(terminal.result, events)
    items = tool_items(events)
    logger.info("[%s] tool turn -> %s items=%s answer=%s", harness.card.name, terminal.kind.value, items, text)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert token in text
    kinds = {kind for _, kind in items}
    assert ItemEventKind.STARTED in kinds and ItemEventKind.COMPLETED in kinds, items
    started_ids = {item_id for item_id, kind in items if kind is ItemEventKind.STARTED}
    completed_ids = {item_id for item_id, kind in items if kind is ItemEventKind.COMPLETED}
    assert started_ids & completed_ids, "tool started/completed items do not share an item id"
    await harness.stop()


async def run_follow_up_turns(harness: HarnessProtocol, context: HarnessContext) -> None:
    """Two inputs accepted back to back run as two serialized turns on one stream."""
    await harness.start(context)
    cursor = harness.events()
    first = await harness.send(HarnessInput(content="Reply with exactly the word ALPHA and nothing else."))
    second = await harness.send(HarnessInput(content="Reply with exactly the word BRAVO and nothing else."))
    assert second.accepted_mode is DeliveryMode.FOLLOW_UP
    assert first.turn_id != second.turn_id

    async def _collect_until_both() -> list[HarnessEvent]:
        seen: list[HarnessEvent] = []
        terminals: set[str] = set()
        async for event in cursor:
            seen.append(event)
            payload = event.event
            if isinstance(payload, TurnLifecycleEvent) and payload.kind is not TurnEventKind.STARTED:
                terminals.add(event.turn_id or "")
                if terminals == {first.turn_id, second.turn_id}:
                    break
        return seen

    events = await asyncio.wait_for(_collect_until_both(), timeout=TURN_TIMEOUT_S * 2)
    await cursor.aclose()
    by_turn: dict[str, list[HarnessEvent]] = {}
    for event in events:
        if event.turn_id:
            by_turn.setdefault(event.turn_id, []).append(event)
    for turn_id, marker in ((first.turn_id, "ALPHA"), (second.turn_id, "BRAVO")):
        assert_turn_invariants(by_turn[turn_id], turn_id)
        terminal = terminal_of(by_turn[turn_id])
        text = answer_text(terminal.result, by_turn[turn_id])
        logger.info("[%s] follow-up turn %s -> %s", harness.card.name, marker, text)
        assert terminal.kind is TurnEventKind.FINISHED
        assert marker in text.upper()
    first_end = max(event.sequence for event in by_turn[first.turn_id])
    second_start = min(event.sequence for event in by_turn[second.turn_id])
    assert first_end < second_start, "follow-up turn interleaved with the first turn"
    await harness.stop()
    assert harness.state is HarnessState.TERMINATED


def _abort_mode_for(harness: HarnessProtocol) -> AbortMode:
    """Pick the abort mode the provider card declares (graceful preferred)."""
    if harness.card.supports(HarnessCapability.GRACEFUL_ABORT):
        return AbortMode.GRACEFUL
    assert harness.card.supports(HarnessCapability.FORCE_ABORT), "provider declares no abort capability"
    return AbortMode.FORCE


async def run_abort_turn(harness: HarnessProtocol, context: HarnessContext) -> None:
    """Abort a long turn after the first output; the turn terminates as ABORTED."""
    await harness.start(context)
    receipt = await harness.send(
        HarnessInput(
            content=(
                "Count slowly from 1 to 400, writing each number on its own line in your reply. "
                "Do not stop early and do not summarize."
            )
        )
    )
    cursor = harness.turn_events(receipt.turn_id)
    events: list[HarnessEvent] = []
    aborted = False

    async def _consume() -> None:
        nonlocal aborted
        async for event in cursor:
            events.append(event)
            if not aborted and isinstance(event.event, OutputEvent):
                aborted = True
                await harness.abort(mode=_abort_mode_for(harness))

    await asyncio.wait_for(_consume(), timeout=TURN_TIMEOUT_S)
    assert aborted, "the turn produced no output to abort on"
    assert_turn_invariants(events, receipt.turn_id)
    terminal = terminal_of(events)
    logger.info("[%s] abort -> %s", harness.card.name, terminal.kind.value)
    assert terminal.kind is TurnEventKind.ABORTED, terminal.result
    assert terminal.result is not None and terminal.result.termination is not None
    assert harness.state is HarnessState.IDLE
    # The session survives an abort: a fresh turn still completes.
    again = await harness.send(HarnessInput(content="Reply with exactly the word RESUMED and nothing else."))
    events = await collect_turn(harness, again.turn_id)
    assert terminal_of(events).kind is TurnEventKind.FINISHED
    await harness.stop()


async def run_steer_turn(harness: HarnessProtocol, context: HarnessContext) -> None:
    """Steer an active turn; the steer receipt targets the active turn id."""
    await harness.start(context)
    receipt = await harness.send(
        HarnessInput(
            content=(
                "Write a four line poem about the sea. Before writing, wait for any additional "
                "instruction that may arrive, then follow the most recent instruction."
            )
        )
    )
    cursor = harness.turn_events(receipt.turn_id)
    events: list[HarnessEvent] = []
    steer_receipt = None

    async def _consume() -> None:
        nonlocal steer_receipt
        async for event in cursor:
            events.append(event)
            if steer_receipt is None and isinstance(event.event, TurnLifecycleEvent):
                steer_receipt = await harness.send(
                    HarnessInput(content="Additional instruction: end your reply with the exact word MOUNTAIN."),
                    mode=DeliveryMode.STEER,
                )

    await asyncio.wait_for(_consume(), timeout=TURN_TIMEOUT_S)
    assert steer_receipt is not None
    assert steer_receipt.turn_id == receipt.turn_id
    assert steer_receipt.accepted_mode is DeliveryMode.STEER
    assert_turn_invariants(events, receipt.turn_id)
    terminal = terminal_of(events)
    text = answer_text(terminal.result, events)
    logger.info("[%s] steer -> %s answer=%s", harness.card.name, terminal.kind.value, text)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    await harness.stop()


class RecordingUserInputHandler:
    """Host interaction handler answering every user-input request with one value."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[Any] = []
        self.cancelled: list[tuple[str, InteractionCancelReason]] = []

    async def handle(self, request: Any) -> Any:
        self.requests.append(request)
        logger.info("user input requested: %s", request.prompt)
        return UserInputResponse(
            request_id=request.request_id,
            status=InteractionResponseStatus.COMPLETED,
            content=self.answer,
        )

    async def cancel(
        self,
        request_id: str,
        *,
        reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW,
    ) -> None:
        self.cancelled.append((request_id, reason))


async def run_user_input_turn(
    harness: HarnessProtocol,
    context: HarnessContext,
    handler: RecordingUserInputHandler,
    *,
    tool_hint: str = "ask-user",
) -> None:
    """A turn that asks the user a question routed through the host handler.

    Args:
        tool_hint: How the prompt names the provider's question tool; models
            only reach for it reliably when addressed by their own tool name.
    """
    await harness.start(context)
    receipt = await harness.send(
        HarnessInput(
            content=(
                f"You must ask me a question before answering: use your {tool_hint} tool to ask me for my favorite "
                "color. After I answer, reply with exactly 'COLOR: ' followed by the color I gave you."
            )
        )
    )
    events = await collect_turn(harness, receipt.turn_id)
    assert_turn_invariants(events, receipt.turn_id)
    terminal = terminal_of(events)
    text = answer_text(terminal.result, events)
    logger.info("[%s] user input -> %s requests=%s answer=%s", harness.card.name, terminal.kind.value, len(handler.requests), text)
    assert handler.requests, "the host user-input handler was never invoked"
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert handler.answer.lower() in text.lower()
    await harness.stop()


async def run_resume_turns(build: Any, context_kwargs: dict[str, Any]) -> None:
    """A checkpoint exported from one cycle resumes the provider session in the next."""
    host_session_id = f"e2e-resume-{uuid.uuid4().hex[:8]}"
    secret = f"ZEBRA-{uuid.uuid4().hex[:6].upper()}"
    first = build()
    await first.start(make_context(host_session_id=host_session_id, **context_kwargs))
    receipt = await first.send(
        HarnessInput(content=f"Remember the secret word {secret}. Reply with exactly the word OK and nothing else.")
    )
    events = await collect_turn(first, receipt.turn_id)
    assert terminal_of(events).kind is TurnEventKind.FINISHED
    checkpoint = await first.export_checkpoint()
    assert checkpoint is not None and checkpoint.provider == first.card.name
    await first.stop()

    second = build()
    await second.start(
        make_context(
            host_session_id=host_session_id,
            resume_policy=ResumePolicy.REQUIRE_RESUME,
            checkpoint=checkpoint,
            **context_kwargs,
        )
    )
    receipt = await second.send(
        HarnessInput(content="What is the secret word I told you earlier? Reply with just that word.")
    )
    events = await collect_turn(second, receipt.turn_id)
    terminal = terminal_of(events)
    text = answer_text(terminal.result, events)
    logger.info("[%s] resume -> %s answer=%s", second.card.name, terminal.kind.value, text)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert secret in text.upper()
    await second.stop()


__all__ = [
    "RecordingUserInputHandler",
    "answer_text",
    "assert_turn_invariants",
    "collect_turn",
    "make_context",
    "run_abort_turn",
    "run_follow_up_turns",
    "run_resume_turns",
    "run_steer_turn",
    "run_text_turn",
    "run_tool_turn",
    "run_user_input_turn",
    "terminal_of",
    "tool_items",
]
