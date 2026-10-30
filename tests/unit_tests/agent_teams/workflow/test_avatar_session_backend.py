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
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.agent_teams.workflow.backends import avatar_session_backend as asb_mod
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager
from openjiuwen.agent_teams.workflow.backends.external_fork import ForkCredentials
from openjiuwen.agent_teams.workflow.backends.external_turns import ExternalTurnOutcome
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.harness_protocol import HarnessProtocolError


class _FakeNative:
    """Stands in for the DeepAgent a harness exposes; holds a message list."""

    def __init__(self, messages: list | None = None) -> None:
        self.messages = list(messages) if messages else []
        self.seeded: list[tuple[str | None, list]] = []  # (session_id, messages)
        self.sid = "child-sess"

    def get_current_context(self, session_id: str | None = None) -> list:
        return list(self.messages)

    async def create_new_context_engine(self, session_id: str | None = None, messages: list | None = None) -> str:
        self.seeded.append((session_id, list(messages) if messages else []))
        self.messages = list(messages) if messages else []  # context now holds the injected messages
        return session_id or "child-sess"

    @property
    def session_id(self) -> str:
        return self.sid


class _FakeChild:
    """Stands in for the child ``Session`` a harness binds; records state writes."""

    def __init__(self) -> None:
        self.state_updates: list[dict] = []

    def update_state(self, data: dict) -> None:
        self.state_updates.append(data)


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
        self.aborted_immediate: bool | None = None
        self.member_name: str | None = None
        self.spec: Any = None
        self.started_with: Any = None  # team_session passed to start
        self.native = _FakeNative()
        self.child = _FakeChild()

    def current_session(self) -> _FakeChild:
        return self.child

    async def start(self, *, team_session=None) -> None:
        self.started_with = team_session
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
            await self._on_round(
                kind="finished", round_id=self._round, result={"output": f"echo:{content}", "result_type": result_type}
            )
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

    def get_deep_agent(self) -> _FakeNative:
        return self.native

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


def test_session_member_name_is_run_scoped():
    """Session avatar names carry the run prefix, mirroring worker member names.

    Without the prefix, two runs of the same script mint the same member name
    and their avatars share one NativeHarness session id, one workspace dir
    and one owner-qualified resource-manager key (the "Tool instance not
    found" cross-run collision). Without a run id (direct construction) the
    legacy ``wf-sess-`` form is kept.
    """
    scoped = _mgr(run_id="wf_57e978bddb2e")
    legacy = _mgr()

    async def scenario():
        a = await scoped.ensure_member_name(kind="agent", opts={"label": "analyst"})
        h = await scoped.ensure_member_name(kind="human", opts={"label": "lead"})
        c = await legacy.ensure_member_name(kind="agent", opts={"label": "analyst"})
        return a, h, c

    a, h, c = asyncio.run(scenario())
    assert a == "wf-57e978bddb2e-sess-analyst-0"
    assert h == "wf-57e978bddb2e-human-lead-1"
    assert c == "wf-sess-analyst-0"


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
    # Natural-language round output wins over JSON dump of the capture.
    assert res.text.startswith("echo:structured please")
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
        mgr = AvatarSessionManager(worker_base_spec=base, human_base_spec=base, team_name="t", language="en")
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
            worker_base_spec=base,
            human_base_spec=base,
            team_name="t",
            language="en",
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
            worker_base_spec=base,
            human_base_spec=base,
            team_name="t",
            session_id="s1",
            messager=messager,
            on_human_prompt=on_prompt,
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


# ----------------------------------------------------------------------
# Fork: stable session ids, round-boundary mapping, capture_fork, seeding
# ----------------------------------------------------------------------


def test_derive_avatar_session_id_is_stable_and_namespaced():
    """The derived id is stable per member, and workflow/team-aware."""
    mgr = _mgr(workflow_name="wf1")
    a1 = mgr._derive_avatar_session_id("wf-sess-sre-0")
    a2 = mgr._derive_avatar_session_id("wf-sess-sre-0")
    other = mgr._derive_avatar_session_id("wf-sess-other-0")
    assert a1 == a2  # stable across calls (and therefore across a resume)
    assert a1 != other  # unique per member
    assert a1.startswith("t/wf1/")  # team + workflow namespacing
    assert _mgr(workflow_name="wf2")._derive_avatar_session_id("wf-sess-sre-0") != a1
    assert _mgr()._derive_avatar_session_id("wf-sess-sre-0").endswith("wf-sess-sre-0")


