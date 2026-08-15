# coding: utf-8
from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.workflow.engine.errors import BackendError, WorkflowAborted
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _FakeParentAgent:
    """Minimal stand-in for the leader harness: only exposes ``model``.

    ``run_background`` reaches the harness via ``getattr`` for
    ``background_task_controller`` / ``build_context`` (both default to None
    here), so no runtime or launch surface is needed for the abort path.
    """

    model = "fake-model"


def test_format_early_return_message():
    tool = _make_tool()
    msg = tool._format_early_return(reply="改 X", edit_hints="focus Y", run_id="wf_1")
    assert "wf_1" in msg
    assert "改 X" in msg
    assert "focus Y" in msg


def test_format_stopped_message():
    tool = _make_tool()
    msg = tool._format_stopped(run_id="wf_1")
    assert "wf_1" in msg
    assert "已停止" in msg or "stopped" in msg.lower()


def test_format_helpers_are_static():
    """_format_early_return/_format_stopped never touch instance state."""
    tool = _make_tool()
    assert not inspect.ismethod(tool._format_early_return)
    assert not inspect.ismethod(tool._format_stopped)


def test_run_background_early_return_injects_backend_error():
    """reason=early_return surfaces as BackendError carrying reply + edit_hints."""
    tool = _make_tool()
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="early_return", reply="改 X", edit_hints="focus Y"),
    ):
        with pytest.raises(BackendError) as excinfo:
            asyncio.run(tool.run_background("task-early", _inputs("wf_1")))
    msg = str(excinfo.value)
    assert "wf_1" in msg
    assert "改 X" in msg
    assert "focus Y" in msg


def test_run_background_stop_injects_backend_error():
    """reason=stop surfaces as BackendError carrying the stopped message."""
    tool = _make_tool()
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="stop"),
    ):
        with pytest.raises(BackendError) as excinfo:
            asyncio.run(tool.run_background("task-stop", _inputs("wf_1")))
    msg = str(excinfo.value)
    assert "wf_1" in msg
    assert "已停止" in msg


def test_run_background_pause_raises_cancelled_error():
    """reason=pause (default) is a silent cancellation, no BackendError."""
    tool = _make_tool()
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="pause"),
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(tool.run_background("task-pause", _inputs("wf_1")))


def _make_tool() -> SwarmflowTool:
    # 用最小构造绕过完整 leader；仅设置 run_background 触及的属性
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = _FakeParentAgent()
    tool._messager = None
    tool._team_name = "t"
    tool._language = "cn"
    tool._model_resolver = None
    tool._worker_base_spec = None
    tool._human_base_spec = None
    tool._budget = None
    tool._governor = None  # skips the finally governor release
    return tool


def _inputs(run_id: str) -> dict[str, Any]:
    # run_background's enriched-input keys; gate/ticket are only passed on to
    # run_swarmflow (mocked out), so None is fine.
    return {
        "_run_id": run_id,
        "_agent_gate": None,
        "_workflow_ticket": None,
        "_completion_ctx": {},
        "script_path": "/tmp/flow.py",
    }
