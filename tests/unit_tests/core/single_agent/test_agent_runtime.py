# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the process-level agent runtime registry."""

import gc
from typing import cast

import pytest

from openjiuwen.core.single_agent.agent_runtime import AgentRuntime, get_agent_runtime
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class _RuntimeStub:
    def __init__(self, agent_id: str) -> None:
        self.card = AgentCard(id=agent_id)


def _new_runtime(agent_id: str = "runtime-agent") -> BaseAgent:
    return cast(BaseAgent, _RuntimeStub(agent_id))


@pytest.mark.level0
def test_bind_get_and_replace_runtime() -> None:
    registry = get_agent_runtime()
    session_id = "agent-runtime-bind-replace"
    first = _new_runtime()
    second = _new_runtime()

    registry.bind(session_id, first)
    assert registry.get(session_id) is first

    registry.bind(session_id, second)
    assert registry.get(session_id) is second
    assert registry is get_agent_runtime()

    registry.unbind(session_id, second)


@pytest.mark.level0
def test_get_returns_none_for_missing_or_released_runtime() -> None:
    registry = AgentRuntime()
    session_id = "agent-runtime-missing"

    assert registry.get(session_id) is None

    runtime = _new_runtime()
    registry.bind(session_id, runtime)
    del runtime
    gc.collect()

    assert registry.get(session_id) is None


@pytest.mark.level0
def test_unbind_does_not_remove_replacement_runtime() -> None:
    registry = AgentRuntime()
    session_id = "agent-runtime-identity-unbind"
    first = _new_runtime()
    second = _new_runtime()

    registry.bind(session_id, first)
    registry.bind(session_id, second)
    registry.unbind(session_id, first)

    assert registry.get(session_id) is second

    registry.unbind(session_id, second)
    assert registry.get(session_id) is None


def test_members_of_one_session_remain_independently_addressable() -> None:
    registry = AgentRuntime()
    leader = _new_runtime("leader")
    teammate = _new_runtime("teammate")
    registry.bind("team-session", leader)
    registry.bind("team-session", teammate)

    assert registry.get("team-session", agent_id="leader") is leader
    assert registry.get("team-session", agent_id="teammate") is teammate
    assert registry.get("team-session", agent_id="missing") is None
    assert registry.get("team-session") is teammate

    registry.unbind("team-session", teammate)
    assert registry.get("team-session") is leader
    assert registry.get("team-session", agent_id="leader") is leader


def test_session_only_query_tracks_rebinding_and_identity_safe_unbind() -> None:
    registry = AgentRuntime()
    leader = _new_runtime("leader")
    teammate = _new_runtime("teammate")
    registry.bind("team-session", leader)
    registry.bind("team-session", teammate)
    registry.bind("team-session", leader)
    other = _new_runtime("other")
    registry.bind("other-session", other)

    assert registry.get("team-session") is leader
    registry.unbind("team-session", teammate)
    assert registry.get("team-session") is leader
    registry.unbind("team-session", leader)
    assert registry.get("team-session") is None


def test_replacement_cleanup_and_collection_do_not_remove_other_members() -> None:
    registry = AgentRuntime()
    old = _new_runtime("teammate")
    replacement = _new_runtime("teammate")
    leader = _new_runtime("leader")
    registry.bind("team-session", leader)
    registry.bind("team-session", old)
    registry.bind("team-session", replacement)
    registry.unbind("team-session", old)

    assert registry.get("team-session", agent_id="teammate") is replacement
    del replacement
    gc.collect()
    assert registry.get("team-session", agent_id="teammate") is None
    assert registry.get("team-session") is leader
    assert registry.get("team-session", agent_id="leader") is leader
