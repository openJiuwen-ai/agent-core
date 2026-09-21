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
    remove_team_span,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.span_context import (
    get_root_span,
    reset_state,
    set_current_session_id,
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
