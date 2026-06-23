# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AvatarSessionManager tests (no real LLM).

A fake ``TeamHarness`` stands in for the real one: on ``send`` it fires the
``on_round(finished)`` + ``on_state(IDLE)`` callbacks the manager registered,
exactly as the real supervisor would when a round settles — so the send-wait-
settle rendezvous, per-turn schema tool mount/unmount, context reuse across
turns, and dispose lifecycle are all exercised deterministically.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager
from openjiuwen.agent_teams.workflow.engine.errors import BackendError


class _FakeHarness:
    """Stands in for a started TeamHarness; drives the manager's callbacks."""

    def __init__(self) -> None:
        self._on_state = None
        self._on_round = None
        self.sends: list[str] = []
        self.tools: list = []
        self.rails: list = []
        self.removed: list[str] = []
        self.disposed = False
        self._round = 0
        self.interrupt_next = False
        self.aborted_immediate = None

    async def start(self, *, team_session=None) -> None:
        return None

    async def subscribe(self, *, on_state=None, on_round=None) -> None:
        self._on_state = on_state
        self._on_round = on_round

    async def send(self, content, *, immediate: bool = False) -> str:
        self.sends.append(content)
        self._round += 1
        # A schema turn mounts a structured_output tool; simulate the agent
        # calling it (its captured args become the structured result).
        for tool in self.tools:
            if getattr(tool, "card", None) is not None and tool.card.name == "structured_output":
                tool.captured = {"echo": content}
                tool.called = True
        result_type = "interrupt" if self.interrupt_next else "answer"
        if self._on_round is not None:
            await self._on_round(kind="finished", round_id=self._round, result={"output": f"echo:{content}", "result_type": result_type})
        if self._on_state is not None:
            await self._on_state(old=HarnessState.RUNNING, new=HarnessState.IDLE, session_id="sess")
        return "seq"

    def add_tool(self, tool) -> None:
        self.tools.append(tool)

    def add_rail(self, rail) -> None:
        self.rails.append(rail)

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)
        self.tools = [t for t in self.tools if getattr(t, "card", None) is None or t.card.name != name]

    async def dispose(self) -> None:
        self.disposed = True

    async def abort(self, *, immediate: bool = False) -> None:
        self.aborted_immediate = immediate


def _patch_build(monkeypatch, harnesses: list) -> None:
    """Patch ``TeamHarness.build`` to hand out fresh fake harnesses, recorded."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        h = _FakeHarness()
        h.member_name = member_name
        h.spec = agent_spec
        harnesses.append(h)
        return h

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)


def _mgr(**kw) -> AvatarSessionManager:
    base = DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[])
    return AvatarSessionManager(worker_base_spec=base, team_name="t", language="en", **kw)


def test_agent_session_reuses_one_harness_across_turns(monkeypatch):
    """One avatar is built per session and reused for every turn (context persists)."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr()
        sid = await mgr.open_session(kind="agent", instructions="be brief", opts={"label": "sre"})
        r1 = await mgr.send_turn(sid, "first", {"label": "sre"}, None)
        r2 = await mgr.send_turn(sid, "second", {"label": "sre"}, None)
        await mgr.aclose()
        return sid, r1, r2

    sid, r1, r2 = asyncio.run(scenario())

    assert len(harnesses) == 1  # a single avatar serves both turns
    h = harnesses[0]
    assert h.member_name == sid == "wf-sess-sre-0"
    assert h.sends == ["first", "second"]  # both turns hit the same harness
    assert r1.text == "echo:first" and r2.text == "echo:second"
    assert h.disposed is True  # aclose disposed it
    # The session persona folds in the caller instructions.
    assert "be brief" in (h.spec.system_prompt or "")


