# coding: utf-8
from __future__ import annotations
from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted


def test_bare_defaults_to_pause():
    exc = WorkflowAborted()
    assert exc.reason == "pause"
    assert exc.reply is None
    assert exc.edit_hints is None


def test_early_return_carries_payload():
    exc = WorkflowAborted(reason="early_return", reply="改 reviewer prompt", edit_hints="focus risk")
    # 注：构造参数名是 edit_hints（不是 edit_instructions），便于与字段名一致
    assert exc.reason == "early_return"
    assert exc.reply == "改 reviewer prompt"
    assert exc.edit_hints == "focus risk"


def test_stop_carries_reason():
    exc = WorkflowAborted(reason="stop")
    assert exc.reason == "stop"
    assert exc.reply is None


def test_is_baseexception():
    assert issubclass(WorkflowAborted, BaseException)
    assert not issubclass(WorkflowAborted, Exception)
