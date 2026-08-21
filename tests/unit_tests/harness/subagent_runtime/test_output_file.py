# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent turn output file helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.harness.subagent_runtime.models import SubagentTurn
from openjiuwen.harness.subagent_runtime.output_file import (
    resolve_output_path,
    resolve_parent_workspace_root,
    write_turn_output,
)


def test_resolve_parent_workspace_root_from_string() -> None:
    parent = SimpleNamespace(deep_config=SimpleNamespace(workspace="/tmp/ws"))
    assert resolve_parent_workspace_root(parent) == Path("/tmp/ws").resolve()


def test_resolve_parent_workspace_root_from_workspace_object() -> None:
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(workspace=SimpleNamespace(root_path="/tmp/object-ws")),
    )
    assert resolve_parent_workspace_root(parent) == Path("/tmp/object-ws").resolve()


def test_resolve_parent_workspace_root_defaults_to_cwd() -> None:
    assert resolve_parent_workspace_root(SimpleNamespace()) == Path(".").resolve()


def test_resolve_output_path_stays_under_outputs_dir(tmp_path: Path) -> None:
    path = resolve_output_path(tmp_path, "parent_sub_explore_ab12", "task-abc123")
    assert path == (
        tmp_path / "sub_agents" / "parent_sub_explore_ab12" / "outputs" / "task-abc123.md"
    ).resolve()


def test_write_turn_output_writes_markdown(tmp_path: Path) -> None:
    output_path = write_turn_output(
        tmp_path,
        "parent_sub_explore_ab12",
        "task-abc123",
        "## report\n\nhello",
    )
    written = Path(output_path)
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == "## report\n\nhello"


def test_subagent_turn_from_dict_without_output_file() -> None:
    turn = SubagentTurn.from_dict(
        {
            "subagent_id": "sid",
            "task_id": "tid",
            "seq": 1,
            "prompt": "hello",
            "answer": "done",
            "closed_reason": "completed",
            "created_at_ms": 1.0,
        },
    )
    assert turn.output_file is None


def test_subagent_turn_to_dict_omits_none_output_file() -> None:
    turn = SubagentTurn(
        subagent_id="sid",
        task_id="tid",
        seq=1,
        prompt="hello",
        answer="done",
        closed_reason="completed",
        created_at_ms=1.0,
    )
    assert "output_file" not in turn.to_dict()
