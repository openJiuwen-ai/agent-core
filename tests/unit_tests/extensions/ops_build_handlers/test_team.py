# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``team`` Ops namespace: queries, validation, visibility and paging."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.models import TeamPolicyContext
from openjiuwen.agent_teams.extensions.ops import (
    TaskDependencyInfo,
    TeamReadView,
    bind_policy_context_scope,
    bind_team_scope,
)
from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import Event, ExtensionEventBus, SessionContext
from openjiuwen.core.events.ops import EventHandlerOpsBuilder
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.extensions.ops_build_handlers import team as team_ops


class _FakeView(TeamReadView):
    """In-memory read view over one team."""

    def __init__(
        self,
        *,
        team_name: str = "research-team",
        hide_dm: bool = False,
        members: list[MemberInfo] | None = None,
        tasks: list[TaskInfo] | None = None,
        dependencies: list[TaskDependencyInfo] | None = None,
        messages: list[MessageInfo] | None = None,
    ) -> None:
        self.team_name = team_name
        self._hide_dm = hide_dm
        self._team_info = TeamInfo(
            team_name=team_name,
            display_name="Research",
            leader_member_name="leader",
            created=1,
        )
        self._members = members or []
        self._tasks = tasks or []
        self._dependencies = dependencies or []
        self._messages = messages or []

    @property
    def hide_dm(self) -> bool:
        """Whether direct messages are hidden for this caller."""
        return self._hide_dm

    async def team_info(self) -> TeamInfo:
        return self._team_info

    async def members(self, status: str | None = None) -> list[MemberInfo]:
        return [m for m in self._members if status is None or m.status == status]

    async def member(self, member_name: str) -> MemberInfo | None:
        return next((m for m in self._members if m.member_name == member_name), None)

    async def tasks(self, status: str | None = None) -> list[TaskInfo]:
        return [t for t in self._tasks if status is None or t.status == status]

    async def task(self, task_id: str) -> TaskInfo | None:
        return next((t for t in self._tasks if t.task_id == task_id), None)

    async def task_dependencies(self, task_id: str) -> list[TaskDependencyInfo]:
        return [d for d in self._dependencies if d.task_id == task_id]

    async def messages(self) -> list[MessageInfo]:
        return list(self._messages)

    async def message(self, message_id: str) -> MessageInfo | None:
        return next((m for m in self._messages if m.message_id == message_id), None)


class _FakeResolver:
    """Resolve views from a fixed map; unknown teams are inaccessible."""

    def __init__(self, views: dict[str, _FakeView]) -> None:
        self.views = views
        self.requests: list[tuple[str | None, str]] = []

    async def resolve(self, session_id: str | None, team_name: str):
        self.requests.append((session_id, team_name))
        return self.views.get(team_name)


def _member(member_name: str) -> MemberInfo:
    return MemberInfo(
        member_name=member_name,
        team_name="research-team",
        display_name=member_name,
        status="ready",
        mode="inprocess",
        role="teammate",
    )


def _task(task_id: str, team_name: str = "research-team") -> TaskInfo:
    return TaskInfo(task_id=task_id, team_name=team_name, title=task_id, content="work", status="pending")


def _message(
    message_id: str,
    *,
    timestamp: int,
    from_member_name: str = "leader",
    to_member_name: str | None = "researcher",
    broadcast: bool = False,
    team_name: str = "research-team",
) -> MessageInfo:
    return MessageInfo(
        message_id=message_id,
        team_name=team_name,
        from_member_name=from_member_name,
        to_member_name=to_member_name,
        content=f"body-{message_id}",
        timestamp=timestamp,
        broadcast=broadcast,
    )


async def _ops_for(
    resolver: _FakeResolver,
    *,
    session_id: str | None = "session-1",
) -> EventHandlerOpsBuilder:
    """Build a ready bus and return the ops view factory's builder."""
    bus = ExtensionEventBus()
    team_ops.register(bus, resolver)
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    return builder


def _session(session_id: str | None) -> SessionContext | None:
    return SessionContext(session_id=session_id) if session_id else None


async def _view_methods(resolver: _FakeResolver, session_id: str | None = "session-1"):
    builder = await _ops_for(resolver, session_id=session_id)
    ops = builder.build(None, _session(session_id))
    return ops.team


