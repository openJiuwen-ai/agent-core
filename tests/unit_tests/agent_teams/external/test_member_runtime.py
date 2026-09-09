# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior tests for the ExternalHarnessProtocol-to-MemberRuntime bridge."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.agent.member_runtime import MemberRuntime
from openjiuwen.agent_teams.external.member_runtime import (
    ExternalHarnessMemberRuntime,
    TeamContextAwareRuntime,
)
from openjiuwen.agent_teams.external.protocol import (
    AbortMode,
    DeliveryMode,
    EventBufferConfig,
    ExternalHarnessCard,
    ExternalHarnessContext,
    ExternalHarnessInput,
    ExternalHarnessProtocol,
    ExternalHarnessStateError,
    HarnessCapability,
    HarnessEvent,
    ItemEventKind,
    ItemLifecycleEvent,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    SendReceipt,
    StateChangedEvent,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    TurnStatus,
    UnsupportedHarnessCapabilityError,
)
from openjiuwen.agent_teams.harness.state import HarnessState


class _FakeEventCursor:
    """One closable queue-backed event cursor owned by the fake harness."""

    _END = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._finished = False
        self.close_calls = 0

    def __aiter__(self) -> "_FakeEventCursor":
        return self

    async def __anext__(self) -> HarnessEvent:
        value = await self._queue.get()
        if value is self._END:
            raise StopAsyncIteration
        return value

    async def put(self, event: HarnessEvent) -> None:
        await self._queue.put(event)

    async def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._queue.put(self._END)

    async def aclose(self) -> None:
        self.close_calls += 1
        await self.finish()