def test_agent_session_schema_turn_mounts_and_unmounts_tool(monkeypatch):
    """A schema turn mounts structured_output for that turn and removes it after."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    schema = {"type": "object", "properties": {"echo": {"type": "string"}}, "required": ["echo"]}

    async def scenario():
        mgr = _mgr()
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "q"})
        res = await mgr.send_turn(sid, "structured please", {"label": "q"}, schema)
        await mgr.close_session(sid)
        return res

    res = asyncio.run(scenario())

    # Structured result came through the structured_output tool; the turn prompt
    # carried the schema nudge (the fake echoes whatever content it received).
    assert isinstance(res.structured, dict)
    assert res.structured["echo"].startswith("structured please")
    assert "structured_output" in res.structured["echo"]  # the nudge was appended
    h = harnesses[0]
    # The tool was removed at turn end (mounted only for the schema turn).
    assert h.removed == ["structured_output"] and h.tools == []
    assert h.disposed is True


def test_agent_session_interrupt_raises_backend_error(monkeypatch):
    """A HITL interrupt mid-turn is surfaced as a BackendError, not a partial."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr()
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "x"})
        harnesses[0].interrupt_next = True
        with pytest.raises(BackendError):
            await mgr.send_turn(sid, "go", {"label": "x"}, None)
        await mgr.aclose()

    asyncio.run(scenario())


def test_abort_all_terminates_every_session_and_pending_human(monkeypatch):
    """abort_all hard-aborts every live session (agent AND human) and cancels a
    pending human-reply wait — the pause path that stops all sub-sessions.

    This is the deterministic guarantee behind ``BackgroundTaskController.pause``'s
    second step (``backend.abort_sessions``): the engine MockBackend path answers
    humans instantly so it cannot exercise a *waiting* human, and the real-LLM e2e
    is not CI-verifiable — so the contract is pinned here.
    """
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    base = DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[])

    async def scenario():
        mgr = AvatarSessionManager(
            worker_base_spec=base, human_base_spec=base, team_name="t", language="en"
        )
        await mgr.open_session(kind="agent", instructions=None, opts={"label": "chef"})
        await mgr.open_session(kind="human", instructions=None, opts={"label": "guest"})
        # Simulate a human turn parked on a pending reply (as _await_human_reply does).
        pending = asyncio.get_running_loop().create_future()
        mgr._pending_human["guest:0"] = pending
        await mgr.abort_all()
        return mgr, pending

    mgr, pending = asyncio.run(scenario())

    # Both live sessions — agent and human — had their supervisor harness hard-aborted.
    assert len(harnesses) == 2
    assert all(h.aborted_immediate is True for h in harnesses)
    # The pending human wait was cancelled and the registry cleared (turn won't journal).
    assert pending.cancelled()
    assert mgr._pending_human == {}
    # abort stops but does not dispose — sessions stay for the run's own aclose teardown.
    assert all(h.disposed is False for h in harnesses)
    assert len(mgr._sessions) == 2


def test_open_human_session_without_base_spec_fails_clearly(monkeypatch):
    """A human session with no human_base_spec fails fast (stage-1 behavior)."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr()  # no human_base_spec
        with pytest.raises(BackendError) as exc:
            await mgr.open_session(kind="human", instructions=None, opts={"label": "lead"})
        assert "human" in str(exc.value)

    asyncio.run(scenario())


def test_human_session_pushes_prompt_waits_then_formats_reply(monkeypatch):
    """A human turn signals the prompt out, waits for the reply, then formats it."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    base = DeepAgentSpec(tools=[])
    captured: dict = {}

    def on_prompt(member_name, corr, prompt):
        captured["member"] = member_name
        captured["corr"] = corr
        captured["prompt"] = prompt

    async def scenario():
        mgr = AvatarSessionManager(
            worker_base_spec=base, human_base_spec=base, team_name="t", language="en",
            on_human_prompt=on_prompt,
        )
        sid = await mgr.open_session(kind="human", instructions="confirm", opts={"label": "lead"})

        async def reply_soon():
            for _ in range(1000):
                if "corr" in captured:
                    break
                await asyncio.sleep(0)
            return mgr.submit_human_reply(captured["corr"], "yes, approved")

        send_task = asyncio.create_task(mgr.send_turn(sid, "approve?", {"label": "lead"}, None))
        accepted = await reply_soon()
        res = await send_task
        await mgr.aclose()
        return accepted, res

    accepted, res = asyncio.run(scenario())

    assert accepted is True
    assert captured["member"] == "wf-human-lead-0"
    assert captured["prompt"] == "approve?"  # the question is pushed verbatim
    # The avatar formatted the person's reply (fake echoes the format prompt,
    # which embeds the raw answer + the human persona).
    assert "yes, approved" in res.text
    h = harnesses[0]
    assert "human" in (h.spec.system_prompt or "").lower()  # human avatar persona


