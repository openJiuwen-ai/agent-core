# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-state delivery through a CLI-backed member runtime.

An external CLI member has no rail and no reachable context, so team state has
to travel inside the messages the runtime sends it. Two entry points share one
delivery path: ``send`` folds pending state into whatever is already going out,
and ``announce_team_context`` pushes it as a message of its own when a roster
event cannot wait for the next one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from openjiuwen.agent_teams.external.runtime import CliRuntimeBase
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TEAM_CONTEXT_STATE_KEY, TeamContextTracker


class _StubMemberSession:
    """Member AgentSession stand-in holding the delivery baseline."""

    def __init__(self) -> None:
        self.state: dict = {}
        self.commits = 0
        self.pre_run_calls = 0

    def get_state(self, key: str | None = None) -> Any:
        """Read one key out of the session state."""
        if key is None:
            return dict(self.state)
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        """Shallow-merge into the session state."""
        self.state.update(data)

    async def commit(self) -> None:
        """Record that the state was flushed."""
        self.commits += 1

    async def pre_run(self) -> None:
        """Record the restore hook the runtime awaits at start."""
        self.pre_run_calls += 1


class _StubTeamSession:
    """Team session stand-in vending one member session per agent id."""

    def __init__(self) -> None:
        self.created: dict[str, _StubMemberSession] = {}

    def create_agent_session(self, *, agent_id: str, share_stream_writer: bool = True) -> _StubMemberSession:
        """Return (and remember) this member's own session."""
        session = self.created.setdefault(agent_id, _StubMemberSession())
        return session


