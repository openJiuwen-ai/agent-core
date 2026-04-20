# -*- coding: utf-8 -*-
"""Unit tests for RewardRegistry: register, get, list, decorator; exception semantics."""

import pytest

from openjiuwen.agent_evolving.agent_rl.reward import (
    RewardRegistry,
    reward_registry,
    register_reward,
)


def _make_registry():
    return RewardRegistry()


def test_register_and_get_returns_same_callable():
    reg = _make_registry()

    def fn(x):
        return x + 1
    reg.register("r1", fn)
    assert reg.get("r1") is fn
    assert reg.get("r1")(10) == 11


def test_list_returns_all_registered_names():
    reg = _make_registry()
    reg.register("a", lambda: 1)
    reg.register("b", lambda: 2)
    names = reg.list()
    assert set(names) == {"a", "b"}


def test_register_empty_name_raises_value_error():
    reg = _make_registry()
    with pytest.raises(Exception) as exc_info:
        reg.register("", lambda: 1)
    assert "non-empty" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()


def test_get_nonexistent_raises_key_error():
    reg = _make_registry()
    with pytest.raises(Exception) as exc_info:
        reg.get("nonexistent")
    assert "nonexistent" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


def test_decorator_register_reward():
    reg = _make_registry()

    @register_reward("r2")
    def my_reward(rollout):
        return 0.5
    assert reward_registry.get("r2") is my_reward
