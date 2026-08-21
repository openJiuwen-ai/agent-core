# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for runtime subagent tool prompt metadata."""

from openjiuwen.harness.prompts.sections.subagent_tools import (
    build_subagent_tools_system_prompt,
)
from openjiuwen.harness.prompts.tools.subagent_tools import (
    SUBAGENT_SPAWN_DESCRIPTION,
)


def test_subagent_system_prompt_includes_spawn_trigger_rules_cn() -> None:
    prompt = build_subagent_tools_system_prompt("cn")

    assert "何时委派" in prompt
    assert "并行" in prompt
    assert "同一 turn 内 spawn 后必须 subagent_wait" in prompt
    assert "调研" in prompt
    assert "不能" in prompt and "单凭" in prompt


def test_subagent_system_prompt_includes_spawn_trigger_rules_en() -> None:
    prompt = build_subagent_tools_system_prompt("en")

    assert "When to delegate" in prompt
    assert "critical-path" in prompt
    assert "Call subagent_wait in the same turn after spawn" in prompt
    assert "never authorize spawning by themselves" in prompt


def test_spawn_description_states_delegation_authorization_cn() -> None:
    desc = SUBAGENT_SPAWN_DESCRIPTION["cn"]

    assert "AGENTS.md" in desc
    assert "spawn 条件" in desc or "不能单凭" in desc
    assert "同一 turn" in desc
    assert "干等" in desc or "并行" in desc


def test_spawn_description_states_delegation_authorization_en() -> None:
    desc = SUBAGENT_SPAWN_DESCRIPTION["en"]

    assert "AGENTS.md" in desc
    assert "does not authorize spawning alone" in desc
    assert "same turn" in desc
    assert "critical-path" in desc