def test_round_boundary_index_maps_rounds_to_message_index():
    """before/after boundaries slice at the Nth UserMessage; out of range -> None."""
    from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import _round_boundary_index

    msgs = [
        UserMessage(content="u1"),
        AssistantMessage(content="a1"),
        UserMessage(content="u2"),
        AssistantMessage(content="a2"),
        UserMessage(content="u3"),
        AssistantMessage(content="a3"),
    ]
    # before: keep through round 2 (index of the 3rd UserMessage = 4)
    assert _round_boundary_index(msgs, 2, keep_after=False) == 4
    # before round 1: keep through index 2 (the 2nd UserMessage)
    assert _round_boundary_index(msgs, 1, keep_after=False) == 2
    # after round 2: start at index 2 (the 2nd UserMessage)
    assert _round_boundary_index(msgs, 2, keep_after=True) == 2
    # after round 1: start at index 0
    assert _round_boundary_index(msgs, 1, keep_after=True) == 0
    # out of range / None -> None (caller falls back to full context)
    assert _round_boundary_index(msgs, 4, keep_after=False) is None
    assert _round_boundary_index(msgs, 4, keep_after=True) is None
    assert _round_boundary_index(msgs, None, keep_after=False) is None


def _rounds(n: int) -> list[Any]:
    msgs: list[Any] = []
    for i in range(n):
        msgs.append(UserMessage(content=f"q{i}"))
        msgs.append(AssistantMessage(content=f"a{i}"))
    return msgs


@pytest.mark.parametrize(
    ("fork_mode", "keep_rounds", "expected_len", "expected_split", "expected_direction"),
    [
        ("full", None, 6, None, None),
        ("before", 2, 4, None, None),   # rounds 1-2 kept
        ("after", 2, 4, None, None),    # rounds 2-3 kept
        ("keep_before_compact_after", 2, 6, 4, "after"),  # full capture, tail compressed on injection
        ("keep_after_compact_before", 2, None, 4, "before"),  # head compressed on injection
    ],
)
def test_capture_fork_modes(monkeypatch, fork_mode, keep_rounds, expected_len, expected_split, expected_direction):
    """Each fork mode captures the context its contract names (keep-rounds table)."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr(workflow_name="wf")
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "p"})
        harnesses[0].native.messages = _rounds(3)
        return await mgr.capture_fork(sid, keep_rounds=keep_rounds, fork_mode=fork_mode)

    data = asyncio.run(scenario())
    assert data is not None
    if expected_len is not None:
        assert len(data["messages"]) == expected_len
    assert data["compact_split"] == expected_split
    if expected_direction is not None:
        assert data["compact_direction"] == expected_direction


def test_capture_fork_degrades_out_of_range_and_unknown(monkeypatch, caplog):
    """Out-of-range keep_rounds degrades per mode; an unknown session yields None.

    ``before`` falls back to a full capture with a warning, a compact mode pins
    its split to the end (no-op compaction), and a session with no live row
    returns None so the engine degrades to its history mirror.
    """
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr(workflow_name="wf")
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "p"})
        harnesses[0].native.messages = _rounds(3)
        before = await mgr.capture_fork(sid, keep_rounds=99, fork_mode="before")
        compact = await mgr.capture_fork(sid, keep_rounds=99, fork_mode="keep_before_compact_after")
        unknown = await mgr.capture_fork("no-such-session", keep_rounds=None, fork_mode="full")
        return before, compact, unknown

    before, compact, unknown = asyncio.run(scenario())
    assert len(before["messages"]) == 6  # full fallback
    assert compact["compact_split"] == 6  # pinned to the end -> no-op compaction
    assert any("out-of-range" in rec.message for rec in caplog.records)
    assert unknown is None


def test_open_session_with_fork_data_seeds_child_context(monkeypatch):
    """A fork child's context is injected from fork_data right after start."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    from openjiuwen.core.session.vcs.codec import encode_message

    fork_data = {
        "messages": [encode_message(m) for m in _rounds(2)],
        "compact_split": None,
        "compact_direction": "before",
    }

    async def scenario():
        mgr = _mgr(workflow_name="wf")
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "child"}, fork_data=fork_data)
        return sid, harnesses[0]

    sid, h = asyncio.run(scenario())
    assert sid == "wf-sess-child-0"
    # The child context was seeded into the native's context engine (via the
    # child's bound session id).
    assert len(h.native.seeded) == 1
    assert len(h.native.seeded[0][1]) == 4
    # The fork messages were also written back to the child session's state so
    # the child's first model call loads the fork context (not a stale prior
    # context recovered by pre_run).
    assert len(h.child.state_updates) == 1
    ctx = h.child.state_updates[0]["context"]
    assert len(ctx["default_context_id"]["messages"]) == 4
    # The avatar bound a stable team session (fixed session_id, not random).
    assert h.started_with is not None
    assert h.started_with.get_session_id().startswith("t/wf/")
    assert h.started_with.get_session_id().endswith("wf-sess-child-0")
    assert harnesses[0].native.sid == "child-sess"


