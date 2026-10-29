# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end team events on the real ExtensionEventBus.

Covers the host wiring order (event types + team ops handler, then one
``ops.build``), the CORE results of both events, short-circuit semantics
and the pattern filter at the chain entry.
"""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.events import (
    TEAM_BUILD_POLICY,
    TEAM_MONITOR_EVENT,
    build_team_policy_sections,
    register_team_event_types,
)
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
    TeamPolicyBuildInput,
    TeamPolicyBuildOutput,
    TeamPolicyContext,
)
from openjiuwen.agent_teams.extensions.ops import bind_policy_context_scope, bind_policy_loader_scope
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel, SessionContext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.extensions.ops_build_handlers import team as team_ops


class _NullResolver:
    """Resolver double: no team is accessible (not used by these tests)."""

    async def resolve(self, session_id: str | None, team_name: str):
        return None


async def _ready_bus() -> ExtensionEventBus:
    """Create a bus with the team events and ops namespace built."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, _NullResolver())
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _monitor_input() -> TeamMonitorEventInput:
    return TeamMonitorEventInput(
        team_name="research-team",
        member_name="researcher",
        monitor_event=MonitorEvent(
            event_type=MonitorEventType.TASK_CREATED,
            team_name="research-team",
            member_name="researcher",
            task_id="t1",
            timestamp=1,
        ),
    )


@pytest.mark.asyncio
async def test_monitor_event_core_runs_without_handlers() -> None:
    """With no registered handler the chain lands in the CORE output."""
    bus = await _ready_bus()
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert isinstance(output, TeamMonitorEventOutput)


@pytest.mark.asyncio
async def test_monitor_event_user_short_circuit_returns_none() -> None:
    """A short-circuited notification chain returns None — no CORE run."""

    async def swallow(_event, _ops, _next_handler):
        return None

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_MONITOR_EVENT, swallow, match_pattern={})
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert output is None


@pytest.mark.asyncio
async def test_monitor_event_pattern_filters_at_chain_entry() -> None:
    """A handler whose pattern misses never runs; the CORE tail still does."""
    ran = False

    async def never_runs(_event, _ops, _next_handler):
        nonlocal ran
        ran = True
        return None

    bus = await _ready_bus()
    bus.register_event_handler(
        TEAM_MONITOR_EVENT,
        never_runs,
        match_pattern={"team_name": "other-team"},
    )
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert ran is False
    assert isinstance(output, TeamMonitorEventOutput)


@pytest.mark.asyncio
async def test_build_policy_core_reads_bound_context() -> None:
    """The CORE builds from the snapshot bound at the build entry."""
    bus = await _ready_bus()
    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
    )
    context = TeamPolicyContext(language="cn", hitt_enabled=True)
    with bind_policy_context_scope(context), bind_policy_loader_scope(load_template):
        output = await bus.trigger(
            Event(TEAM_BUILD_POLICY, build_input),
            SessionContext(session_id="s1"),
        )
    assert isinstance(output, TeamPolicyBuildOutput)
    assert {"team_role", "team_hitt"} <= {s.name for s in output.sections}


@pytest.mark.asyncio
async def test_build_policy_without_bound_context_fails() -> None:
    """A build outside the build entry's scope is an explicit error."""
    bus = await _ready_bus()
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    with pytest.raises(BaseError):
        await bus.trigger(
            Event(TEAM_BUILD_POLICY, build_input),
            SessionContext(session_id="s1"),
        )


@pytest.mark.level1
@pytest.mark.asyncio
async def test_build_policy_without_bound_loader_fails() -> None:
    """A bound context without a bound loader fails loudly, never falls back.

    Silently degrading to static framework templates would hand teams with
    evolved workspace templates the wrong sections without any signal.
    """
    bus = await _ready_bus()
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    with bind_policy_context_scope(TeamPolicyContext(language="cn")):
        with pytest.raises(BaseError):
            await bus.trigger(
                Event(TEAM_BUILD_POLICY, build_input),
                SessionContext(session_id="s1"),
            )


@pytest.mark.asyncio
async def test_build_policy_user_reads_same_snapshot_and_appends() -> None:
    """A USER handler extends the CORE sections and reads the same snapshot."""

    async def append_section(event, ops, next_handler):
        context = await ops.team.policy_context()
        assert context.language == "cn"
        output = await next_handler(event)
        assert isinstance(output, TeamPolicyBuildOutput)
        extra = PromptSection(name="business_extra", content={"cn": "extra"}, priority=50)
        return TeamPolicyBuildOutput(sections=(*output.sections, extra))

    bus = await _ready_bus()
    bus.register_event_handler(
        TEAM_BUILD_POLICY,
        append_section,
        match_pattern={"team_name": "research-team", "role": "teammate"},
        handler_level=HandlerLevel.USER,
    )
    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
    )
    with bind_policy_context_scope(TeamPolicyContext(language="cn")), bind_policy_loader_scope(
        load_template,
    ):
        output = await bus.trigger(
            Event(TEAM_BUILD_POLICY, build_input),
            SessionContext(session_id="s1"),
        )
    assert isinstance(output, TeamPolicyBuildOutput)
    assert "business_extra" in {s.name for s in output.sections}
    assert "team_role" in {s.name for s in output.sections}


