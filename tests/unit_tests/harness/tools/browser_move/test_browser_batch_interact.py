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
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.controllers import action as controller
from openjiuwen.harness.tools.browser_move.controllers.action import (
    _build_batch_interact_script,
    _build_drag_script,
    _compact_extraction_provenance,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime


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


def test_batch_click_retries_only_transient_actionability_failures_within_same_timeout() -> None:
    js = _build_batch_interact_script(
        {
            "steps": [
                {"op": "click", "role": "button", "name": "Search"},
                {"op": "wait_for_url", "url_contains": "/results"},
            ],
            "timeout_ms": 2500,
        }
    )

    assert "clickWithTransientRetry" in js
    assert "intercept|not stable|outside of the viewport" in js
    assert "for (let attempt = 0; attempt < 2; attempt += 1)" in js
    assert "timeout - (Date.now() - started)" in js


def test_batch_extraction_provenance_keeps_selector_raw_text_and_generation() -> None:
    provenance = _compact_extraction_provenance(
        [
            {
                "op": "extract_text",
                "ok": True,
                "field": "title",
                "selector": "article:nth-of-type(1) h2",
                "raw_text": "Original\n title",
            },
            {
                "op": "extract_value",
                "ok": False,
                "field": "price",
                "selector": "#price",
                "raw_text": "99",
            },
        ],
        "g12",
    )

    assert provenance == {
        "title": {
            "selector": "article:nth-of-type(1) h2",
            "raw_text": "Original\n title",
            "generation_id": "g12",
        }
    }


def test_drag_script_uses_playwright_wait_for_timeout_instead_of_global_settimeout() -> None:
    js = _build_drag_script(
        {
            "coord_source_x": 1,
            "coord_source_y": 2,
            "coord_target_x": 10,
            "coord_target_y": 20,
            "delay_ms": 5,
        }
    )

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
    assert result["metrics"]["script_size_bytes"] > 0
    assert result["metrics"]["response_size_bytes"] > 0
    assert result["metrics"]["executor_elapsed_ms"] >= 0
    assert result["execution_mode"] == "compact_rpc"
    assert "url" not in result
    assert "title" not in result
    assert result["_runtime_page"] == {
        "url": "https://example.test/booking",
        "title": "Booking form",
    }
    assert "Singapore (SIN)" in observed["js_code"]
    assert "Nationality" in observed["js_code"]


def test_batch_interact_action_rejects_more_than_25_steps() -> None:
    called = False

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        del js_code
        nonlocal called
        called = True
        return {"ok": True}

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    steps = [{"op": "click", "selector": f"#button-{index}"} for index in range(30)]
    result = _run(
        controller.run_action(
            "browser_batch_interact",
            session_id="sess-test",
            request_id="req-test",
            steps=steps,
        )
    )

    assert result["ok"] is False
    assert "at most 25" in result["error"]
    assert called is False


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
            steps=[
                {"op": "fill", "placeholder": "Search", "value": "OpenJiuwen"},
                {"op": "press", "key": "Enter"},
            ],
        )
    )

    assert result["ok"] is False
    assert result["error"] == "browser_code_executor_not_ready"


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"op": "press"}], "requires key"),
        (
            [
                {"op": "unknown", "selector": "#x"},
                {"op": "press", "key": "Enter"},
            ],
            "unsupported",
        ),
        (
            [
                {"op": "fill", "value": "query"},
                {"op": "press", "key": "Enter"},
            ],
            "requires a target",
        ),
        (
            [
                {"op": "autocomplete", "selector": "#from", "value": "SIN"},
                {"op": "press", "key": "Enter"},
            ],
            "requires an option target",
        ),
    ],
)
def test_batch_interact_validates_schema_before_executor(
    steps: list[dict[str, Any]],
    message: str,
) -> None:
    called = False

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        del js_code
        nonlocal called
        called = True
        return {"ok": True}

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=steps,
        )
    )

    assert result["ok"] is False
    assert message in result["error"]
    assert called is False


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [
                {"op": "click", "selector": "text=销量", "role": "tab"},
                {"op": "press", "key": "Enter"},
            ],
            "exactly one locator strategy",
        ),
        (
            [
                {"op": "type", "selector": "#toolbar-search", "text": "query"},
                {"op": "press", "key": "Enter"},
            ],
            "use value, not text",
        ),
        (
            [
                {
                    "op": "autocomplete",
                    "target_id": "t_g2_1",
                    "value": "Shanghai",
                    "option_target_id": "t_g2_2",
                    "option_selector": ".autocomplete",
                },
                {"op": "press", "key": "Enter"},
            ],
            "exactly one option locator strategy",
        ),
        (
            [
                {"op": "wait_for_text", "text": "Results", "selector": ".guessed-result"},
                {"op": "press", "key": "Enter"},
            ],
            "does not accept a primary locator",
        ),
    ],
)
def test_batch_interact_rejects_known_bad_case_schema_before_executor(
    steps: list[dict[str, Any]],
    message: str,
) -> None:
    called = False

    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        del js_code
        nonlocal called
        called = True
        return {"ok": True}

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(controller.run_action("browser_batch_interact", steps=steps))

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert any(message in error for error in result["validation_errors"])
    assert called is False


