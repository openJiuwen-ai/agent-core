# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the process-level agent runtime registry."""

import gc
from typing import cast

import pytest

from openjiuwen.core.single_agent.agent_runtime import AgentRuntime, get_agent_runtime
from openjiuwen.core.single_agent.base import BaseAgent


class _RuntimeStub:
    pass


def _new_runtime() -> BaseAgent:
    return cast(BaseAgent, _RuntimeStub())


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