def test_mirror_fallback_seeds_history_only_on_first_turn(monkeypatch):
    """fork_data=None + non-empty history seeds the mirror once, then not again."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr(workflow_name="wf")
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "m"})
        history = [
            {"role": "user", "content": "q0"},
            {"role": "assistant", "content": "a0"},
        ]
        await mgr.send_turn(sid, "second", {"label": "m"}, None, history=history)
        state = mgr._sessions[sid]
        # A second turn does not re-seed (context_seeded guard).
        await mgr.send_turn(sid, "third", {"label": "m"}, None, history=history)
        state2 = mgr._sessions[sid]
        return state, state2, harnesses[0]

    state, state2, h = asyncio.run(scenario())
    assert state.context_seeded is True
    assert len(h.native.seeded) == 1
    assert len(h.native.seeded[0][1]) == 2
    assert state2.context_seeded is True
    assert len(h.native.seeded) == 1  # still only the first seed


def test_mirror_fallback_skipped_without_history(monkeypatch):
    """No history mirror -> no seeding (a cold-start session stays clean)."""
    harnesses: list = []
    _patch_build(monkeypatch, harnesses)

    async def scenario():
        mgr = _mgr(workflow_name="wf")
        sid = await mgr.open_session(kind="agent", instructions=None, opts={"label": "m"})
        await mgr.send_turn(sid, "first", {"label": "m"}, None)
        return mgr._sessions[sid], harnesses[0]

    state, h = asyncio.run(scenario())
    assert state.context_seeded is False  # nothing to seed, marker untouched
    assert h.native.seeded == []  # nothing was injected


def test_persisted_messages_recovers_tool_message_context_from_checkpointer():
    """A parent's ToolMessage-rich context committed to the checkpointer is
    recoverable by member name after a fully-hit resume (no live avatar)."""
    from openjiuwen.core.foundation.llm.schema.message import ToolMessage
    from openjiuwen.core.session.agent import create_agent_session
    from openjiuwen.core.single_agent import AgentCard

    mgr = _mgr(workflow_name="wf")
    member = "wf-sess-parent-0"
    fixed_id = mgr._derive_avatar_session_id(member)
    messages = _rounds(2) + [ToolMessage(content="tool-result", tool_call_id="t1")]

    async def write_and_recover():
        # "Prior run": pre_run creates the agent store, then write a
        # state["context"] and commit it to the checkpointer. The save card's id
        # is ``{team}_{member}`` (matching derive_member_spec's agent_id), as the
        # real avatar would persist — so recovery exercises the true key match.
        sess = create_agent_session(session_id=fixed_id, card=AgentCard(id=f"t_{member}", name=member))
        await sess.pre_run()
        sess.update_state(
            {
                "context": {
                    "default_context_id": {"messages": messages},
                }
            }
        )
        await sess.commit()
        # "Fully-hit resume": recover by member name only (no avatar built).
        return await mgr._persisted_messages(member)

    recovered = asyncio.run(write_and_recover())
    assert recovered is not None
    assert len(recovered) == 5  # 2 rounds (4 msgs) + 1 ToolMessage
    assert any(isinstance(m, ToolMessage) for m in recovered)


def test_capture_fork_recovers_from_checkpointer_when_no_live_avatar():
    """capture_fork on a parent with no live avatar (fully-hit resume) recovers
    the persisted context (incl. ToolMessage) instead of degrading to None."""
    from openjiuwen.core.foundation.llm.schema.message import ToolMessage
    from openjiuwen.core.session.agent import create_agent_session
    from openjiuwen.core.single_agent import AgentCard

    mgr = _mgr(workflow_name="wf")
    member = "wf-sess-parent-0"
    fixed_id = mgr._derive_avatar_session_id(member)
    messages = _rounds(2) + [ToolMessage(content="tool-result", tool_call_id="t1")]

    async def scenario():
        # Save with the real team-prefixed card id (agent_id), as the avatar
        # would persist; recovery goes through capture_fork → _persisted_messages.
        sess = create_agent_session(session_id=fixed_id, card=AgentCard(id=f"t_{member}", name=member))
        await sess.pre_run()
        sess.update_state({"context": {"default_context_id": {"messages": messages}}})
        await sess.commit()
        # No live session in the manager's table — the parent was never rebuilt.
        return await mgr.capture_fork(member, keep_rounds=None, fork_mode="full")

    data = asyncio.run(scenario())
    assert data is not None
    assert len(data["messages"]) == 5
    # The persisted ToolMessage survives the encode/decode round-trip.
    from openjiuwen.core.session.vcs.codec import decode_message

    decoded = [decode_message(d) for d in data["messages"]]
    assert any(isinstance(m, ToolMessage) for m in decoded)


# ---------------------------------------------------------------------------
# run-prefixed session member names + external (agent_type) sessions
# ---------------------------------------------------------------------------


def test_session_member_names_carry_the_run_prefix():
    mgr = AvatarSessionManager(run_id="wf_abc-123")
    first = mgr._next_member_name("agent", {"label": "Coder"})
    second = mgr._next_member_name("agent", {"label": "Coder"})
    human = mgr._next_member_name("human", {"label": "Host"})
    assert first == "wf-abc-123-sess-coder-0"
    assert second == "wf-abc-123-sess-coder-1"
    assert human == "wf-abc-123-human-host-2"
    assert AvatarSessionManager(run_id="other")._next_member_name("agent", {"label": "Coder"}) == "other-sess-coder-0"


class _FakeMemberSession:
    """Stands in for the child AgentSession a runtime (or the read path) owns."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = dict(state or {})
        self.commit_calls = 0
        self.post_run_calls = 0

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key: str):
        return self.state.get(key)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def pre_run(self) -> None:
        return None

    async def post_run(self) -> None:
        self.post_run_calls += 1