# ---------------------------------------------------------------------------
# Wiring and resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ops_build_adds_team_namespace_with_all_methods() -> None:
    """register + ops.build yields the nine documented team methods."""
    resolver = _FakeResolver({"research-team": _FakeView()})
    team = await _view_methods(resolver)
    for method in (
        "info",
        "members",
        "member",
        "tasks",
        "task",
        "task_dependencies",
        "messages",
        "message",
        "policy_context",
    ):
        assert hasattr(team, method)


@pytest.mark.asyncio
async def test_query_without_session_raises() -> None:
    """No session context means an explicit error, not a guess."""
    resolver = _FakeResolver({"research-team": _FakeView()})
    team = await _view_methods(resolver, session_id=None)
    with pytest.raises(BaseError):
        await team.info()


@pytest.mark.asyncio
async def test_query_without_team_name_or_binding_raises() -> None:
    """No bound team and no explicit name is an explicit error."""
    resolver = _FakeResolver({"research-team": _FakeView()})
    team = await _view_methods(resolver)
    with pytest.raises(BaseError):
        await team.info()


@pytest.mark.asyncio
async def test_bound_team_used_when_name_omitted() -> None:
    """The current-team binding resolves the omitted team_name."""
    resolver = _FakeResolver({"research-team": _FakeView()})
    team = await _view_methods(resolver)
    with bind_team_scope("research-team"):
        info = await team.info()
    assert info is not None
    assert info.team_name == "research-team"
    assert resolver.requests[-1] == ("session-1", "research-team")


@pytest.mark.asyncio
async def test_inaccessible_team_raises() -> None:
    """A team the resolver cannot serve is an explicit error."""
    resolver = _FakeResolver({})
    team = await _view_methods(resolver)
    with pytest.raises(BaseError):
        await team.info(team_name="research-team")


# ---------------------------------------------------------------------------
# Members, tasks and dependencies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_queries() -> None:
    """members lists the roster; member returns None for unknown names."""
    view = _FakeView(members=[_member("researcher"), _member("writer")])
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    roster = await team.members(team_name="research-team")
    assert [m.member_name for m in roster] == ["researcher", "writer"]
    assert await team.member("researcher", team_name="research-team") is not None
    assert await team.member("ghost", team_name="research-team") is None


@pytest.mark.asyncio
async def test_task_requires_team_ownership() -> None:
    """task rejects missing tasks and tasks of another team."""
    view = _FakeView(tasks=[_task("t1"), _task("t9", team_name="other-team")])
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    task_info = await team.task("t1", team_name="research-team")
    assert task_info.task_id == "t1"
    with pytest.raises(BaseError):
        await team.task("missing", team_name="research-team")
    with pytest.raises(BaseError):
        await team.task("t9", team_name="research-team")


@pytest.mark.asyncio
async def test_task_dependencies_direct_only_and_owned() -> None:
    """Dependencies stay direct; endpoints outside the team are errors."""
    view = _FakeView(
        tasks=[_task("t1"), _task("t2")],
        dependencies=[TaskDependencyInfo(task_id="t1", depends_on_task_id="t2", resolved=False)],
    )
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    dependencies = await team.task_dependencies("t1", team_name="research-team")
    assert dependencies == [TaskDependencyInfo(task_id="t1", depends_on_task_id="t2", resolved=False)]

    broken = _FakeView(
        tasks=[_task("t1")],
        dependencies=[TaskDependencyInfo(task_id="t1", depends_on_task_id="gone", resolved=False)],
    )
    resolver_broken = _FakeResolver({"research-team": broken})
    team_broken = await _view_methods(resolver_broken)
    with pytest.raises(BaseError):
        await team_broken.task_dependencies("t1", team_name="research-team")


@pytest.mark.asyncio
async def test_task_dependencies_empty_when_none() -> None:
    """A task without dependencies yields an empty collection."""
    view = _FakeView(tasks=[_task("t1")])
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    assert await team.task_dependencies("t1", team_name="research-team") == []


