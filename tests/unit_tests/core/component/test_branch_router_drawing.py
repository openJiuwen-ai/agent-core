# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.workflow import BranchRouter, End, ExpressionCondition, Start, Workflow


@pytest.fixture
def construction_loop():
    """Supply the current construction requirement without changing caller state."""
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(previous_loop)


@pytest.mark.parametrize("initial_flag", [None, "false", "true"])
def test_router_created_before_drawing_is_enabled(monkeypatch, construction_loop, initial_flag):
    if initial_flag is None:
        monkeypatch.delenv("WORKFLOW_DRAWABLE", raising=False)
    else:
        monkeypatch.setenv("WORKFLOW_DRAWABLE", initial_flag)

    router = BranchRouter()
    router.add_branch("false", ["first", "second"], "unused-label")
    router.add_branch(ExpressionCondition("true"), "end", "fallback")

    monkeypatch.setenv("WORKFLOW_DRAWABLE", "true")
    workflow = Workflow()
    workflow.set_start_comp("start", Start())
    for node_id in ("first", "second", "end"):
        workflow.set_end_comp(node_id, End())
    workflow.add_conditional_connection("start", router)

    drawing = workflow.draw(output_format="mermaid")
    assert drawing.count('-.->|"false"|') == 2
    assert drawing.count('-.->|"fallback"|') == 1
    assert construction_loop.run_until_complete(router()) == ["end"]


def test_router_preserves_branches_added_across_flag_changes(monkeypatch, construction_loop):
    monkeypatch.delenv("WORKFLOW_DRAWABLE", raising=False)
    router = BranchRouter()
    router.add_branch(ExpressionCondition("false"), "end", "early")
    monkeypatch.setenv("WORKFLOW_DRAWABLE", "true")
    router.add_branch(ExpressionCondition("true"), "end", "late")

    workflow = Workflow()
    workflow.set_start_comp("start", Start())
    workflow.set_end_comp("end", End())
    workflow.add_conditional_connection("start", router)

    drawing = workflow.draw(output_format="mermaid")
    assert drawing.count('-.->|"early"|') == 1
    assert drawing.count('-.->|"late"|') == 1
    assert construction_loop.run_until_complete(router()) == ["end"]


def test_workflow_drawing_remains_disabled_until_workflow_is_rebuilt(monkeypatch, construction_loop):
    monkeypatch.delenv("WORKFLOW_DRAWABLE", raising=False)
    router = BranchRouter()
    router.add_branch("true", "end")
    workflow = Workflow()
    workflow.set_start_comp("start", Start())
    workflow.set_end_comp("end", End())
    workflow.add_conditional_connection("start", router)
    monkeypatch.setenv("WORKFLOW_DRAWABLE", "true")

    assert workflow.draw(output_format="mermaid") == ""
    assert construction_loop.run_until_complete(router()) == ["end"]