class _FakeTurns:
    """Stands in for ExternalMemberTurns: records lifecycle, answers canned turns."""

    instances: list["_FakeTurns"] = []

    def __init__(self, *, resume: bool, require: bool, has_pointer: bool = True, seed: dict | None = None) -> None:
        self.resume, self.require, self.has_pointer = resume, require, has_pointer
        self.seed = seed
        self.pointer: str | None = None  # what the provider reports as its session/thread id
        self.member_session = _FakeMemberSession()
        self.started = 0
        self.stopped = 0
        self.aborted = 0
        self.prompts: list[tuple[str, dict | None]] = []
        self.model: str | None = None  # what the CLI reported this member runs on
        self.activity_binds: list[tuple] = []  # recorded bind_activity_sink identity tuples
        _FakeTurns.instances.append(self)

    provider_name = "codex"

    def bind_activity_sink(self, sink, *, agent_id=None, phase=None, label=None, correlation_id=None):
        self.activity_binds.append((agent_id, phase, label, correlation_id))

    @property
    def session_id(self) -> str | None:
        return self.pointer

    @property
    def reported_model(self) -> str | None:
        return self.model

    async def start(self, team_session):
        if self.resume and self.require and not self.has_pointer:
            raise HarnessProtocolError("no checkpoint pointer")
        self.started += 1

    async def run_turn(self, prompt, *, schema_json=None):
        self.prompts.append((prompt, schema_json))
        return ExternalTurnOutcome(
            text=f"ext:{prompt}",
            structured={"v": 1} if schema_json else None,
            tokens=7,
            marker=f"mk{len(self.prompts)}",
        )

    async def abort(self):
        self.aborted += 1

    async def stop(self):
        self.stopped += 1


def _pool(*names: str) -> SimpleNamespace:
    """Mirror test_external_model.py's fake team backend: get_model_pool only."""
    return SimpleNamespace(
        get_model_pool=lambda: [
            SimpleNamespace(
                model_name=name,
                api_key="k1",
                api_base_url="https://api.jiuwen.example/v1",
                api_provider="deepseek",
            )
            for name in names
        ],
    )


def _external_mgr(
    monkeypatch,
    *,
    has_pointer: bool = True,
    team_backend: Any = None,
    progress_sink: Any = None,
) -> AvatarSessionManager:
    _FakeTurns.instances.clear()
    mgr = AvatarSessionManager(
        team_name="t",
        run_id="r1",
        external_cli_agents=(ExternalCliAgentSpec(cli_agent="codex"),),
        gate_agent_type=lambda name: ExternalCliAgentSpec(cli_agent=name),
        external_cwd=lambda member, entry: None,
        team_backend=team_backend,
        progress_sink=progress_sink,
    )

    async def _build(state, *, resume, require, seed_checkpoint=None, opts=None):
        return _FakeTurns(resume=resume, require=require, has_pointer=has_pointer, seed=seed_checkpoint)

    monkeypatch.setattr(mgr, "_build_external_turns", _build)
    return mgr