class _FakeHarness:
    """Protocol-conforming harness with deterministic command/event recording."""

    def __init__(
        self,
        *,
        capabilities: frozenset[HarnessCapability] = frozenset(),
        state: HarnessState = HarnessState.IDLE,
    ) -> None:
        self._card = ExternalHarnessCard(
            name="fake",
            implementation_version="1.0",
            capabilities=capabilities,
        )
        self._state = state
        self._session_id: str | None = None
        self._cursor = _FakeEventCursor()
        self._sequence = 0
        self.start_contexts: list[ExternalHarnessContext] = []
        self.stop_calls = 0
        self.events_calls = 0
        self.send_calls: list[tuple[ExternalHarnessInput, DeliveryMode]] = []
        self.abort_calls: list[AbortMode] = []
        self.pause_calls = 0
        self.resume_calls: list[ExternalHarnessInput | None] = []
        self.fail_first_steer_after_terminal = False

    @property
    def card(self) -> ExternalHarnessCard:
        return self._card

    @property
    def state(self) -> HarnessState:
        return self._state

    @state.setter
    def state(self, value: HarnessState) -> None:
        self._state = value

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def event_buffer_config(self) -> EventBufferConfig:
        return EventBufferConfig(capacity=16)

    async def start(self, context: ExternalHarnessContext) -> None:
        if self._cursor._finished:
            self._cursor = _FakeEventCursor()
        self.start_contexts.append(context)
        self._session_id = "provider-session"
        self._state = HarnessState.IDLE

    async def stop(self) -> None:
        self.stop_calls += 1
        self._state = HarnessState.TERMINATED
        await self._cursor.finish()

    def events(self) -> _FakeEventCursor:
        self.events_calls += 1
        return self._cursor

    def turn_events(self, turn_id: str | None = None) -> _FakeEventCursor:
        _ = turn_id
        return self.events()

    async def send(
        self,
        content: ExternalHarnessInput,
        *,
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> SendReceipt:
        self.send_calls.append((content, mode))
        if self.fail_first_steer_after_terminal and len(self.send_calls) == 1 and mode is DeliveryMode.STEER:
            self._state = HarnessState.IDLE
            raise ExternalHarnessStateError("turn completed before steer acceptance")
        return SendReceipt(
            message_id=f"message-{len(self.send_calls)}",
            turn_id="turn-1",
            accepted_mode=mode,
        )

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        self.abort_calls.append(mode)

    async def pause(self) -> None:
        self.pause_calls += 1

    async def resume(self, *, query: ExternalHarnessInput | None = None) -> None:
        self.resume_calls.append(query)

    async def export_checkpoint(self) -> None:
        return None

    async def emit(
        self,
        payload: Any,
        *,
        turn_id: str | None = None,
        item_id: str | None = None,
    ) -> None:
        event = HarnessEvent(
            sequence=self._sequence,
            timestamp=float(self._sequence),
            event=payload,
            team_session_id="team-session",
            member_agent_id="member-agent",
            session_id=self._session_id,
            turn_id=turn_id,
            item_id=item_id,
        )
        self._sequence += 1
        await self._cursor.put(event)


class _FakeMemberSession:
    def __init__(self) -> None:
        self.pre_run_calls = 0
        self.post_run_calls = 0

    async def pre_run(self) -> None:
        self.pre_run_calls += 1

    async def post_run(self) -> None:
        self.post_run_calls += 1


class _FakeTeamSession:
    def __init__(self) -> None:
        self.created: list[tuple[str, bool]] = []
        self.member_session = _FakeMemberSession()

    def create_agent_session(self, *, agent_id: str, share_stream_writer: bool) -> _FakeMemberSession:
        self.created.append((agent_id, share_stream_writer))
        return self.member_session


class _OneShotTeamContextTracker:
    """Return one announcement and hide it only after a successful commit."""

    def __init__(self, text: str = "<team-context>new roster</team-context>") -> None:
        self.text = text
        self.commits = 0

    async def pending_text(self, session: Any) -> str | None:
        _ = session
        return None if self.commits else self.text

    async def commit(self, session: Any) -> None:
        _ = session
        self.commits += 1


def _context() -> ExternalHarnessContext:
    return ExternalHarnessContext(
        team_name="team",
        member_name="worker",
        member_agent_id="member-agent",
        team_session_id="team-session",
        system_prompt="work carefully",
    )


async def _all_outputs(runtime: ExternalHarnessMemberRuntime) -> list[Any]:
    return [chunk async for chunk in runtime.outputs()]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_context_factory_start_stop_and_event_pump() -> None:
    harness = _FakeHarness()
    team_session = _FakeTeamSession()
    factory_calls: list[Any] = []

    async def context_factory(received_team_session: Any) -> ExternalHarnessContext:
        factory_calls.append(received_team_session)
        return _context()

    runtime = ExternalHarnessMemberRuntime(harness=harness, context=context_factory)
    await runtime.start(team_session=team_session)
    await harness.emit(
        OutputEvent(output_id="answer", kind=OutputKind.TEXT, content="ready", operation=OutputOperation.DELTA),
        turn_id="turn-1",
    )
    await runtime.stop()

    assert factory_calls == [team_session]
    assert harness.start_contexts == [_context()]
    assert harness.events_calls == 1
    assert harness.stop_calls == 1
    assert harness._cursor.close_calls == 1
    assert team_session.created == [("member-agent", False)]
    assert team_session.member_session.pre_run_calls == 1
    assert team_session.member_session.post_run_calls == 1
    assert runtime.state is HarnessState.TERMINATED
    assert runtime.session_id == "provider-session"
    chunks = await _all_outputs(runtime)
    assert [(chunk.type, chunk.payload["content"]) for chunk in chunks] == [("llm_output", "ready")]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_each_runtime_cycle_owns_and_finalizes_a_fresh_member_session() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())
    first_team_session = _FakeTeamSession()
    second_team_session = _FakeTeamSession()

    await runtime.start(team_session=first_team_session)
    await asyncio.gather(runtime.stop(), runtime.stop())
    await runtime.start(team_session=second_team_session)
    await runtime.stop()

    assert first_team_session.member_session.pre_run_calls == 1
    assert first_team_session.member_session.post_run_calls == 1
    assert second_team_session.member_session.pre_run_calls == 1
    assert second_team_session.member_session.post_run_calls == 1
    assert harness.stop_calls == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_harness_start_finalizes_the_member_session() -> None:
    class _FailingStartHarness(_FakeHarness):
        async def start(self, context: ExternalHarnessContext) -> None:
            _ = context
            raise RuntimeError("start failed")

    harness = _FailingStartHarness()
    team_session = _FakeTeamSession()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    with pytest.raises(RuntimeError, match="start failed"):
        await runtime.start(team_session=team_session)

    assert harness.stop_calls == 1
    assert team_session.member_session.pre_run_calls == 1
    assert team_session.member_session.post_run_calls == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_output_deltas_and_final_snapshot_are_not_duplicated() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())
    await runtime.start()

    await harness.emit(
        OutputEvent(output_id="answer", kind=OutputKind.TEXT, content="Hel", operation=OutputOperation.DELTA),
        turn_id="turn-1",
    )
    await harness.emit(
        OutputEvent(output_id="answer", kind=OutputKind.TEXT, content="lo", operation=OutputOperation.DELTA),
        turn_id="turn-1",
    )
    await harness.emit(
        OutputEvent(output_id="answer", kind=OutputKind.TEXT, content="Hello", operation=OutputOperation.FINAL),
        turn_id="turn-1",
    )
    await harness.emit(
        OutputEvent(
            output_id="reasoning",
            kind=OutputKind.TEXT,
            content="trace",
            operation=OutputOperation.FINAL,
            channel=OutputChannel.REASONING,
        ),
        turn_id="turn-1",
    )
    await runtime.stop()

    chunks = await _all_outputs(runtime)
    assert [chunk.type for chunk in chunks] == ["llm_output", "llm_output", "llm_reasoning"]
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert [chunk.payload["content"] for chunk in chunks] == ["Hel", "lo", "trace"]
    assert [chunk.payload["operation"] for chunk in chunks] == ["delta", "delta", "final"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_tool_lifecycle_projects_call_and_result() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())
    await runtime.start()

    await harness.emit(
        ItemLifecycleEvent(
            kind=ItemEventKind.STARTED,
            item_type="tool",
            data={"name": "search", "arguments": {"query": "weather"}},
        ),
        turn_id="turn-1",
        item_id="call-1",
    )
    await harness.emit(
        ItemLifecycleEvent(
            kind=ItemEventKind.COMPLETED,
            item_type="tool",
            data={"tool_name": "search", "result": {"temperature": 25}},
        ),
        turn_id="turn-1",
        item_id="call-1",
    )
    await runtime.stop()

    call, result = await _all_outputs(runtime)
    assert call.type == "tool_call"
    assert call.payload == {
        "name": "search",
        "arguments": '{"query":"weather"}',
        "tool_call_id": "call-1",
    }
    assert result.type == "tool_result"
    assert result.payload == {
        "tool_name": "search",
        "result": {"temperature": 25},
        "tool_call_id": "call-1",
    }