def test_human_session_times_out_to_skipped(monkeypatch):
    """No reply within the turn timeout yields a skipped result (engine -> None)."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    base = DeepAgentSpec(tools=[])

    async def scenario():
        mgr = AvatarSessionManager(worker_base_spec=base, human_base_spec=base, team_name="t")
        sid = await mgr.open_session(kind="human", instructions=None, opts={"label": "lead"})
        # Tiny per-turn timeout, no reply submitted -> skipped.
        res = await mgr.send_turn(sid, "approve?", {"label": "lead", "timeout": 0.05}, None)
        await mgr.aclose()
        return res

    res = asyncio.run(scenario())
    assert res.skipped is True


class _FakeMessager:
    """Minimal messager: publish fans out to the topic's subscribed handler."""

    def __init__(self) -> None:
        self.handlers: dict = {}
        self.unsubscribed: list[str] = []

    async def subscribe(self, topic_id: str, handler) -> None:
        self.handlers[topic_id] = handler

    async def unsubscribe(self, topic_id: str) -> None:
        self.unsubscribed.append(topic_id)
        self.handlers.pop(topic_id, None)

    async def publish(self, topic_id: str, message) -> None:
        handler = self.handlers.get(topic_id)
        if handler is not None:
            await handler(message)


def test_human_round_trip_via_messager(monkeypatch):
    """Full inbound path: subscribe on open, a published reply resolves the turn."""
    from openjiuwen.agent_teams.schema.events import (
        EventMessage,
        TeamEvent,
        swarmflow_human_reply_topic,
    )

    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    base = DeepAgentSpec(tools=[])
    messager = _FakeMessager()
    captured: dict = {}

    def on_prompt(member_name, corr, prompt):
        captured["corr"] = corr

    async def scenario():
        mgr = AvatarSessionManager(
            worker_base_spec=base, human_base_spec=base, team_name="t",
            session_id="s1", messager=messager, on_human_prompt=on_prompt,
        )
        sid = await mgr.open_session(kind="human", instructions=None, opts={"label": "lead"})
        topic = swarmflow_human_reply_topic("s1", "t")
        assert topic in messager.handlers  # subscribed lazily on the human open

        async def reply_soon():
            for _ in range(1000):
                if "corr" in captured:
                    break
                await asyncio.sleep(0)
            # Simulate interact_agent_team publishing the person's reply.
            await messager.publish(
                topic,
                EventMessage(
                    event_type=TeamEvent.WORKFLOW_HUMAN_REPLY,
                    payload={"correlation_id": captured["corr"], "answer": "approved"},
                    sender_id="user",
                ),
            )

        send_task = asyncio.create_task(mgr.send_turn(sid, "approve?", {"label": "lead"}, None))
        await reply_soon()
        res = await send_task
        await mgr.aclose()
        return res

    res = asyncio.run(scenario())
    assert "approved" in res.text  # avatar formatted the person's reply
    assert messager.unsubscribed  # aclose unsubscribed from the reply topic


def test_reply_event_for_unknown_correlation_is_ignored(monkeypatch):
    """A reply with no matching pending turn is dropped (late / duplicate)."""
    from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent

    base = DeepAgentSpec(tools=[])
    mgr = AvatarSessionManager(worker_base_spec=base, human_base_spec=base, team_name="t", session_id="s1")
    msg = EventMessage(
        event_type=TeamEvent.WORKFLOW_HUMAN_REPLY,
        payload={"correlation_id": "nope", "answer": "x"},
        sender_id="user",
    )
    # No pending turn registered -> handler is a no-op, must not raise.
    asyncio.run(mgr._on_reply_event(msg))


def test_aclose_disposes_all_open_sessions(monkeypatch):
    """Run-end aclose disposes every avatar that was opened."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr()
        a = await mgr.open_session(kind="agent", instructions=None, opts={"label": "a"})
        b = await mgr.open_session(kind="agent", instructions=None, opts={"label": "b"})
        assert a != b  # distinct member identities
        await mgr.aclose()

    asyncio.run(scenario())
    assert len(harnesses) == 2
    assert all(h.disposed for h in harnesses)