def test_external_session_first_turn_starts_fresh_and_reuses_runtime(monkeypatch):
    mgr = _external_mgr(monkeypatch)
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    assert _FakeTurns.instances == []  # nothing launched until the first turn
    first = asyncio.run(mgr.send_turn(sid, "one", {"label": "c", "agent_type": "codex"}, None, history=()))
    second = asyncio.run(
        mgr.send_turn(
            sid, "two", {"label": "c", "agent_type": "codex"}, {"type": "object"},
            history=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "ext:one"}],
        )
    )
    turns = _FakeTurns.instances
    assert len(turns) == 1 and turns[0].resume is False and turns[0].started == 1
    assert first.text == "ext:one" and first.tokens == 7
    assert second.structured == {"v": 1}
    assert [p for p, _ in turns[0].prompts] == ["one", "two"]
    asyncio.run(mgr.close_session(sid))
    assert turns[0].stopped == 1


def test_external_session_rebinds_activity_sink_per_turn(monkeypatch):
    """Turn 2 rebinds the runtime's sink with turn 2's correlation id; turn 1's
    does not stick (correlation ids are per-turn)."""
    sink = object()
    mgr = _external_mgr(monkeypatch, progress_sink=sink)
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    opts = {"label": "c", "agent_type": "codex", "agent_id": "n1", "phase": "run"}
    first = asyncio.run(mgr.send_turn(sid, "one", opts, None, history=(), correlation_id="corr1"))
    second = asyncio.run(mgr.send_turn(sid, "two", opts, None, history=(), correlation_id="corr2"))
    assert first.text == "ext:one" and second.text == "ext:two"
    turns = _FakeTurns.instances
    assert len(turns) == 1
    assert turns[0].activity_binds == [
        ("n1", "run", "c", "corr1"),
        ("n1", "run", "c", "corr2"),
    ]
    # Turn 1's id is gone once turn 2 ran (rebind, not accumulate).
    assert turns[0].activity_binds[-1][3] == "corr2"


def test_external_session_first_turn_resolves_model_plan_before_builder(monkeypatch):
    """The session path plans the model where the builder is invoked: a builtin
    pick reaches build_cli_workflow_runtime as the endpointless config."""
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel

    builtin = ExternalCliBuiltinModel(name="gpt-5-codex", efforts=["medium"], default_effort="medium")
    entry = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[builtin])
    captured: dict = {}

    class _RecTurns:
        def __init__(self, runtime, *, member_name, auth_hint_models=None):
            captured["member"] = member_name

        member_session = _FakeMemberSession()
        session_id = None

        async def start(self, team_session):
            return None

        async def run_turn(self, prompt, *, schema_json=None):
            return ExternalTurnOutcome(text="ext", structured=None, tokens=7, marker="mk1")

        async def stop(self):
            return None

    async def _fake_build(**kwargs: Any) -> object:
        captured["build"] = dict(kwargs)
        return object()

    monkeypatch.setattr(asb_mod, "ExternalMemberTurns", _RecTurns)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.external.cli_agent.spawn.build_cli_workflow_runtime",
        _fake_build,
    )

    mgr = AvatarSessionManager(
        team_name="t",
        run_id="r1",
        external_cli_agents=(entry,),
        gate_agent_type=lambda name: entry,
        external_cwd=lambda member, e: None,
    )
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    result = asyncio.run(
        mgr.send_turn(
            sid,
            "one",
            {"label": "c", "agent_type": "codex", "builtin_model": "gpt-5-codex"},
            None,
            history=(),
        )
    )
    assert result.text == "ext"
    cfg = captured["build"]["model_config"]
    assert cfg is not None
    assert cfg.model == "gpt-5-codex" and cfg.effort == "medium"
    assert cfg.provider is None and cfg.api_base is None and cfg.api_key is None


def test_external_session_model_pool_miss_raises_before_builder(monkeypatch):
    """A session turn with an unresolvable model hint fails non-retryably before
    the builder is ever invoked."""
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel

    builtin = ExternalCliBuiltinModel(name="gpt-5-codex", efforts=["medium"], default_effort="medium")
    entry = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[builtin])
    build_calls: list = []

    async def _fake_build(**kwargs: Any) -> object:
        build_calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(
        "openjiuwen.agent_teams.external.cli_agent.spawn.build_cli_workflow_runtime",
        _fake_build,
    )

    mgr = AvatarSessionManager(
        team_name="t",
        run_id="r1",
        external_cli_agents=(entry,),
        gate_agent_type=lambda name: entry,
        external_cwd=lambda member, e: None,
        team_backend=_pool("deepseek-v4-flash"),
    )
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    with pytest.raises(BackendError) as exc:
        asyncio.run(
            mgr.send_turn(
                sid,
                "one",
                {"label": "c", "agent_type": "codex", "model": "X"},
                None,
                history=(),
            )
        )
    assert exc.value.retryable is False
    assert "deepseek-v4-flash" in str(exc.value)
    assert build_calls == []


