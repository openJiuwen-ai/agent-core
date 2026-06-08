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
        self.removed: list[str] = []
        self.disposed = False
        self._round = 0
        self.interrupt_next = False

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

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)
        self.tools = [t for t in self.tools if getattr(t, "card", None) is None or t.card.name != name]

    async def dispose(self) -> None:
        self.disposed = True


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
