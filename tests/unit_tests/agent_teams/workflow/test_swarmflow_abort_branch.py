# coding: utf-8
from __future__ import annotations
import pytest
from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


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


def _make_tool() -> SwarmflowTool:
    # 用最小构造绕过完整 leader；测试只验消息格式化，不跑 run_background
    tool = object.__new__(SwarmflowTool)
    return tool