def test_external_session_cold_resume_with_pointer_resumes(monkeypatch):
    mgr = _external_mgr(monkeypatch, has_pointer=True)
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    result = asyncio.run(mgr.send_turn(sid, "next", {"label": "c", "agent_type": "codex"}, None, history=history))
    assert result.text == "ext:next"
    assert _FakeTurns.instances[0].resume is True and _FakeTurns.instances[0].started == 1


def test_external_session_abort_all_aborts_live_runtimes(monkeypatch):
    mgr = _external_mgr(monkeypatch)
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    asyncio.run(mgr.send_turn(sid, "one", {"label": "c", "agent_type": "codex"}, None, history=()))
    asyncio.run(mgr.abort_all())
    assert _FakeTurns.instances[0].aborted == 1


def test_human_session_ignores_agent_type():
    mgr = AvatarSessionManager(
        external_cli_agents=(ExternalCliAgentSpec(cli_agent="codex"),),
        gate_agent_type=lambda name: ExternalCliAgentSpec(cli_agent=name),
    )
    with pytest.raises(BackendError, match="human"):
        asyncio.run(mgr.open_session(kind="human", instructions=None, opts={"label": "h", "agent_type": "codex"}))


def test_external_session_start_failure_stops_runtime(monkeypatch):
    """A failed external session start must call stop() to prevent a session leak."""
    mgr = _external_mgr(monkeypatch, has_pointer=False)
    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts={"label": "c", "agent_type": "codex"}))
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    with pytest.raises(HarnessProtocolError):
        asyncio.run(mgr.send_turn(sid, "next", {"label": "c", "agent_type": "codex"}, None, history=history))
    # stop() must be called so the runtime is not leaked even though start() failed.
    assert _FakeTurns.instances[0].stopped == 1


def test_external_fork_refuses_pointerless_and_invalid_requests(monkeypatch):
    """capture_fork refuses a session without a provider pointer (never ran a
    turn, row already gone) and rejects modes / keep_rounds the provider
    does not support — no child runtime is touched either way."""
    mgr = _external_mgr(monkeypatch)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await mgr.close_session(sid)
        # Row is gone, but _external_provider remembers this was a codex session.
        with pytest.raises(BackendError, match="no provider pointer"):
            await mgr.capture_fork(sid, keep_rounds=None, fork_mode="full")

        live = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await _run_two_turns(mgr, live)
        _FakeTurns.instances[-1].pointer = "thr_p"
        with pytest.raises(BackendError, match="after"):
            await mgr.capture_fork(live, keep_rounds=1, fork_mode="after")
        with pytest.raises(BackendError, match="keep_rounds"):
            await mgr.capture_fork(live, keep_rounds=5, fork_mode="before")
        # Only the live parent's runtime exists (the pointerless session never
        # ran a turn, so it built none); no child runtime was touched.
        assert len(_FakeTurns.instances) == 1

    asyncio.run(_body())


# ---------------------------------------------------------------------------
# external fork: capture credentials, persist markers, materialise on first turn
# ---------------------------------------------------------------------------


def _turn_opts(label="c"):
    return {"label": label, "agent_type": "codex"}


async def _run_two_turns(mgr, sid):
    await mgr.send_turn(sid, "one", _turn_opts(), None, history=())
    await mgr.send_turn(sid, "two", _turn_opts(), None,
                        history=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "ext:one"}])


def test_external_fork_captures_credentials_from_live_parent(monkeypatch):
    mgr = _external_mgr(monkeypatch)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await _run_two_turns(mgr, sid)
        _FakeTurns.instances[0].pointer = "thr_p"  # what runtime.session_id reports after start
        data = await mgr.capture_fork(sid, keep_rounds=1, fork_mode="before")
        creds = ForkCredentials.from_fork_data(data)
        assert creds == ForkCredentials(provider="codex", parent_member=sid, parent_pointer="thr_p",
                                        mode="before", cut_marker="mk1", keep_rounds=1)
        full = ForkCredentials.from_fork_data(await mgr.capture_fork(sid, keep_rounds=None, fork_mode="full"))
        assert full.cut_marker is None and full.mode == "full"

    asyncio.run(_body())


def test_external_child_materialises_on_first_turn(monkeypatch):
    mgr = _external_mgr(monkeypatch)
    materialised: list = []

    async def _fake_materialise(creds, *, entry, cwd):
        materialised.append(creds)
        return "thr_child"

    monkeypatch.setattr(asb_mod, "materialise_external_fork", _fake_materialise)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await _run_two_turns(mgr, sid)
        _FakeTurns.instances[0].pointer = "thr_p"
        data = await mgr.capture_fork(sid, keep_rounds=2, fork_mode="before")

        child = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts("k"), fork_data=data)
        assert materialised == []  # nothing until the first turn
        result = await mgr.send_turn(child, "go", _turn_opts("k"), None, history=[{"role": "user", "content": "x"}])
        assert result.text == "ext:go"
        assert materialised[0].cut_marker == "mk2"
        child_turns = _FakeTurns.instances[-1]
        assert child_turns.resume is True and child_turns.require is True
        assert child_turns.seed == {"thread_id": "thr_child"}

    asyncio.run(_body())


