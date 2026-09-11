# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for async session tool prompt metadata."""

from openjiuwen.harness.prompts.sections.session_tools import (
    build_session_tools_system_prompt,
)
from openjiuwen.harness.prompts.tools.session_tools import (
    SESSIONS_SPAWN_DESCRIPTION,
    get_sessions_spawn_input_params,
)


def test_spawn_input_params_require_core_fields() -> None:
    spawn_params = get_sessions_spawn_input_params("cn")

    assert "subagent_type" in spawn_params["properties"]
    assert "task_description" in spawn_params["properties"]
    assert "browser_capabilities" in spawn_params["properties"]
    assert spawn_params["required"] == ["subagent_type", "task_description"]


def test_spawn_description_mentions_parallel_execution() -> None:
    assert "并行" in SESSIONS_SPAWN_DESCRIPTION["cn"]
    assert "parallel" in SESSIONS_SPAWN_DESCRIPTION["en"].lower()


def test_session_system_prompt_mentions_pending_and_parallel_rules() -> None:
    cn_prompt = build_session_tools_system_prompt("cn")
    en_prompt = build_session_tools_system_prompt("en")

    assert "sessions_spawn" in cn_prompt
    assert "pending" in cn_prompt
    assert "并行" in cn_prompt
    assert "sessions_spawn" in en_prompt
    assert "pending" in en_prompt.lower()
    assert "parallel" in en_prompt.lower()