@pytest.mark.asyncio
@pytest.mark.level1
async def test_state_and_turn_events_reach_member_callbacks() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())
    state_events: list[tuple[HarnessState, HarnessState, str | None]] = []
    round_events: list[tuple[str, str | None, TurnResult | None]] = []
    round_finished = asyncio.Event()

    async def on_state(*, old: HarnessState, new: HarnessState, session_id: str | None) -> None:
        state_events.append((old, new, session_id))

    async def on_round(*, kind: str, round_id: str | None, result: TurnResult | None) -> None:
        round_events.append((kind, round_id, result))
        if kind == "finished":
            round_finished.set()

    await runtime.subscribe(on_state=on_state, on_round=on_round)
    await runtime.start()
    result = TurnResult(status=TurnStatus.COMPLETED, final_output="done")
    await harness.emit(StateChangedEvent(old=HarnessState.IDLE, new=HarnessState.RUNNING))
    await harness.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), turn_id="turn-1")
    await harness.emit(TurnLifecycleEvent(kind=TurnEventKind.FINISHED, result=result), turn_id="turn-1")
    await asyncio.wait_for(round_finished.wait(), timeout=1)

    assert state_events == [(HarnessState.IDLE, HarnessState.RUNNING, "provider-session")]
    assert round_events == [
        ("started", "turn-1", None),
        ("finished", "turn-1", result),
    ]
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_immediate_send_from_idle_uses_auto() -> None:
    harness = _FakeHarness(state=HarnessState.IDLE)
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    receipt = await runtime.send("start", immediate=True)

    assert harness.send_calls[0][0].content == "start"
    assert harness.send_calls[0][1] is DeliveryMode.AUTO
    assert receipt.accepted_mode is DeliveryMode.AUTO


@pytest.mark.asyncio
@pytest.mark.level1
async def test_immediate_send_while_running_uses_steer_when_supported() -> None:
    harness = _FakeHarness(
        state=HarnessState.RUNNING,
        capabilities=frozenset({HarnessCapability.STEER}),
    )
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    receipt = await runtime.send("redirect", immediate=True)

    assert harness.send_calls[0][1] is DeliveryMode.STEER
    assert receipt.accepted_mode is DeliveryMode.STEER


