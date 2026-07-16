#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.controllers import action as controller
from openjiuwen.harness.tools.browser_move.controllers.action import (
    _build_batch_interact_script,
    _build_drag_script,
)


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


def _realistic_booking_steps() -> list[dict[str, Any]]:
    return [
        {"op": "fill", "label": "First name", "value": "John"},
        {"op": "fill", "placeholder": "Email address", "value": "john@example.com"},
        {
            "op": "autocomplete",
            "placeholder": "From",
            "value": "Singapore",
            "choose_text": "Singapore (SIN)",
        },
        {
            "op": "autocomplete",
            "placeholder": "To",
            "value": "Kuala Lumpur",
            "option_role": "option",
            "option_name": "Kuala Lumpur (KUL)",
        },
        {"op": "select_option", "label": "Nationality", "option_text": "Singapore"},
        {"op": "set_checked", "label": "Male", "checked": True},
        {"op": "click", "role": "button", "name": "Search"},
        {"op": "wait_for_text", "text": "Select"},
        {"op": "extract_value", "label": "First name"},
    ]


def test_batch_interact_script_contains_realistic_playwright_locators() -> None:
    js = _build_batch_interact_script(
        {
            "steps": _realistic_booking_steps(),
            "timeout_ms": 3000,
            "wait_after_each_ms": 0,
            "continue_on_error": False,
        }
    )

    assert "page.getByLabel" in js
    assert "page.getByPlaceholder" in js
    assert "page.getByRole" in js
    assert "page.getByText" in js
    assert "page.keyboard.type" in js
    assert ".selectOption(option" in js
    assert ".setChecked(checked" in js
    assert "waitFor({ state: 'visible'" in js
    assert "Singapore (SIN)" in js
    assert "Kuala Lumpur (KUL)" in js
    assert "page.waitForTimeout" in js
    assert "setTimeout" not in js


def test_drag_script_uses_playwright_wait_for_timeout_instead_of_global_settimeout() -> None:
    js = _build_drag_script({
        "coord_source_x": 1,
        "coord_source_y": 2,
        "coord_target_x": 10,
        "coord_target_y": 20,
        "delay_ms": 5,
    })

    assert "page.waitForTimeout" in js
    assert "setTimeout" not in js


def test_batch_interact_action_calls_code_executor_and_parses_result() -> None:
    observed: dict[str, Any] = {}

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        observed["js_code"] = js_code
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": True,
                            "steps": [
                                {"index": 0, "op": "fill", "ok": True},
                                {"index": 1, "op": "autocomplete", "ok": True},
                                {"index": 2, "op": "select_option", "ok": True},
                            ],
                            "elapsed_ms": 27,
                            "url": "https://example.test/booking",
                            "title": "Booking form",
                            "error": None,
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
            session_id="sess-test",
            request_id="req-test",
            steps=_realistic_booking_steps(),
            timeout_ms=3000,
            global_timeout_ms=20000,
        )
    )

    assert result["ok"] is True
    assert result["action"] == "browser_batch_interact"
    assert result["session_id"] == "sess-test"
    assert result["request_id"] == "req-test"
    assert result["steps"][1]["op"] == "autocomplete"
    assert "Singapore (SIN)" in observed["js_code"]
    assert "Nationality" in observed["js_code"]


def test_batch_interact_action_logs_failed_click_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[tuple[str, tuple[Any, ...]]] = []

    def capture_log(message: str, *args: Any) -> None:
        logged.append((message, args))

    async def fake_code_executor(_js_code: str) -> dict[str, Any]:
        actionability = {
            "visible": True,
            "enabled": True,
            "stable": None,
            "receives_events": True,
            "probe_status": {"animation_frames": {"status": "timeout"}},
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": False,
                            "error": "locator.click timed out",
                            "failed_step": {
                                "index": 0,
                                "op": "click",
                                "ok": False,
                                "actionability": actionability,
                            },
                            "completed_steps": [
                                {
                                    "index": 0,
                                    "op": "click",
                                    "ok": False,
                                    "actionability": actionability,
                                }
                            ],
                            "elapsed_ms": 5000,
                        }
                    ),
                }
            ]
        }

    monkeypatch.setattr(controller, "browser_agent_log_info", capture_log)
    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=[{"op": "click", "selector": "#submit"}],
        )
    )

    diagnostic_logs = [entry for entry in logged if "click_diagnostics" in entry[0]]
    assert result["ok"] is False
    assert len(diagnostic_logs) == 1
    assert diagnostic_logs[0][1][1:5] == (True, True, None, True)
    assert '"animation_frames":{"status":"timeout"}' in diagnostic_logs[0][1][5]