def test_external_markers_persist_and_fork_works_without_live_parent(monkeypatch):
    mgr = _external_mgr(monkeypatch)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await mgr.send_turn(sid, "one", _turn_opts(), None, history=())
        # The provider hands out its pointer once the runtime is started.
        _FakeTurns.instances[0].pointer = "thr_p"
        await mgr.send_turn(sid, "two", _turn_opts(), None,
                            history=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "ext:one"}])
        saved = dict(mgr._persisted_external.get(sid) or {})
        await mgr.close_session(sid)
        assert saved["markers"] == ["mk1", "mk2"] and saved["pointer"] == "thr_p"
        data = await mgr.capture_fork(sid, keep_rounds=1, fork_mode="before")
        assert ForkCredentials.from_fork_data(data).cut_marker == "mk1"

    asyncio.run(_body())


def test_external_failed_turn_leaves_no_marker(monkeypatch):
    """A failed turn records nothing: engine retries stay inside one round, so a hole would misalign rounds."""
    mgr = _external_mgr(monkeypatch)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await mgr.send_turn(sid, "one", _turn_opts(), None, history=())
        turns = _FakeTurns.instances[0]
        turns.pointer = "thr_p"

        async def _boom(prompt, *, schema_json=None):
            raise BackendError("boom", tokens=1)

        turns.run_turn = _boom
        with pytest.raises(BackendError, match="boom"):
            await mgr.send_turn(sid, "two", _turn_opts(), None, history=())
        assert mgr._persisted_external[sid]["markers"] == ["mk1"]
        with pytest.raises(BackendError, match="keep_rounds"):
            await mgr.capture_fork(sid, keep_rounds=2, fork_mode="before")
        creds = ForkCredentials.from_fork_data(await mgr.capture_fork(sid, keep_rounds=1, fork_mode="before"))
        assert creds.cut_marker == "mk1"

    asyncio.run(_body())


def test_external_member_is_registered_on_name_reservation(monkeypatch):
    """A fully-hit parent never reaches open_session, so the name reservation must register it."""
    mgr = _external_mgr(monkeypatch)

    async def _body():
        name = await mgr.ensure_member_name(kind="agent", opts=_turn_opts())
        mgr._persisted_external[name] = {"markers": ["mk1"], "pointer": "thr_p"}
        creds = ForkCredentials.from_fork_data(await mgr.capture_fork(name, keep_rounds=1, fork_mode="before"))
        assert creds.provider == "codex" and creds.cut_marker == "mk1"

    asyncio.run(_body())


def test_external_child_with_its_own_pointer_resumes_instead_of_re_forking(monkeypatch):
    """A partially-hit child already has a branch; a second fork would strand its history."""
    mgr = _external_mgr(monkeypatch)
    materialised: list = []

    async def _fake_materialise(creds, *, entry, cwd):
        materialised.append(creds)
        return "thr_child"

    monkeypatch.setattr(asb_mod, "materialise_external_fork", _fake_materialise)

    async def _body():
        sid = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts())
        await _run_two_turns(mgr, sid)
        _FakeTurns.instances[0].pointer = "thr_p"
        data = await mgr.capture_fork(sid, keep_rounds=2, fork_mode="before")
        child = await mgr.open_session(kind="agent", instructions=None, opts=_turn_opts("k"), fork_data=data)
        mgr._persisted_external[child] = {"markers": ["c1"], "pointer": "thr_child_prev"}

        await mgr.send_turn(child, "go", _turn_opts("k"), None, history=[{"role": "user", "content": "x"}])
        assert materialised == []
        child_turns = _FakeTurns.instances[-1]
        assert child_turns.resume is True and child_turns.require is True and child_turns.seed is None

    asyncio.run(_body())


# ---------------------------------------------------------------------------
# live activity: session avatar rail (Task 2)
# ---------------------------------------------------------------------------


