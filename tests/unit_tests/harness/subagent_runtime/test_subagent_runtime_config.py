# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime config."""

from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.harness.subagent_runtime.config import (
    TURN_TIMEOUT_S_DEFAULT,
    WAIT_TIMEOUT_MS_DEFAULT,
    WAIT_TIMEOUT_MS_MAX,
    WAIT_TIMEOUT_MS_MIN,
    SubagentRuntimeConfig,
)


def test_wait_timeout_constants() -> None:
    assert TURN_TIMEOUT_S_DEFAULT == 1800.0
    assert WAIT_TIMEOUT_MS_DEFAULT == 1_800_000
    assert WAIT_TIMEOUT_MS_DEFAULT == int(TURN_TIMEOUT_S_DEFAULT * 1000)
    assert WAIT_TIMEOUT_MS_MIN == 10_000
    assert WAIT_TIMEOUT_MS_MAX == 3_600_000
    assert WAIT_TIMEOUT_MS_MIN < WAIT_TIMEOUT_MS_DEFAULT < WAIT_TIMEOUT_MS_MAX


def test_subagent_runtime_config_defaults() -> None:
    config = SubagentRuntimeConfig()
    assert config.max_subagents == 10
    assert config.max_concurrent_running == 5
    assert config.turn_timeout_s == TURN_TIMEOUT_S_DEFAULT
    assert config.turn_timeout_s * 1000 == WAIT_TIMEOUT_MS_DEFAULT
    assert config.enable_lru_eviction is True


def test_subagent_runtime_config_is_frozen() -> None:
    config = SubagentRuntimeConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.max_subagents = 99  # type: ignore[misc]
