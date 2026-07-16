#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.controllers import action as controller
from openjiuwen.harness.tools.browser_move.controllers.action import _build_batch_interact_script


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate_controller_state() -> None:
    ctl = controller.get_default_controller()
    snapshot = ctl.snapshot()
    ctl.restore({"actions": {}, "action_specs": {}, "runtime_runner": None, "code_executor": None})
    try:
        yield
    finally:
        ctl.restore(snapshot)


def test_batch_script_reports_continue_on_error_failures() -> None:
    js_code = _build_batch_interact_script(
        {
            "steps": [
                {"op": "fill", "selector": "#name", "value": "Alex"},
                {"op": "click", "selector": "#missing"},
            ],
            "continue_on_error": True,
        }
    )

    assert "one_or_more_steps_failed" in js_code
    assert "all_steps_ok" in js_code
    assert "had_step_errors" in js_code
    assert "steps_failed" in js_code


def test_controller_normalizes_partial_batch_result_to_failure() -> None:
    async def fake_code_executor(_js_code: str) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": True,
                            "error": None,
                            "steps": [
                                {"index": 0, "op": "fill", "ok": True},
                                {
                                    "index": 1,
                                    "op": "click",
                                    "ok": False,
                                    "error": "selector not found",
                                },
                            ],
                            "elapsed_ms": 12,
                        }
                    ),
                }
            ]
        }

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=[
                {"op": "fill", "selector": "#name", "value": "Alex"},
                {"op": "click", "selector": "#missing"},
            ],
            continue_on_error=True,
        )
    )

    assert result["ok"] is False
    assert result["error"] == "one_or_more_steps_failed"
    assert result["all_steps_ok"] is False
    assert result["had_step_errors"] is True
    assert result["partial"] is True
    assert result["steps_ok"] == 1
    assert result["steps_failed"] == 1