def test_session_avatar_rail_emits_paired_activity_with_agent_id_and_correlation(monkeypatch):
    """A session avatar's rail forwards tool activity as paired AGENT_ACTIVITY
    events, tagged with the session node key and the CURRENT turn's correlation id."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import SwarmflowActivityRail
    from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    events: list = []
    base = DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[])
    mgr = AvatarSessionManager(worker_base_spec=base, team_name="t", language="en", progress_sink=events.append)
    agent_id = '[["call", 0]]'
    opts = {"label": "sre", "agent_id": agent_id, "phase": "review"}

    sid = asyncio.run(mgr.open_session(kind="agent", instructions=None, opts=opts))
    asyncio.run(mgr.send_turn(sid, "first", opts, None, correlation_id="review:sre:0"))

    rail = next(r for r in harnesses[0].rails if isinstance(r, SwarmflowActivityRail))

    class _Ctx:
        inputs = ToolCallInputs(
            tool_call=SimpleNamespace(id="call-9"),
            tool_name="write_file",
            tool_args={"path": "a.txt"},
        )
        exception = None

    asyncio.run(rail.before_tool_call(_Ctx()))
    ctx = _Ctx()
    ctx.inputs.tool_result = "wrote a.txt"
    asyncio.run(rail.after_tool_call(ctx))

    first = [e for e in events if e.kind == ProgressKind.AGENT_ACTIVITY]
    assert [e.activity_type for e in first] == ["tool_call", "tool_result"]
    assert all(e.agent_id == agent_id for e in first)
    assert all(e.correlation_id == "review:sre:0" for e in first)  # matches the turn's started/completed id
    assert all(e.phase == "review" and e.label == "sre" for e in first)
    assert all(e.tool_call_id == "call-9" and e.tool_name == "write_file" for e in first)
    assert first[0].content == '{"path": "a.txt"}'
    assert first[1].content == "wrote a.txt"
    assert first[0].message == "tool: write_file"

    # A later turn's tool activity carries THAT turn's correlation id.
    events.clear()
    asyncio.run(mgr.send_turn(sid, "second", opts, None, correlation_id="review:sre:1"))
    asyncio.run(rail.before_tool_call(_Ctx()))
    ctx = _Ctx()
    ctx.inputs.tool_result = "again"
    asyncio.run(rail.after_tool_call(ctx))
    second = [e for e in events if e.kind == ProgressKind.AGENT_ACTIVITY]
    assert len(second) == 2 and all(e.correlation_id == "review:sre:1" for e in second)




def test_agent_session_avatar_starts_under_its_own_trajectory_subject(monkeypatch):
    """The avatar's supervisor starts under a ``team_member`` subject named by its label."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.harness.execution_subject import current_execution_subject

    start_subjects: list = []

    class _SubjectRecordingHarness(_FakeHarness):
        async def start(self, *, team_session=None) -> None:
            start_subjects.append(current_execution_subject())
            await super().start(team_session=team_session)

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        h = _SubjectRecordingHarness()
        h.member_name = member_name
        h.spec = agent_spec
        return h

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)

    async def scenario():
        mgr = _mgr(session_id="product-sess")
        sid = await mgr.open_session(kind="agent", instructions="", opts={"label": "sre"})
        await mgr.send_turn(sid, "first", {"label": "sre"}, None)
        await mgr.aclose()
        return sid

    sid = asyncio.run(scenario())

    subject = start_subjects[0]
    assert subject.subject_id == f"team-member:product-sess:t:{sid}"
    assert subject.display_name == "sre"
    assert subject.kind == "team_member"
    assert subject.session_id == "product-sess"
    assert current_execution_subject() is None


def test_agent_session_picks_expert_spec_and_prepends_persona(monkeypatch):
    """An agent_template session derives from the expert spec; its system prompt is
    persona + blank line + the session role prompt (+ instructions)."""
    from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import _SESSION_SYS_PROMPT_AGENT
    from openjiuwen.agent_teams.workflow.backends.expert_specs import SwarmflowExpertEntry

    harnesses: list = []
    _patch_build(monkeypatch, harnesses)
    expert_spec = DeepAgentSpec(tools=[], agent_template_spec={"agent_card": {"id": "clip", "name": "clip"}})
    entry = SwarmflowExpertEntry("clip", "视频镜头师", "PERSONA", expert_spec, None)

    def _gate(opts):
        return entry if opts.get("agent_template") == "clip" else None

    async def scenario():
        mgr = _mgr(gate_agent_template=_gate)
        await mgr.open_session(kind="agent", instructions="be brief", opts={"label": "shot", "agent_template": "clip"})
        await mgr.open_session(kind="agent", instructions=None, opts={"label": "plain"})

    asyncio.run(scenario())
    assert harnesses[0].spec.agent_template_spec == {"agent_card": {"id": "clip", "name": "clip"}}
    assert harnesses[0].spec.system_prompt == f"PERSONA\n\n{_SESSION_SYS_PROMPT_AGENT}\n\nbe brief"
    assert harnesses[1].spec.system_prompt == _SESSION_SYS_PROMPT_AGENT