def test_batch_interact_reports_partial_when_any_executed_step_failed() -> None:
    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        del js_code
        return {
            "ok": True,
            "steps": [
                {"index": 0, "op": "fill", "ok": True},
                {"index": 1, "op": "wait_for_text", "ok": False, "error": "timeout"},
            ],
            "error": None,
        }

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=[
                {"op": "fill", "selector": "#q", "value": "OpenJiuwen"},
                {"op": "wait_for_text", "text": "Results", "optional": True},
            ],
        )
    )

    assert result["ok"] is False
    assert result["status"] == "partial"
    assert result["error"] == "one or more batch steps failed"


def test_batch_interact_unwraps_compact_rpc_and_merges_metrics() -> None:
    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        return {
            "__browser_compact_rpc__": True,
            "payload": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "ok": True,
                                "steps": [],
                                "elapsed_ms": 7,
                                "internal_steps_elapsed_ms": 5,
                            }
                        ),
                    }
                ]
            },
            "rpc_metrics": {
                "tool_name": "browser_run_code_unsafe",
                "tool_resolution_elapsed_ms": 2,
                "transport_invoke_elapsed_ms": 8,
                "rpc_total_elapsed_ms": 10,
                "script_size_bytes": len(js_code),
                "response_size_bytes": 123,
            },
        }

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)

    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=[
                {"op": "fill", "selector": "#q", "value": "test"},
                {"op": "press", "key": "Enter"},
            ],
        )
    )

    assert result["ok"] is True
    assert result["metrics"]["tool_name"] == "browser_run_code_unsafe"
    assert result["metrics"]["tool_resolution_elapsed_ms"] == 2
    assert result["metrics"]["transport_invoke_elapsed_ms"] == 8
    assert result["metrics"]["rpc_total_elapsed_ms"] == 10
    assert result["metrics"]["internal_steps_elapsed_ms"] == 5


def test_batch_interact_returns_compact_condition_observations() -> None:
    async def fake_code_executor(js_code: str) -> dict[str, Any]:
        del js_code
        return {
            "ok": True,
            "steps": [
                {"index": 0, "op": "wait_for_url", "ok": True, "elapsed_ms": 12},
                {"index": 1, "op": "wait_for_result_count", "ok": True, "elapsed_ms": 9},
            ],
            "conditions": [
                {
                    "index": 0,
                    "op": "wait_for_url",
                    "ok": True,
                    "elapsed_ms": 12,
                    "observed": {"url": "https://example.test/results"},
                },
                {
                    "index": 1,
                    "op": "wait_for_result_count",
                    "ok": True,
                    "elapsed_ms": 9,
                    "observed": {"count": 10},
                },
            ],
            "url": "https://example.test/results",
            "title": "Results",
        }

    controller.register_example_actions()
    controller.bind_code_executor(fake_code_executor)
    result = _run(
        controller.run_action(
            "browser_batch_interact",
            steps=[
                {"op": "wait_for_url", "url_contains": "/results"},
                {"op": "wait_for_result_count", "selector": ".result", "min_count": 1},
            ],
        )
    )

    assert result["conditions"][0]["observed"] == {"url": "https://example.test/results"}
    assert result["conditions"][1]["observed"] == {"count": 10}
    assert "url" not in result
    assert "title" not in result


def test_batch_interact_script_uses_fail_fast_targets_and_condition_waits() -> None:
    js = _build_batch_interact_script(
        {
            "steps": [
                {"op": "wait_for_url", "url_contains": "/results"},
                {
                    "op": "wait_for_result_count",
                    "selector": ".result",
                    "min_count": 1,
                },
            ]
        }
    )

    assert "payload.timeout_ms || 2500" in js
    assert "payload.condition_timeout_ms || 10000" in js
    assert "conditionOps.has(op) ? defaultConditionTimeout : defaultTimeout" in js
    assert "target must match exactly one element" in js
    assert "match_count" in js
    assert "generation_id" in js
    assert "op === 'wait_for_url'" in js
    assert "op === 'wait_for_first_card_title'" in js
    assert "op === 'wait_for_sort_state'" in js
    assert "op === 'wait_for_result_count'" in js
    assert "op === 'wait_for_dom_text_change'" in js
    assert "op === 'wait_for_stable'" in js
    assert "op === 'wait_for_tab'" in js
    assert "initialTabCount" in js
    assert "page.context().pages()" in js
    assert "await matchedPage.bringToFront()" in js
    assert "visible_text_preview" not in js


