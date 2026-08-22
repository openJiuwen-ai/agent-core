# coding: utf-8
from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.workflow.engine.errors import BackendError, WorkflowAborted
from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind
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


def test_run_background_early_return_publishes_paused_event():
    """reason=early_return publishes WORKFLOW_PAUSED (not STOPPED) before BackendError.

    Early-return is a resumable pause — the run pauses so the leader can edit the
    script and re-run under the same run_id — so the Monitor flips the card to
    paused. Without any terminal/pause event it would stay "running" forever.
    """
    tool = _make_tool(messager=_CapturingMessager())
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="early_return", reply="改 X", edit_hints="focus Y"),
    ):
        _run_abort_and_drain(
            tool, "task-early-pub", _inputs("wf_1"), expect=BackendError
        )
    assert ProgressKind.WORKFLOW_PAUSED in _published_kinds(tool)
    paused = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_PAUSED
    ]
    assert paused and paused[0].payload["text"] == "workflow paused for script edit"


def test_run_background_early_return_without_hints_publishes_paused_event():
    """reason=early_return without edit_hints still publishes WORKFLOW_PAUSED."""
    tool = _make_tool(messager=_CapturingMessager())
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="early_return", reply="改 X"),
    ):
        _run_abort_and_drain(
            tool, "task-early-pub2", _inputs("wf_1"), expect=BackendError
        )
    assert ProgressKind.WORKFLOW_PAUSED in _published_kinds(tool)
    paused = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_PAUSED
    ]
    assert paused and paused[0].payload["text"] == "workflow paused for script edit"


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
    assert "已停止" in msg or "stopped" in msg.lower()


def test_run_background_pause_raises_cancelled_error():
    """reason=pause (default) is a silent cancellation, no BackendError."""
    tool = _make_tool()
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="pause"),
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(tool.run_background("task-pause", _inputs("wf_1")))


class _CapturingMessager:
    """Records every publish (topic, EventMessage) without fanning out."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, topic_id: str, message) -> None:
        self.published.append((topic_id, message))


def _run_abort_and_drain(tool, task_id: str, inputs: dict[str, Any], *, expect: type[BaseException]) -> None:
    """Run run_background to an abort, then drain the fire-and-forget publish task.

    ``_publish`` schedules via ``asyncio.create_task``; ``asyncio.run`` would
    cancel that still-pending task on exit, so drive a manual loop and let the
    publish task complete before asserting on the recording messager.
    """
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(expect):
            loop.run_until_complete(tool.run_background(task_id, inputs))
        loop.run_until_complete(asyncio.sleep(0))  # run the scheduled publish task
    finally:
        loop.close()


def _published_kinds(tool) -> list[str]:
    return [message.payload["kind"] for _, message in tool._messager.published]


def test_run_background_stop_publishes_workflow_stopped_event():
    """reason=stop publishes WORKFLOW_STOPPED progress before the BackendError."""
    tool = _make_tool(messager=_CapturingMessager())
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(reason="stop"),
    ):
        _run_abort_and_drain(
            tool, "task-stop-pub", _inputs("wf_1"), expect=BackendError
        )
    assert ProgressKind.WORKFLOW_STOPPED in _published_kinds(tool)
    stopped = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_STOPPED
    ]
    assert stopped and stopped[0].payload["text"] == "workflow stopped"


def test_run_background_pause_publishes_workflow_paused_event():
    """reason=pause (default) publishes WORKFLOW_PAUSED before the silent cancel."""
    tool = _make_tool(messager=_CapturingMessager())
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=WorkflowAborted(),
    ):
        _run_abort_and_drain(
            tool, "task-pause-pub", _inputs("wf_1"), expect=asyncio.CancelledError
        )
    assert ProgressKind.WORKFLOW_PAUSED in _published_kinds(tool)
    paused = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_PAUSED
    ]
    assert paused and paused[0].payload["text"] == "workflow paused"


def test_run_background_cancel_with_abort_pause_publishes_workflow_paused():
    """Controller cancel path: CancelledError + abort_event(pause) -> WORKFLOW_PAUSED.

    ``BackgroundTaskController._abort_one`` cancels the top-level task as its
    third step, so when the in-flight agent is mid-LLM-call (never reaching an
    abort checkpoint) the engine raises ``asyncio.CancelledError`` instead of
    ``WorkflowAborted``. The abort signal is still set with the pause reason, so
    the handler must publish the same WORKFLOW_PAUSED event the WorkflowAborted
    branch publishes.
    """
    tool = _make_tool(messager=_CapturingMessager())

    def _raise_cancelled(script_path, abort_event=None, **kwargs):
        abort_event.set("pause")
        raise asyncio.CancelledError()

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_raise_cancelled,
    ):
        _run_abort_and_drain(
            tool, "task-cancel-pause", _inputs("wf_1"), expect=asyncio.CancelledError
        )
    assert ProgressKind.WORKFLOW_PAUSED in _published_kinds(tool)
    paused = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_PAUSED
    ]
    assert paused and paused[0].payload["text"] == "workflow paused"


def test_run_background_cancel_with_abort_stop_publishes_stopped_and_backend_error():
    """Controller cancel path: CancelledError + abort_event(stop) -> WORKFLOW_STOPPED + BackendError.

    stop is terminal, so unlike pause it must surface a BackendError carrying the
    stopped message (so the async-tool runtime injects it into the leader),
    matching the ``except WorkflowAborted`` stop branch.
    """
    tool = _make_tool(messager=_CapturingMessager())

    def _raise_cancelled(script_path, abort_event=None, **kwargs):
        abort_event.set("stop")
        raise asyncio.CancelledError()

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_raise_cancelled,
    ):
        _run_abort_and_drain(tool, "task-cancel-stop", _inputs("wf_1"), expect=BackendError)
    assert ProgressKind.WORKFLOW_STOPPED in _published_kinds(tool)
    stopped = [
        m for _, m in tool._messager.published if m.payload["kind"] == ProgressKind.WORKFLOW_STOPPED
    ]
    assert stopped and stopped[0].payload["text"] == "workflow stopped"


def test_run_background_external_cancel_publishes_nothing_and_reraises():
    """External (non-controller) cancel: abort_event unset -> silent CancelledError, no events."""
    tool = _make_tool(messager=_CapturingMessager())

    def _raise_cancelled(script_path, **kwargs):
        raise asyncio.CancelledError("external-cancel")

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_raise_cancelled,
    ):
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(asyncio.CancelledError) as excinfo:
                loop.run_until_complete(tool.run_background("task-ext-cancel", _inputs("wf_1")))
            loop.run_until_complete(asyncio.sleep(0))  # drain any publish task
        finally:
            loop.close()
    # The same CancelledError propagates unchanged (not replaced), and the
    # external cancel stays silent — no status events leak to the Monitor.
    assert excinfo.value.args == ("external-cancel",)
    assert _published_kinds(tool) == []


def _make_tool(messager=None) -> SwarmflowTool:
    # 用最小构造绕过完整 leader；仅设置 run_background 触及的属性
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = _FakeParentAgent()
    tool._messager = messager
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
