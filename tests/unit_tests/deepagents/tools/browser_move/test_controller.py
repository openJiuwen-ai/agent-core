#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for browser_move controller action registry behavior."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.deepagents.tools.browser_move.playwright_runtime import controller


@pytest.fixture(autouse=True)
def _isolate_controller_state() -> None:
    ctl = controller.get_default_controller()
    snapshot = ctl.snapshot()
    ctl.restore({"actions": {}, "action_specs": {}, "runtime_runner": None})
    try:
        yield
    finally:
        ctl.restore(snapshot)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_register_list_and_run_action_happy_path() -> None:
    async def plus_one(session_id: str = "", request_id: str = "", value: int = 0, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "value": value + 1,
            "handler_session_id": session_id,
            "handler_request_id": request_id,
            "meta": kwargs,
        }

    controller.register_action("  MyAction  ", plus_one)
    assert controller.list_actions() == ["myaction"]

    result = _run(
        controller.run_action("MYACTION", session_id="session-1", request_id="request-1", value=41, source="test")
    )
    assert result["ok"] is True
    assert result["action"] == "myaction"
    assert result["session_id"] == "session-1"
    assert result["request_id"] == "request-1"
    assert result["value"] == 42
    assert result["handler_session_id"] == "session-1"
    assert result["handler_request_id"] == "request-1"
    assert result["meta"] == {"source": "test"}


def test_run_action_unknown_action_returns_error() -> None:
    result = _run(controller.run_action("missing_action", session_id="s1", request_id="r1"))
    assert result["ok"] is False
    assert result["action"] == "missing_action"
    assert result["session_id"] == "s1"
    assert result["request_id"] == "r1"
    assert "unknown action: missing_action" in str(result["error"])


def test_run_action_handler_exception_is_captured() -> None:
    async def boom(session_id: str = "", request_id: str = "", **kwargs: Any) -> dict[str, Any]:
        del session_id, request_id, kwargs
        raise RuntimeError("boom")

    controller.register_action("explode", boom)
    result = _run(controller.run_action("explode", session_id="s2", request_id="r2"))
    assert result["ok"] is False
    assert result["action"] == "explode"
    assert result["session_id"] == "s2"
    assert result["request_id"] == "r2"
    assert "boom" in str(result["error"])


def test_browser_task_runtime_not_bound() -> None:
    controller.register_example_actions()
    controller.clear_runtime_runner()

    result = _run(controller.run_action("browser_task", session_id="s3", request_id="r3", task="open page"))
    assert result["ok"] is False
    assert result["session_id"] == "s3"
    assert result["request_id"] == "r3"
    assert "runtime_not_bound" in str(result["error"])


def test_browser_task_calls_bound_runner() -> None:
    observed: dict[str, Any] = {}

    async def fake_runner(
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        observed["task"] = task
        observed["session_id"] = session_id
        observed["request_id"] = request_id
        observed["timeout_s"] = timeout_s
        return {"ok": True, "final": "done"}

    controller.register_example_actions()
    controller.bind_runtime_runner(fake_runner)

    result = _run(
        controller.run_action(
            "browser_task",
            session_id="s4",
            request_id="r4",
            task="go to example.com",
            timeout_s="7",
        )
    )

    assert result["ok"] is True
    assert result["final"] == "done"
    assert observed == {
        "task": "go to example.com",
        "session_id": "s4",
        "request_id": "r4",
        "timeout_s": 7,
    }


def test_describe_actions_includes_registered_specs() -> None:
    controller.register_example_actions()
    details = controller.describe_actions()
    assert "browser_drag_and_drop" in details
    spec = details["browser_drag_and_drop"]
    assert isinstance(spec.get("summary"), str)
    assert spec["summary"]
    assert isinstance(spec.get("params"), dict)
    assert spec["params"]


def test_browser_get_element_coordinates_parses_fenced_json_output() -> None:
    async def fake_runner(
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        del task, session_id, request_id, timeout_s
        return {
            "ok": True,
            "final": (
                "```json\n{\"ok\": true, \"source\": {\"x\": 12, \"y\": 34},"
                " \"target\": null, \"error\": null}\n```"
            ),
        }

    controller.register_example_actions()
    controller.bind_runtime_runner(fake_runner)

    result = _run(
        controller.run_action(
            "browser_get_element_coordinates",
            session_id="s-json",
            request_id="r-json",
            element_source="Example",
        )
    )

    assert result["ok"] is True
    assert result["source"] == {"x": 12, "y": 34}
    assert result["target"] is None
    assert result["error"] is None


def test_action_controller_instance_isolation() -> None:
    ctrl_a = controller.ActionController()
    ctrl_b = controller.ActionController()

    async def handler_a(session_id: str = "", request_id: str = "", **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "who": "a", "session_id": session_id, "request_id": request_id, "meta": kwargs}

    ctrl_a.register_action("only_a", handler_a)
    assert ctrl_a.list_actions() == ["only_a"]
    assert ctrl_b.list_actions() == []

    seen: list[str] = []

    async def runner_a(
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        del timeout_s
        seen.append(f"a:{task}:{session_id}:{request_id}")
        return {"ok": True, "final": "from_a"}

    async def runner_b(
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        del timeout_s
        seen.append(f"b:{task}:{session_id}:{request_id}")
        return {"ok": True, "final": "from_b"}

    ctrl_a.bind_runtime_runner(runner_a)
    ctrl_b.bind_runtime_runner(runner_b)
    ctrl_a.register_example_actions()
    ctrl_b.register_example_actions()

    result_a = _run(ctrl_a.run_action("browser_task", session_id="sa", request_id="ra", task="task-a"))
    result_b = _run(ctrl_b.run_action("browser_task", session_id="sb", request_id="rb", task="task-b"))

    assert result_a["ok"] is True
    assert result_a["final"] == "from_a"
    assert result_b["ok"] is True
    assert result_b["final"] == "from_b"
    assert seen == ["a:task-a:sa:ra", "b:task-b:sb:rb"]