# ---------------------------------------------------------------------------
# Event-level validators
# ---------------------------------------------------------------------------


@pytest.mark.level1
@pytest.mark.asyncio
async def test_invalid_input_raises_at_trigger_entry() -> None:
    """A wrong input type is an event-definition fault, not a match miss."""
    bus = await _ready_bus()
    with pytest.raises(BaseError):
        await bus.trigger(Event(TEAM_MONITOR_EVENT, "not-an-input"), SessionContext(session_id="s1"))


@pytest.mark.level1
@pytest.mark.asyncio
async def test_invalid_handler_output_is_skipped_chain_reaches_core() -> None:
    """A policy handler returning a wrong type is isolated; the CORE still builds."""

    async def bad_output(_event, _ops, _next_handler):
        return "not-a-policy-output"

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_BUILD_POLICY, bad_output, match_pattern={})
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    with bind_policy_context_scope(TeamPolicyContext(language="cn")), bind_policy_loader_scope(
        load_template,
    ):
        output = await bus.trigger(
            Event(TEAM_BUILD_POLICY, build_input),
            SessionContext(session_id="s1"),
        )
    assert isinstance(output, TeamPolicyBuildOutput)
    assert "team_role" in {s.name for s in output.sections}


@pytest.mark.level1
@pytest.mark.asyncio
async def test_monitor_short_circuit_none_passes_output_validator() -> None:
    """None stays a legal monitor-event outcome under the output validator."""

    async def swallow(_event, _ops, _next_handler):
        return None

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_MONITOR_EVENT, swallow, match_pattern={})
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert output is None


@pytest.mark.level1
@pytest.mark.asyncio
async def test_build_policy_uses_the_callers_loader_over_the_registered_default() -> None:
    """The entry binds the caller's loader; the registered one is a fallback.

    The caller-bound loader is how a team's workspace cache reaches the
    CORE even though registration happens before any team exists.
    """

    class _StubTemplate:
        def __init__(self, content: str) -> None:
            self.content = content

        def format(self, values: dict) -> str:
            del values
            return self.content

    def marker_loader(name: str, lang: str) -> _StubTemplate:
        del lang
        return _StubTemplate(f"<{name}-via-bound-loader>")

    bus = await _ready_bus()  # registered with the default framework loader
    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
    )
    output = await build_team_policy_sections(
        build_input,
        TeamPolicyContext(language="cn"),
        bus=bus,
        session_id="s1",
        loader=marker_loader,
    )
    role_section = next(s for s in output.sections if s.name == "team_role")
    assert "<teammate_policy-via-bound-loader>" in role_section.content["cn"]


@pytest.mark.level1
@pytest.mark.asyncio
async def test_handlers_see_the_call_bound_team() -> None:
    """Both entry points bind the triggering team for the call scope.

    Handlers may then omit ``team_name`` in team Ops queries — the query
    resolves to the team the event itself belongs to, never a guess. The
    binding belongs to the entries (dispatcher / build entry): a bare
    ``trigger`` binds nothing, which is the no-binding error path instead.
    """
    from openjiuwen.agent_teams.extensions.events import build_monitor_event_dispatcher
    from openjiuwen.agent_teams.extensions.ops import current_team_name

    seen_teams: list[str | None] = []

    async def record_team(_event, _ops, next_handler):
        seen_teams.append(current_team_name())
        return await next_handler(_event)

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_MONITOR_EVENT, record_team, match_pattern={})
    dispatcher = build_monitor_event_dispatcher(bus)
    await dispatcher(_monitor_input().monitor_event, "s1")

    bus.register_event_handler(TEAM_BUILD_POLICY, record_team, match_pattern={})
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    with bind_policy_context_scope(TeamPolicyContext(language="cn")):
        await build_team_policy_sections(
            build_input,
            TeamPolicyContext(language="cn"),
            loader=load_template,
            bus=bus,
            session_id="s1",
        )

    assert seen_teams == ["research-team", "research-team"]
    # The binding is call-scoped: it does not leak past the entry.
    assert current_team_name() is None