@pytest.mark.asyncio
@pytest.mark.level1
async def test_immediate_send_while_running_without_steer_becomes_follow_up() -> None:
    harness = _FakeHarness(state=HarnessState.RUNNING)
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    receipt = await runtime.send("next", immediate=True)

    assert harness.send_calls[0][1] is DeliveryMode.FOLLOW_UP
    assert receipt.accepted_mode is DeliveryMode.FOLLOW_UP


@pytest.mark.asyncio
@pytest.mark.level1
async def test_terminal_race_retries_rejected_steer_as_auto() -> None:
    harness = _FakeHarness(
        state=HarnessState.RUNNING,
        capabilities=frozenset({HarnessCapability.STEER}),
    )
    harness.fail_first_steer_after_terminal = True
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    receipt = await runtime.send("race", immediate=True)

    assert [mode for _content, mode in harness.send_calls] == [DeliveryMode.STEER, DeliveryMode.AUTO]
    assert receipt.accepted_mode is DeliveryMode.AUTO


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unsupported_controls_raise_while_they_are_actionable() -> None:
    harness = _FakeHarness(state=HarnessState.RUNNING)
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    with pytest.raises(UnsupportedHarnessCapabilityError, match="graceful_abort"):
        await runtime.abort()
    with pytest.raises(UnsupportedHarnessCapabilityError, match="force_abort"):
        await runtime.abort(immediate=True)
    with pytest.raises(UnsupportedHarnessCapabilityError, match="pause/resume"):
        await runtime.pause()
    harness.state = HarnessState.PAUSED
    with pytest.raises(UnsupportedHarnessCapabilityError, match="pause/resume"):
        await runtime.resume()

    assert harness.abort_calls == []
    assert harness.pause_calls == 0
    assert harness.resume_calls == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unsupported_force_abort_can_fall_back_to_stopping_the_harness() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=_context(),
        stop_on_unsupported_force_abort=True,
    )
    await runtime.start()
    harness.state = HarnessState.RUNNING

    await runtime.abort(immediate=True)

    assert harness.stop_calls == 1
    assert harness.abort_calls == []
    assert harness.state is HarnessState.TERMINATED
    assert await _all_outputs(runtime) == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_context_announcement_is_committed_and_not_repeated() -> None:
    harness = _FakeHarness()
    tracker = _OneShotTeamContextTracker()
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=_context(),
        team_context_tracker=tracker,
    )
    await runtime.start(team_session=_FakeTeamSession())

    await runtime.announce_team_context()
    await runtime.announce_team_context()

    assert [(content.content, mode) for content, mode in harness.send_calls] == [
        (tracker.text, DeliveryMode.AUTO),
    ]
    assert tracker.commits == 1
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_concurrent_sends_deliver_pending_team_context_once() -> None:
    class _BlockingFirstSendHarness(_FakeHarness):
        def __init__(self) -> None:
            super().__init__()
            self.first_send_entered = asyncio.Event()
            self.release_first_send = asyncio.Event()
            self._blocked = False

        async def send(
            self,
            content: ExternalHarnessInput,
            *,
            mode: DeliveryMode = DeliveryMode.AUTO,
        ) -> SendReceipt:
            if not self._blocked:
                self._blocked = True
                self.first_send_entered.set()
                await self.release_first_send.wait()
            return await super().send(content, mode=mode)

    harness = _BlockingFirstSendHarness()
    tracker = _OneShotTeamContextTracker()
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=_context(),
        team_context_tracker=tracker,
    )
    await runtime.start(team_session=_FakeTeamSession())

    first = asyncio.create_task(runtime.send("first"))
    await asyncio.wait_for(harness.first_send_entered.wait(), timeout=1)
    second = asyncio.create_task(runtime.send("second"))
    await asyncio.sleep(0)
    harness.release_first_send.set()
    await asyncio.gather(first, second)

    assert [content.content for content, _mode in harness.send_calls] == [
        f"{tracker.text}\n\nfirst",
        "second",
    ]
    assert tracker.commits == 1
    await runtime.stop()


@pytest.mark.level1
def test_runtime_satisfies_member_and_team_context_protocols() -> None:
    harness = _FakeHarness()
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())

    assert isinstance(harness, ExternalHarnessProtocol)
    assert isinstance(runtime, MemberRuntime)
    assert isinstance(runtime, TeamContextAwareRuntime)
