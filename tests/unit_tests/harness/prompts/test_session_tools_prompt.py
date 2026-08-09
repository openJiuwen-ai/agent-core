# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for async session tool prompt metadata."""

from openjiuwen.harness.prompts.sections.session_tools import (
    build_session_tools_system_prompt,
)
from openjiuwen.harness.prompts.tools.session_tools import (
    SESSIONS_SPAWN_DESCRIPTION,
    get_sessions_resume_input_params,
    get_sessions_spawn_input_params,
)
from openjiuwen.harness.prompts.tools.todo import TODO_CREATE_DESCRIPTION


def test_session_tools_expose_optional_todo_id_binding() -> None:
    spawn_params = get_sessions_spawn_input_params("cn")
    resume_params = get_sessions_resume_input_params("cn")

    assert "todo_id" in spawn_params["properties"]
    assert "todo_id" in resume_params["properties"]
    assert "todo_id" not in spawn_params["required"]
    assert "todo_id" not in resume_params["required"]


def test_todo_id_params_state_one_todo_per_task_rule() -> None:
    for language in ("cn", "en"):
        spawn_desc = get_sessions_spawn_input_params(language)["properties"]["todo_id"]["description"]
        resume_desc = get_sessions_resume_input_params(language)["properties"]["todo_id"]["description"]
        if language == "cn":
            assert "一个 todo 同一时间只应绑定一个后台任务" in spawn_desc
            assert "一个 todo 同一时间只应绑定一个后台任务" in resume_desc
        else:
            assert "at most one background task" in spawn_desc
            assert "at most one background task" in resume_desc


def test_spawn_description_forbids_multi_binding_same_todo() -> None:
    assert "绑给多个并行任务" in SESSIONS_SPAWN_DESCRIPTION["cn"]
    assert "multiple concurrent tasks" in SESSIONS_SPAWN_DESCRIPTION["en"]


def test_todo_create_description_forbids_background_session_todos() -> None:
    assert "自动 session 汇总" in TODO_CREATE_DESCRIPTION["cn"]
    assert "自动同步" in TODO_CREATE_DESCRIPTION["cn"]
    assert "automatic session summaries" in TODO_CREATE_DESCRIPTION["en"]
    assert "syncs automatically" in TODO_CREATE_DESCRIPTION["en"]


def test_session_system_prompt_states_todo_binding_rules() -> None:
    cn_prompt = build_session_tools_system_prompt("cn")
    en_prompt = build_session_tools_system_prompt("en")

    assert "todo_id" in cn_prompt
    assert "一个 todo 同一时间只绑定一个后台任务" in cn_prompt
    assert "自动 session 汇总" in cn_prompt
    assert "todo_id" in en_prompt
    assert "one todo per background task" in en_prompt
    assert "automatic session summaries" in en_prompt
