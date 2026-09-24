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
    assert TURN_TIMEOUT_S_DEFAULT == 2400.0
    assert WAIT_TIMEOUT_MS_DEFAULT == 2_400_000
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


def test_prompts_bind_wait_timeout_constants() -> None:
    from openjiuwen.harness.prompts.sections.subagent_tools import (
        SUBAGENT_SYSTEM_PROMPT_CN,
        SUBAGENT_SYSTEM_PROMPT_EN,
    )
    from openjiuwen.harness.prompts.tools.subagent_tools import (
        SUBAGENT_WAIT_DESCRIPTION,
        get_subagent_wait_input_params,
    )

    default_ms = str(int(WAIT_TIMEOUT_MS_DEFAULT))
    default_min = str(int(WAIT_TIMEOUT_MS_DEFAULT // 60_000))
    max_ms = str(int(WAIT_TIMEOUT_MS_MAX))
    texts = (
        SUBAGENT_SYSTEM_PROMPT_CN,
        SUBAGENT_SYSTEM_PROMPT_EN,
        SUBAGENT_WAIT_DESCRIPTION["cn"],
        SUBAGENT_WAIT_DESCRIPTION["en"],
        get_subagent_wait_input_params()["properties"]["timeout_ms"]["description"],
    )
    for text in texts:
        assert default_ms in text
        assert default_min in text
        assert max_ms in text
        assert "1800000" not in text
        assert "30 分钟" not in text
        assert "30 min" not in text


def test_subagent_runtime_config_is_frozen() -> None:
    config = SubagentRuntimeConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.max_subagents = 99  # type: ignore[misc]