def test_batch_interact_action_reports_truncated_steps() -> None:
    observed: dict[str, Any] = {}

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        observed["js_code"] = js_code
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"ok": True, "steps": [], "elapsed_ms": 1}),
                }
            ]
        }

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    steps = [
        {"op": "click", "selector": f"#button-{index}"}
        for index in range(30)
    ]
    result = _run(
        controller.run_action(
            "browser_batch_interact",
            session_id="sess-test",
            request_id="req-test",
            steps=steps,
        )
    )

    assert result["ok"] is True
    assert result["original_step_count"] == 30
    assert result["executed_step_limit"] == 25
    assert result["truncated"] is True
    assert result["dropped_step_count"] == 5
    assert "#button-24" in observed["js_code"]
    assert "#button-25" not in observed["js_code"]


def test_batch_interact_action_rejects_empty_or_non_list_steps_before_running_code() -> None:
    called = False

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"ok": True, "code": js_code}

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            session_id="sess-test",
            request_id="req-test",
            steps=[],
        )
    )

    assert result["ok"] is False
    assert "non-empty list" in result["error"]
    assert called is False


def test_batch_interact_action_fails_cleanly_when_code_executor_missing() -> None:
    controller.register_example_actions()
    controller.clear_code_executor()

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            session_id="sess-test",
            request_id="req-test",
            steps=[{"op": "click", "text": "Search"}],
        )
    )

    assert result["ok"] is False
    assert result["error"] == "browser_code_executor_not_ready"