# ---------------------------------------------------------------------------
# Messages: filters, ordering, paging, visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_orders_desc_and_pages_by_cursor() -> None:
    """Pages are timestamp/message_id descending and continue by cursor."""
    view = _FakeView(
        messages=[
            _message("m1", timestamp=1),
            _message("m2", timestamp=3),
            _message("m3", timestamp=3),
            _message("m4", timestamp=2),
        ],
    )
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    page_one = await team.messages(limit=2, team_name="research-team")
    assert [m.message_id for m in page_one.items] == ["m3", "m2"]
    assert page_one.next_cursor is not None
    page_two = await team.messages(limit=2, cursor=page_one.next_cursor, team_name="research-team")
    assert [m.message_id for m in page_two.items] == ["m4", "m1"]
    assert page_two.next_cursor is None


@pytest.mark.asyncio
async def test_messages_filters_combine_with_and() -> None:
    """from/to/broadcast filters intersect; none of them widens visibility."""
    view = _FakeView(
        messages=[
            _message("m1", timestamp=1, from_member_name="leader", to_member_name="researcher"),
            _message("m2", timestamp=2, from_member_name="writer", to_member_name="researcher"),
            _message("m3", timestamp=3, from_member_name="leader", to_member_name=None, broadcast=True),
        ],
    )
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    filtered = await team.messages(
        from_member_name="leader",
        to_member_name="researcher",
        team_name="research-team",
    )
    assert [m.message_id for m in filtered.items] == ["m1"]
    broadcast_only = await team.messages(broadcast=True, team_name="research-team")
    assert [m.message_id for m in broadcast_only.items] == ["m3"]


@pytest.mark.asyncio
async def test_messages_rejects_bad_limit_and_mismatched_cursor() -> None:
    """Out-of-range limits and foreign cursors are explicit errors."""
    view = _FakeView(messages=[_message("m1", timestamp=1)])
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    with pytest.raises(BaseError):
        await team.messages(limit=0, team_name="research-team")
    with pytest.raises(BaseError):
        await team.messages(limit=201, team_name="research-team")
    with pytest.raises(BaseError):
        await team.messages(cursor="not-a-cursor", team_name="research-team")

    page = await team.messages(from_member_name="leader", team_name="research-team")
    assert page.next_cursor is None  # single message: no continuation

    paged = await team.messages(team_name="research-team", limit=1)
    if paged.next_cursor is None:
        return
    with pytest.raises(BaseError):
        await team.messages(cursor=paged.next_cursor, from_member_name="leader", team_name="research-team")


@pytest.mark.asyncio
async def test_messages_hide_dm_shows_broadcast_only() -> None:
    """Under hide-dm only broadcast traffic is visible; DM queries are empty."""
    view = _FakeView(
        hide_dm=True,
        messages=[
            _message("m1", timestamp=1),
            _message("m2", timestamp=2, to_member_name=None, broadcast=True),
        ],
    )
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    page = await team.messages(team_name="research-team")
    assert [m.message_id for m in page.items] == ["m2"]
    dm_page = await team.messages(to_member_name="researcher", team_name="research-team")
    assert dm_page.items == ()


@pytest.mark.asyncio
async def test_message_validates_ownership_and_visibility() -> None:
    """Single-message queries cannot bypass ownership or hide-dm."""
    view = _FakeView(
        messages=[
            _message("m1", timestamp=1),
            _message("m9", timestamp=2, team_name="other-team"),
        ],
    )
    resolver = _FakeResolver({"research-team": view})
    team = await _view_methods(resolver)
    found = await team.message("m1", team_name="research-team")
    assert found.message_id == "m1"
    with pytest.raises(BaseError):
        await team.message("missing", team_name="research-team")
    with pytest.raises(BaseError):
        await team.message("m9", team_name="research-team")

    hidden = _FakeView(hide_dm=True, messages=[_message("m1", timestamp=1)])
    resolver_hidden = _FakeResolver({"research-team": hidden})
    team_hidden = await _view_methods(resolver_hidden)
    with pytest.raises(BaseError):
        await team_hidden.message("m1", team_name="research-team")


# ---------------------------------------------------------------------------
# policy_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_context_reads_the_build_binding() -> None:
    """policy_context returns the bound snapshot and errors without one."""
    resolver = _FakeResolver({})
    team = await _view_methods(resolver)
    with pytest.raises(BaseError):
        await team.policy_context()
    context = TeamPolicyContext(language="cn")
    with bind_policy_context_scope(context):
        assert await team.policy_context() is context