class _RecordingRuntime(CliRuntimeBase):
    """CLI runtime that records outgoing messages instead of driving a CLI."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sent: list[tuple[str, bool]] = []
        self.send_error: Exception | None = None

    async def _send_raw(self, text: str, *, immediate: bool = False) -> Any:
        """Record the message that would have reached the CLI."""
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((text, immediate))
        return None

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        """Yield no chunks."""
        if False:
            yield inputs

    async def steer(self, content: str) -> None:
        """Accept a steer no-op."""
        return None

    async def follow_up(self, content: str) -> None:
        """Accept a follow-up no-op."""
        return None

    async def _abort_turn(self) -> None:
        """Abort no-op."""
        return None

    async def aclose(self) -> None:
        """Close no-op."""
        return None


class _FakeBackend:
    """Minimal TeamBackend serving one team row and a mutable peer roster."""

    def __init__(self) -> None:
        self.team = SimpleNamespace(team_name="demo", display_name="Demo", desc="Ship it")
        self.members: list[SimpleNamespace] = [
            SimpleNamespace(member_name="dev1", display_name="Dev", desc="Coder", role="teammate"),
        ]
        self.team_mtime = 1
        self.members_mtime = 1

    async def get_team_updated_at(self) -> int:
        return self.team_mtime

    async def get_members_max_updated_at(self) -> int:
        return self.members_mtime

    async def get_member_updated_at_state(
        self, member_name: str, field: str
    ) -> tuple[int, bool]:
        """Single-member mtime probe the identity body's first-emit records.

        Returns a stable ``(mtime, present=True)`` so the probe does not
        re-fire between rounds — this fake exercises the CLI delivery path,
        not the prompt-evolution re-announce semantics.
        """
        return 1, True

    async def stamp_member_prompt_updated_at(self, member_name: str, ts: int) -> None:
        """No-op: the stable probe above never signals a blank field."""
        return None

    async def get_team_info(self):
        return self.team

    async def list_members(self):
        return list(self.members)

    async def get_member(self, member_name: str):
        if member_name == "claude-1":
            return SimpleNamespace(
                member_name="claude-1", display_name="Claude One", desc="", role="teammate", prompt=""
            )
        return next((m for m in self.members if m.member_name == member_name), None)

    def add_member(self, member_name: str, display_name: str) -> None:
        self.members.append(
            SimpleNamespace(member_name=member_name, display_name=display_name, desc="", role="teammate")
        )
        self.members_mtime += 1


def _make_runtime(backend: _FakeBackend | None = None) -> tuple[_RecordingRuntime, _FakeBackend]:
    """Build a recording runtime wired to a tracker over ``backend``."""
    backend = backend if backend is not None else _FakeBackend()
    runtime = _RecordingRuntime(
        member_name="claude-1",
        member_agent_id="demo_claude-1",
        team_context_tracker=TeamContextTracker(
            team_backend=backend,
            member_name="claude-1",
            role=TeamRole.TEAMMATE,
            display_name="Claude One",
            language="cn",
        ),
    )
    return runtime, backend


@pytest.mark.asyncio
@pytest.mark.level1
async def test_start_opens_the_member_session():
    runtime, _ = _make_runtime()
    team_session = _StubTeamSession()

    await runtime.start(team_session=team_session)

    assert "demo_claude-1" in team_session.created
    assert team_session.created["demo_claude-1"].pre_run_calls == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_start_without_a_team_session_is_tolerated():
    """A standalone runtime simply runs without persisted per-member state."""
    runtime, _ = _make_runtime()
    await runtime.start(team_session=None)
    await runtime.send("hello")
    assert runtime.sent == [("hello", False)]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_send_prepends_pending_state_to_the_message():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.send("please review the PR")

    assert len(runtime.sent) == 1
    text, _immediate = runtime.sent[0]
    assert text.endswith("please review the PR")
    assert "<team-context>" in text
    assert "你的 member_name: claude-1" in text
    assert "你的 display_name: Claude One" in text
    # Identity and team info share one <team-context>.
    assert text.count("<team-context>") == 1
    assert '<team-event kind="roster">' in text
    assert "member_name=dev1" in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_second_send_carries_nothing_extra():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.send("first")
    await runtime.send("second")

    assert runtime.sent[1][0] == "second"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_steer_and_follow_up_paths_also_carry_state():
    """Every flavour of send goes through the same single delivery path."""
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.send("go", immediate=True)

    text, immediate = runtime.sent[0]
    assert immediate is True
    assert "<team-context>" in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_announce_sends_state_as_its_own_message():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.announce_team_context()

    assert len(runtime.sent) == 1
    text, immediate = runtime.sent[0]
    assert immediate is False
    assert "<team-context>" in text
    assert '<team-event kind="roster">' in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_announce_is_a_no_op_when_nothing_is_new():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.announce_team_context()
    await runtime.announce_team_context()

    assert len(runtime.sent) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_announce_then_send_does_not_repeat_the_state():
    """The two entry points share one baseline, so neither double-delivers."""
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.announce_team_context()
    await runtime.send("carry on")

    assert runtime.sent[1][0] == "carry on"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_roster_change_is_announced_as_a_delta():
    runtime, backend = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())
    await runtime.announce_team_context()

    backend.add_member("dev2", "Newbie")
    await runtime.announce_team_context()

    text = runtime.sent[1][0]
    assert '<team-event kind="roster-change">' in text
    assert "[加入] member_name=dev2" in text
    assert "member_name=dev1" not in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_roster_messages_carry_the_announcement_note():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())

    await runtime.announce_team_context()

    text = runtime.sent[0][0]
    assert '<team-note kind="announcement-only">' in text
    assert "不要" in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_delivery_leaves_the_state_pending():
    runtime, _ = _make_runtime()
    await runtime.start(team_session=_StubTeamSession())
    runtime.send_error = BrokenPipeError("cli gone")

    with pytest.raises(BrokenPipeError):
        await runtime.send("hello")
    assert runtime.sent == []

    runtime.send_error = None
    await runtime.send("hello again")
    assert "<team-context>" in runtime.sent[0][0]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_baseline_is_persisted_and_survives_a_new_runtime():
    """A restarted runtime on the same session must not re-announce."""
    backend = _FakeBackend()
    runtime, _ = _make_runtime(backend)
    team_session = _StubTeamSession()
    await runtime.start(team_session=team_session)
    await runtime.announce_team_context()

    member_session = team_session.created["demo_claude-1"]
    assert member_session.state[TEAM_CONTEXT_STATE_KEY]["identity_emitted"] is True
    assert member_session.commits >= 1

    restarted, _ = _make_runtime(backend)
    await restarted.start(team_session=team_session)
    await restarted.announce_team_context()
    assert restarted.sent == []