def test_batch_interact_script_runs_against_playwright_like_form_stub(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping generated JavaScript execution smoke test")

    js_function = _build_batch_interact_script(
        {
            "steps": _realistic_booking_steps(),
            "timeout_ms": 3000,
            "wait_after_each_ms": 0,
            "continue_on_error": False,
        }
    )
    runner = tmp_path / "run_batch_interact.js"
    runner.write_text(
        textwrap.dedent(
            f"""
            const fn = ({js_function});
            const calls = [];

            class FakeLocator {{
              constructor(kind, value) {{ this.kind = kind; this.value = value; }}
              first() {{ calls.push(['first', this.kind, this.value]); return this; }}
              async click(options) {{ calls.push(['click', this.kind, this.value, options && options.timeout]); }}
              async fill(value, options) {{ calls.push(['fill', this.kind, this.value, value, options && options.timeout]); }}
              async selectOption(option, options) {{ calls.push(['selectOption', this.kind, this.value, option, options && options.timeout]); }}
              async setChecked(checked, options) {{ calls.push(['setChecked', this.kind, this.value, checked, options && options.timeout]); }}
              async waitFor(options) {{ calls.push(['waitFor', this.kind, this.value, options && options.state, options && options.timeout]); }}
              async press(key, options) {{ calls.push(['press', this.kind, this.value, key, options && options.timeout]); }}
              async innerText() {{ calls.push(['innerText', this.kind, this.value]); return 'Cheapest flight SGD 95'; }}
              async inputValue() {{ calls.push(['inputValue', this.kind, this.value]); return 'John'; }}
            }}

            const page = {{
              locator: (selector) => new FakeLocator('selector', selector),
              getByRole: (role, options = {{}}) => new FakeLocator('role', role + ':' + (options.name || '')),
              getByLabel: (label) => new FakeLocator('label', label),
              getByPlaceholder: (placeholder) => new FakeLocator('placeholder', placeholder),
              getByText: (text) => new FakeLocator('text', text),
              getByTestId: (testid) => new FakeLocator('testid', testid),
              keyboard: {{
                async press(key) {{ calls.push(['keyboard.press', key]); }},
                async type(value, options = {{}}) {{ calls.push(['keyboard.type', value, options.delay || 0]); }},
              }},
              async waitForLoadState(state) {{ calls.push(['waitForLoadState', state]); }},
              async evaluate(fn) {{ calls.push(['evaluate']); return 'Booking page ready'; }},
              async screenshot(options) {{ calls.push(['screenshot', options.path]); }},
              url: () => 'https://example.test/booking',
              title: async () => 'Booking form',
            }};

            fn(page).then((result) => {{
              console.log(JSON.stringify({{ result, calls }}));
            }}).catch((error) => {{
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            }});
            """
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [node, str(runner)],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["result"]["ok"] is True
    assert [step["op"] for step in payload["result"]["steps"]] == [
        step["op"] for step in _realistic_booking_steps()
    ]
    assert ["fill", "label", "First name", "John", 3000] in payload["calls"]
    assert ["keyboard.type", "Singapore", 0] in payload["calls"]
    assert ["click", "text", "Singapore (SIN)", 3000] in payload["calls"]
    assert any(call[0] == "selectOption" and call[3] == {"label": "Singapore"} for call in payload["calls"])
    assert ["setChecked", "label", "Male", True, 3000] in payload["calls"]


def test_batch_interact_failed_click_reports_stalled_animation_frames(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping generated JavaScript execution smoke test")

    js_function = _build_batch_interact_script(
        {
            "steps": [{"op": "click", "selector": "#submit"}],
            "timeout_ms": 5000,
            "wait_after_each_ms": 0,
            "continue_on_error": False,
        }
    )
    runner = tmp_path / "run_failed_click_diagnostics.js"
    runner.write_text(
        textwrap.dedent(
            f"""
            const fn = ({js_function});

            class FakeLocator {{
              first() {{ return this; }}
              async click() {{ throw new Error('locator.click: Timeout 5000ms exceeded.'); }}
              async count() {{ return 1; }}
              async isVisible() {{ return true; }}
              async isEnabled() {{ return true; }}
              async boundingBox() {{ return {{ x: 10, y: 20, width: 30, height: 40 }}; }}
              async evaluate(fn) {{
                if (String(fn).includes('requestAnimationFrame')) {{
                  return new Promise(() => {{}});
                }}
                return {{
                  document_visibility: 'visible',
                  document_hidden: false,
                  viewport: {{ width: 800, height: 600 }},
                  scroll: {{ x: 0, y: 0 }},
                  in_viewport: true,
                  click_point: {{ x: 25, y: 40 }},
                  receives_events: true,
                  hit_target: {{ tag: 'button', id: 'submit', role: 'button', classes: '' }},
                  element: {{
                    tag: 'button', id: 'submit', role: 'button', classes: '',
                    aria_checked: null, aria_disabled: null, aria_expanded: null, tabindex: null,
                  }},
                  animations: [],
                }};
              }}
            }}

            const locator = new FakeLocator();
            const page = {{
              locator: () => locator,
              waitForTimeout: async () => new Promise((resolve) => setImmediate(resolve)),
              evaluate: async () => 'Visible text',
              url: () => 'https://example.test/form',
              title: async () => 'Example form',
            }};

            fn(page).then((result) => {{
              console.log(JSON.stringify(result));
            }}).catch((error) => {{
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            }});
            """
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [node, str(runner)],
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)
    diagnostics = result["failed_step"]["actionability"]

    assert result["ok"] is False
    assert diagnostics["attached"] is True
    assert diagnostics["visible"] is True
    assert diagnostics["enabled"] is True
    assert diagnostics["stable"] is None
    assert diagnostics["receives_events"] is True
    assert diagnostics["page"]["document_visibility"] == "visible"
    assert diagnostics["animation_frames"] is None
    assert diagnostics["probe_status"]["animation_frames"] == {"status": "timeout"}
