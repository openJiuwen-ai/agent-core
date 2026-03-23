# -*- coding: utf-8 -*-
"""System tests for RewardRegistry: register, get, list, register_reward decorator."""

import pytest

from openjiuwen.dev_tools.agentrl.reward.registry import (
    RewardRegistry,
    reward_registry,
    register_reward,
)


def test_registry_e2e_register_get_list():
    """E2E: register reward, get returns callable, list returns all names."""
    reg = RewardRegistry()

    def my_reward(rollout):
        return rollout.get("score", 0.5)
    reg.register("e2e_reward", my_reward)
    assert reg.get("e2e_reward") is my_reward
    assert reg.get("e2e_reward")({"score": 0.9}) == 0.9
    reg.register("e2e_reward2", lambda x: 1.0)
    names = reg.list()
    assert "e2e_reward" in names
    assert "e2e_reward2" in names


def test_registry_e2e_decorator():
    """E2E: @register_reward decorator adds to reward_registry."""

    @register_reward("e2e_decorated")
    def decorated_reward(rollout):
        return 0.42
    assert reward_registry.get("e2e_decorated") is decorated_reward
    assert reward_registry.get("e2e_decorated")(None) == 0.42
