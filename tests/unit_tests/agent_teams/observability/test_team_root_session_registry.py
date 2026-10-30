# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The team root must be findable from a teammate's own task."""

from __future__ import annotations

import asyncio
import contextvars

import pytest

from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.observability.span_context import (
    get_or_create_team_span,
    get_team_span,
    remove_team_span,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.span_context import (
    get_root_span,
    get_session_root_span,
    reset_state,
    set_current_session_id,
    set_root_span,
)
from tests.test_logger import logger

_SESSION_ID = "team-root-registry-session"
_TEAM_NAME = "registry-team"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_teammate_task_finds_the_team_root_by_session() -> None:
    runtime = ObservabilityRuntime()
    runtime.initialize(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    set_session_id(_SESSION_ID)
    set_current_session_id(_SESSION_ID)
    team_span = get_or_create_team_span(_TEAM_NAME, runtime.get_tracer("team-root-test"))
    assert team_span is not None

    async def teammate() -> object:
        # An in-process teammate runs in a task of its own; a fresh context
        # carries none of the spawner's ContextVars. Its rail looks the run
        # root up by the session id its callback context states.
        return get_root_span(session_id=_SESSION_ID)

    try:
        found = await asyncio.create_task(teammate(), context=contextvars.Context())
        logger.info("root resolved from a teammate task: {}", found)
        assert found is team_span
    finally:
        removed = remove_team_span()
        if removed is not None and removed.is_recording():
            removed.end()
        runtime.shutdown()
        reset_state()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_an_unbound_session_still_registers_the_root() -> None:
    runtime = ObservabilityRuntime()
    runtime.initialize(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    # Neither context var is bound: the caller is the only one who knows the
    # session, which is the shape of the interact path.
    set_session_id("")
    set_current_session_id("")
    team_span = get_or_create_team_span(
        _TEAM_NAME,
        runtime.get_tracer("team-root-test"),
        session_id=_SESSION_ID,
    )
    assert team_span is not None
    try:

        async def teammate() -> object:
            return get_root_span(session_id=_SESSION_ID)

        assert await asyncio.create_task(teammate(), context=contextvars.Context()) is team_span
    finally:
        removed = remove_team_span()
        if removed is not None and removed.is_recording():
            removed.end()
        runtime.shutdown()
        reset_state()


class _StubLeader:
    """The little a leader has to be for the runner to attach a root to it."""

    def __init__(self, team_name: str) -> None:
        self.team_name = team_name
        self.listeners: list[object] = []

    def add_event_listener(self, listener: object) -> None:
        self.listeners.append(listener)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_the_runner_registers_the_root_under_the_session_it_was_given() -> None:
    from openjiuwen.agent_teams.observability.setup import init_observability, shutdown_observability
    from openjiuwen.core.runner.team_runner import _TeamRunnerMixin

    init_observability(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    # The interact path binds neither context var before attaching.
    set_session_id("")
    set_current_session_id("")
    try:
        _TeamRunnerMixin._maybe_attach_observability(_StubLeader(_TEAM_NAME), _SESSION_ID)

        async def teammate() -> object:
            return get_root_span(session_id=_SESSION_ID)

        found = await asyncio.create_task(teammate(), context=contextvars.Context())
        logger.info("root the runner registered: {}", found)
        assert found is not None
    finally:
        removed = remove_team_span()
        if removed is not None and removed.is_recording():
            removed.end()
        shutdown_observability()
        reset_state()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_removing_the_team_root_clears_the_session_registration() -> None:
    runtime = ObservabilityRuntime()
    runtime.initialize(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    set_session_id(_SESSION_ID)
    set_current_session_id(_SESSION_ID)
    team_span = get_or_create_team_span(_TEAM_NAME, runtime.get_tracer("team-root-test"))
    assert team_span is not None
    try:
        removed = remove_team_span()
        assert removed is team_span
        if removed.is_recording():
            removed.end()

        async def teammate() -> object:
            return get_root_span(session_id=_SESSION_ID)

        assert await asyncio.create_task(teammate(), context=contextvars.Context()) is None
    finally:
        runtime.shutdown()
        reset_state()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_the_runner_never_adopts_a_concurrent_single_agent_root() -> None:
    """A single-agent run in flight must not stand in for a new Team's root.

    Reproduces a Team started while another session's single-agent run is
    still live: that run's root was the only one registered, so an unscoped
    lookup adopted it, the Team's own root was never opened and every
    teammate span of the Team session went unrecorded.
    """
    from openjiuwen.agent_teams.observability.setup import (
        get_tracer,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.core.runner.team_runner import _TeamRunnerMixin
    from openjiuwen.harness.observability.span_context import (
        install_root_span_fallback,
        register_run_root_span,
        unregister_run_root_span,
    )

    init_observability(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    install_root_span_fallback()
    single_agent_session = "single-agent-session"
    single_agent_root = get_tracer("single-agent-test").start_span("agent.agent.work.normal.single")

    def open_single_agent_run() -> None:
        # The single-agent run lives in its own task; only its registrations
        # are visible to the Team's context.
        set_root_span(single_agent_root, session_id=single_agent_session)
        register_run_root_span(single_agent_root, session_id=single_agent_session)

    contextvars.Context().run(open_single_agent_run)
    set_session_id("")
    set_current_session_id("")
    try:
        _TeamRunnerMixin._maybe_attach_observability(_StubLeader(_TEAM_NAME), _SESSION_ID)

        async def teammate() -> object:
            return get_root_span(session_id=_SESSION_ID)

        found = await asyncio.create_task(teammate(), context=contextvars.Context())
        logger.info("team root resolved while a single-agent run is live: {}", found)
        assert found is not None
        assert found is not single_agent_root
        assert found.name == f"team.{_TEAM_NAME}"
        assert get_root_span(session_id=single_agent_session) is single_agent_root
    finally:
        removed = remove_team_span()
        if removed is not None and removed.is_recording():
            removed.end()
        unregister_run_root_span(single_agent_root, session_id=single_agent_session)
        single_agent_root.end()
        shutdown_observability()
        reset_state()


@pytest.mark.level1
def test_an_unscoped_lookup_never_adopts_a_session_registered_root() -> None:
    runtime = ObservabilityRuntime()
    runtime.initialize(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    other_root = runtime.get_tracer("team-root-test").start_span("root.other")
    contextvars.Context().run(set_root_span, other_root, session_id="other-session")
    try:
        # The only live registered root belongs to another session: with no
        # session in reach it is still not the caller's root.
        assert contextvars.Context().run(get_root_span) is None
        assert contextvars.Context().run(get_team_span) is None
        assert get_session_root_span("other-session") is other_root
        assert get_team_span(session_id=_SESSION_ID) is None
    finally:
        other_root.end()
        runtime.shutdown()
        reset_state()


@pytest.mark.level1
def test_a_session_lookup_ignores_a_root_bound_for_another_session() -> None:
    runtime = ObservabilityRuntime()
    runtime.initialize(ObservabilityConfig(enabled=True, service_name="team-root-test", sample_rate=1.0))
    bound_root = runtime.get_tracer("team-root-test").start_span("root.bound")

    def lookup() -> tuple[object, object]:
        set_root_span(bound_root, session_id="other-session")
        return get_session_root_span(_SESSION_ID), get_session_root_span("other-session")

    try:
        foreign, own = contextvars.Context().run(lookup)
        assert foreign is None
        assert own is bound_root
    finally:
        bound_root.end()
        runtime.shutdown()
        reset_state()


@pytest.mark.level1
def test_a_session_lookup_requires_a_session_id() -> None:
    with pytest.raises(ValueError):
        get_session_root_span("")