def test_wait_for_tab_is_a_valid_condition_and_activates_new_tab(tmp_path: Path) -> None:
    assert (
        controller.validate_batch_steps(
            [
                {"op": "click", "selector": "#open"},
                {"op": "wait_for_tab", "url_contains": "/new"},
            ]
        )
        == []
    )

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping generated JavaScript execution test")

    js_function = _build_batch_interact_script(
        {
            "steps": [
                {"op": "click", "selector": "#open"},
                {"op": "wait_for_tab", "url_contains": "/new"},
            ],
            "timeout_ms": 500,
            "condition_timeout_ms": 1000,
            "generation_id": "g2",
        }
    )
    runner = tmp_path / "run_wait_for_tab.js"
    runner.write_text(
        textwrap.dedent(
            f"""
            const fn = ({js_function});
            const calls = [];
            const pages = [];
            const context = {{ pages: () => pages }};
            const newPage = {{
              context: () => context,
              url: () => 'https://example.test/new',
              title: async () => 'New tab',
              bringToFront: async () => calls.push('activated'),
            }};
            class FakeLocator {{
              first() {{ return this; }}
              async count() {{ return 1; }}
              async waitFor() {{}}
              async isVisible() {{ return true; }}
              async isEnabled() {{ return true; }}
              async click() {{ pages.push(newPage); }}
            }}
            const oldPage = {{
              context: () => context,
              locator: () => new FakeLocator(),
              url: () => 'https://example.test/old',
              title: async () => 'Old tab',
            }};
            pages.push(oldPage);
            fn(oldPage).then((result) => console.log(JSON.stringify({{ result, calls }})));
            """
        ),
        encoding="utf-8",
    )

    completed = subprocess.run([node, str(runner)], check=True, text=True, capture_output=True)
    payload = json.loads(completed.stdout)
    assert payload["result"]["ok"] is True
    assert payload["result"]["url"] == "https://example.test/new"
    assert payload["result"]["conditions"][0]["observed"]["count"] == 2
    assert payload["calls"] == ["activated"]


def test_batch_preflight_rejects_later_ambiguous_target_before_any_click(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping generated JavaScript execution test")

    js_function = _build_batch_interact_script(
        {
            "steps": [
                {"op": "click", "selector": "#first"},
                {"op": "click", "selector": ".duplicate"},
            ],
            "timeout_ms": 2500,
            "generation_id": "g4",
        }
    )
    runner = tmp_path / "run_batch_preflight.js"
    runner.write_text(
        textwrap.dedent(
            f"""
            const fn = ({js_function});
            const clicks = [];
            class FakeLocator {{
              constructor(selector) {{ this.selector = selector; }}
              first() {{ return this; }}
              async count() {{ return this.selector === '.duplicate' ? 2 : 1; }}
              async waitFor() {{}}
              async isVisible() {{ return true; }}
              async isEnabled() {{ return true; }}
              async click() {{ clicks.push(this.selector); }}
            }}
            const page = {{
              locator: (selector) => new FakeLocator(selector),
              url: () => 'https://example.test/search',
              title: async () => 'Search',
            }};
            fn(page).then((result) => console.log(JSON.stringify({{ result, clicks }})));
            """
        ),
        encoding="utf-8",
    )

    completed = subprocess.run([node, str(runner)], check=True, text=True, capture_output=True)
    payload = json.loads(completed.stdout)

    assert payload["result"]["ok"] is False
    assert payload["result"]["status"] == "failed"
    assert payload["result"]["steps"][0]["phase"] == "preflight"
    assert "matched 2" in payload["result"]["error"]
    assert payload["clicks"] == []


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
    assert [step["op"] for step in payload["result"]["steps"]] == [step["op"] for step in _realistic_booking_steps()]
    assert ["fill", "label", "First name", "John", 3000] in payload["calls"]
    assert ["keyboard.type", "Singapore", 0] in payload["calls"]
    assert ["click", "text", "Singapore (SIN)", 3000] in payload["calls"]
    assert any(call[0] == "selectOption" and call[3] == {"label": "Singapore"} for call in payload["calls"])
    assert ["setChecked", "label", "Male", True, 3000] in payload["calls"]


def test_safe_read_selector_preflight_requires_one_visible_match() -> None:
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value='{"ok":true,"results":[{"index":0,"selector":".title","match_count":1,"visible":true}]}'
    )

    _run(runtime._validate_safe_read_locators(
        [{"op": "extract_text", "selector": ".title", "field": "title"}]
    ))

    runtime._call_playwright_run_code_unsafe.return_value = (
        '{"ok":true,"results":[{"index":0,"selector":".title",'
        '"match_count":2,"visible":true}]}'
    )
    with pytest.raises(ValueError, match="exactly one visible element"):
        _run(runtime._validate_safe_read_locators(
            [{"op": "extract_text", "selector": ".title", "field": "title"}]
        ))


def test_batch_target_contract_requires_target_id_for_mutations() -> None:
    errors = BrowserAgentRuntime._validate_batch_target_contract(
        [{"op": "click", "selector": "#submit"}]
    )

    assert errors == ["steps[0] op=click requires target_id from the current PageState"]
    assert BrowserAgentRuntime._validate_batch_target_contract(
        [{"op": "extract_text", "selector": ".title", "field": "title"}]
    ) == []
