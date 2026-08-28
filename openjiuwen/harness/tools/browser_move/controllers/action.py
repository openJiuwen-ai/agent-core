# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime-native custom action controller for Playwright runtime.
It exposes a lightweight action registry consumed by the MCP wrapper.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
)
from .base import BaseController

try:
    # Normal package import (openjiuwen.harness.tools.browser_move.controllers.action)
    from ..utils.env import resolve_upload_root
    from ..utils.parsing import extract_json_object
except ImportError:  # pragma: no cover
    # When imported as top-level `controllers.action` (playwright_runtime/__init__.py
    # appends browser_move/ to sys.path), `..utils` would go beyond the top-level
    # package. Fall back to sibling top-level `utils.*` imports.
    from utils.env import resolve_upload_root
    from utils.parsing import extract_json_object

ActionResult = dict[str, Any]
ActionHandler = Callable[..., Awaitable[Any] | Any]
CodeExecutor = Callable[[str], Awaitable[Any]]

_BATCH_SUPPORTED_OPS = frozenset(
    {
        "click",
        "fill",
        "type",
        "autocomplete",
        "select_visible_text",
        "press",
        "select_option",
        "set_checked",
        "wait_for_selector",
        "wait_for_text",
        "wait_for_load_state",
        "wait_for_url",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
        "wait_for_tab",
        "sleep",
        "extract_text",
        "extract_value",
        "screenshot",
    }
)
_BATCH_LOCATOR_KEYS = (
    "target_id",
    "ref",
    "selector",
    "role",
    "label",
    "placeholder",
    "testid",
)
_BATCH_CONDITION_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_text",
        "wait_for_load_state",
        "wait_for_url",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
        "wait_for_tab",
    }
)
_BATCH_PRIMARY_TARGET_OPS = frozenset(
    {
        "click",
        "fill",
        "type",
        "autocomplete",
        "select_option",
        "set_checked",
        "extract_text",
        "extract_value",
    }
)
_BATCH_OPTION_TARGET_OPS = frozenset({"autocomplete", "select_visible_text"})
_BATCH_SELECTOR_CONDITION_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
    }
)


def _locator_strategies(step: Mapping[str, Any], *, include_text: bool = True) -> list[str]:
    strategies: list[str] = []
    for key in _BATCH_LOCATOR_KEYS:
        if step.get(key) not in (None, ""):
            strategies.append(key)
    if include_text and step.get("text") not in (None, ""):
        strategies.append("text")
    return strategies


def _option_locator_strategies(step: Mapping[str, Any]) -> list[str]:
    strategies: list[str] = []
    alias_groups = (
        ("option_target_id", "choose_target_id"),
        ("option_ref", "choose_ref"),
        ("option_selector", "choose_selector"),
        ("option_role", "choose_role"),
        ("option_text", "choose_text", "text_to_choose"),
    )
    for aliases in alias_groups:
        populated = [key for key in aliases if step.get(key) not in (None, "")]
        if len(populated) > 1:
            strategies.extend(populated)
        elif populated:
            strategies.append(populated[0])
    return strategies


def _validate_batch_locator_contract(
    index: int,
    op: str,
    step: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    locator_strategies = _locator_strategies(
        step,
        include_text=op
        not in {
            "wait_for_text",
            "wait_for_sort_state",
            "wait_for_first_card_title",
        },
    )
    option_strategies = _option_locator_strategies(step)
    if step.get("name") not in (None, "") and step.get("role") in (None, ""):
        errors.append(f"steps[{index}].name requires role")
    if op in _BATCH_PRIMARY_TARGET_OPS and not locator_strategies:
        errors.append(f"steps[{index}] op={op} requires a target")
    elif op in _BATCH_PRIMARY_TARGET_OPS and len(locator_strategies) > 1:
        errors.append(
            f"steps[{index}] op={op} requires exactly one locator strategy; received {', '.join(locator_strategies)}"
        )
    if op == "press" and len(locator_strategies) > 1:
        errors.append(
            f"steps[{index}] op=press accepts at most one locator strategy; received {', '.join(locator_strategies)}"
        )
    if op in _BATCH_SELECTOR_CONDITION_OPS:
        invalid_locators = [key for key in locator_strategies if key != "selector"]
        if invalid_locators:
            errors.append(f"steps[{index}] op={op} accepts only selector; received {', '.join(invalid_locators)}")
    elif op not in _BATCH_PRIMARY_TARGET_OPS and op != "press" and locator_strategies:
        errors.append(
            f"steps[{index}] op={op} does not accept a primary locator; received {', '.join(locator_strategies)}"
        )
    if op in _BATCH_OPTION_TARGET_OPS and not option_strategies:
        errors.append(f"steps[{index}] {op} requires an option target")
    elif op in _BATCH_OPTION_TARGET_OPS and len(option_strategies) > 1:
        errors.append(
            f"steps[{index}] op={op} requires exactly one option locator strategy; "
            f"received {', '.join(option_strategies)}"
        )
    option_name = step.get("option_name") or step.get("choose_name")
    option_role = step.get("option_role") or step.get("choose_role")
    if option_name not in (None, "") and option_role in (None, ""):
        errors.append(f"steps[{index}] option_name requires option_role")
    return errors


def _validate_batch_op_requirements(
    index: int,
    op: str,
    step: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    if op in {"fill", "type", "autocomplete"} and "value" not in step:
        suffix = "; use value, not text" if "text" in step else ""
        errors.append(f"steps[{index}] op={op} requires value{suffix}")
    option_value_keys = (
        "value",
        "values",
        "option_value",
        "option_text",
        "option_label",
        "label_value",
        "choose_text",
        "index",
    )
    has_option_value = False
    for key in option_value_keys:
        if step.get(key) is not None:
            has_option_value = True
            break
    if op == "select_option" and not has_option_value:
        errors.append(f"steps[{index}] select_option requires an option value")
    if op == "press" and not str(step.get("key") or "").strip():
        errors.append(f"steps[{index}] press requires key")
    if op == "wait_for_selector" and not step.get("selector"):
        errors.append(f"steps[{index}] wait_for_selector requires selector")
    if op == "wait_for_text" and not step.get("text"):
        errors.append(f"steps[{index}] wait_for_text requires text")
    if op == "wait_for_url" and not any(
        step.get(key) for key in ("url", "expected_url", "url_contains", "url_pattern")
    ):
        errors.append(f"steps[{index}] wait_for_url requires a URL condition")
    if op == "wait_for_first_card_title" and not step.get("selector"):
        errors.append(f"steps[{index}] wait_for_first_card_title requires selector")
    if op == "wait_for_sort_state" and not (
        step.get("selector") and any(step.get(key) is not None for key in ("expected_value", "value", "text"))
    ):
        errors.append(f"steps[{index}] wait_for_sort_state requires selector and expected value")
    if op == "wait_for_result_count" and not (
        step.get("selector") and any(step.get(key) is not None for key in ("count", "min_count", "max_count"))
    ):
        errors.append(f"steps[{index}] wait_for_result_count requires selector and count")
    if op == "wait_for_dom_text_change" and not step.get("selector"):
        errors.append(f"steps[{index}] wait_for_dom_text_change requires selector")
    if op == "sleep":
        try:
            sleep_ms = int(step.get("ms", step.get("time_ms", 0)))
        except (TypeError, ValueError):
            sleep_ms = -1
        if sleep_ms < 0:
            errors.append(f"steps[{index}] sleep requires non-negative ms")
    return errors


def validate_batch_steps(steps: Any) -> list[str]:
    """Validate the strict per-operation Batch contract before browser execution."""
    if not isinstance(steps, list) or not steps:
        return ["steps is required and must be a non-empty list"]
    if len(steps) > 25:
        return ["browser_batch_interact accepts at most 25 steps"]

    errors: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            errors.append(f"steps[{index}] must be an object")
            continue
        op = str(step.get("op") or "").strip().lower()
        if op not in _BATCH_SUPPORTED_OPS:
            errors.append(f"steps[{index}].op is unsupported: {op or '<missing>'}")
            continue
        errors.extend(_validate_batch_op_requirements(index, op, step))
        errors.extend(_validate_batch_locator_contract(index, op, step))
    return errors


def _unwrap_browser_code_result(raw: Any) -> Any:
    """Extract text/data payloads from common browser_run_code/MCP result shapes."""
    if isinstance(raw, dict):
        if raw.get("__browser_compact_rpc__") is True:
            return _unwrap_browser_code_result(raw.get("payload"))

        content = raw.get("content")
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(str(item.get("text") or ""))
            if texts:
                return "\n".join(texts)

        for key in ("result", "text", "data", "final"):
            if key in raw:
                return raw.get(key)

    return raw


def _browser_rpc_metrics(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("__browser_compact_rpc__") is not True:
        return {}
    metrics = raw.get("rpc_metrics")
    return dict(metrics) if isinstance(metrics, Mapping) else {}


def _compact_batch_step(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    ok = bool(item.get("ok", False))
    compact = {
        "index": item.get("index"),
        "op": str(item.get("op") or ""),
        "ok": ok,
        "status": "completed" if ok else "failed",
        "elapsed_ms": int(item.get("elapsed_ms") or 0),
    }
    for key in ("phase", "error"):
        if item.get(key) not in (None, ""):
            compact[key] = item.get(key)
    return compact


def _compact_batch_conditions(parsed: Mapping[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supplied = parsed.get("conditions")
    source = supplied if isinstance(supplied, list) else []
    if not source:
        source = [item for item in steps if str(item.get("op") or "") in _BATCH_CONDITION_OPS]

    conditions: list[dict[str, Any]] = []
    for item in source:
        compact = _compact_batch_step(item)
        if compact is None:
            continue
        observed = item.get("observed") if isinstance(item, Mapping) else None
        if observed not in (None, "", [], {}):
            compact["observed"] = observed
        conditions.append(compact)
    return conditions


def _compact_extraction_provenance(
    steps: list[dict[str, Any]],
    generation_id: str,
) -> dict[str, dict[str, Any]]:
    provenance: dict[str, dict[str, Any]] = {}
    for item in steps:
        if not isinstance(item, Mapping) or not item.get("ok"):
            continue
        if str(item.get("op") or "") not in {"extract_text", "extract_value"}:
            continue
        field_name = str(item.get("field") or "").strip()
        if not field_name:
            continue
        provenance[field_name] = {
            "selector": str(item.get("selector") or "")[:600],
            "raw_text": str(item.get("raw_text") or "")[:1000],
            "generation_id": generation_id,
        }
    return provenance


class RuntimeRunner(Protocol):
    async def __call__(
        self,
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        ...


_ACTIONS: dict[str, ActionHandler] = {}
_ACTION_SPECS: dict[str, dict[str, Any]] = {}
_RUNTIME_RUNNER: RuntimeRunner | None = None
_CODE_EXECUTOR: CodeExecutor | None = None
_LOCK = asyncio.Lock()
_ctx_browser_worker_action: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "playwright_runtime_browser_worker_action",
    default=False,
)
_RECURSIVE_BROWSER_ACTIONS = frozenset({"browser_task", "run_browser_task"})


@contextlib.contextmanager
def browser_worker_action_context():
    token = _ctx_browser_worker_action.set(True)
    try:
        yield
    finally:
        _ctx_browser_worker_action.reset(token)


class ActionController(BaseController):
    """Instance-scoped registry and dispatcher for custom actions."""

    def __init__(
        self,
        *,
        actions: dict[str, ActionHandler] | None = None,
        action_specs: dict[str, dict[str, Any]] | None = None,
        runtime_runner: RuntimeRunner | None = None,
        code_executor: CodeExecutor | None = None,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self._actions: dict[str, ActionHandler] = actions if actions is not None else {}
        self._action_specs: dict[str, dict[str, Any]] = action_specs if action_specs is not None else {}
        self._runtime_runner: RuntimeRunner | None = runtime_runner
        self._code_executor: CodeExecutor | None = code_executor
        self._lock: asyncio.Lock = lock if lock is not None else asyncio.Lock()

    @property
    def runtime_runner(self) -> RuntimeRunner | None:
        return self._runtime_runner

    @property
    def code_executor(self) -> CodeExecutor | None:
        return self._code_executor

    def bind_runtime(self, runtime: Any) -> None:
        run_browser_task = getattr(runtime, "run_browser_task", None)
        if run_browser_task is None or not callable(run_browser_task):
            raise ValueError("runtime must expose an async run_browser_task(...) method")

        async def _runner(
            *,
            task: str,
            session_id: str | None = None,
            request_id: str | None = None,
            timeout_s: int | None = None,
        ) -> dict[str, Any]:
            return await run_browser_task(
                task=task,
                session_id=session_id,
                request_id=request_id,
                timeout_s=timeout_s,
            )

        self.bind_runtime_runner(_runner)

    def bind_runtime_runner(self, runner: RuntimeRunner | None) -> None:
        self._runtime_runner = runner

    def clear_runtime_runner(self) -> None:
        self.bind_runtime_runner(None)

    def bind_code_executor(self, executor: CodeExecutor | None) -> None:
        self._code_executor = executor

    def clear_code_executor(self) -> None:
        self._code_executor = None

    def register_action(self, name: str, handler: ActionHandler, *, overwrite: bool = True) -> None:
        action_name = _normalize_action_name(name)
        if not action_name:
            raise ValueError("action name must be non-empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not overwrite and action_name in self._actions:
            raise ValueError(f"action already exists: {action_name}")
        self._actions[action_name] = handler

    def register_action_spec(
        self,
        name: str,
        *,
        summary: str = "",
        when_to_use: str = "",
        params: dict[str, str] | None = None,
    ) -> None:
        action_name = _normalize_action_name(name)
        if not action_name:
            raise ValueError("action name must be non-empty")
        self._action_specs[action_name] = {
            "summary": (summary or "").strip(),
            "when_to_use": (when_to_use or "").strip(),
            "params": dict(params or {}),
        }

    def list_actions(self) -> list[str]:
        return sorted(self._actions.keys())

    def describe_actions(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in sorted(self._actions.keys()):
            spec = self._action_specs.get(name, {})
            result[name] = {
                "summary": str(spec.get("summary", "") or ""),
                "when_to_use": str(spec.get("when_to_use", "") or ""),
                "params": dict(spec.get("params", {}) or {}),
            }
        return result

    async def run_action(
        self,
        action: str,
        session_id: str = "",
        request_id: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        action_name = _normalize_action_name(action)
        sid = (session_id or "").strip()
        rid = (request_id or "").strip()
        param_keys = ",".join(sorted(str(key) for key in kwargs.keys()))
        logger.info(
            f"CONTROLLER_ACTION start action={action_name} session_id={sid or '-'} "
            f"request_id={rid or '-'} param_keys={param_keys or '-'}"
        )

        if _ctx_browser_worker_action.get() and action_name in _RECURSIVE_BROWSER_ACTIONS:
            error = (
                "recursive_browser_task_blocked: browser workers must not invoke "
                "browser_task/run_browser_task via browser_custom_action; return a JSON error instead"
            )
            logger.warning(
                f"CONTROLLER_ACTION blocked action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} error={error}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": error,
            }

        handler = self._actions.get(action_name)
        if handler is None:
            logger.warning(
                f"CONTROLLER_ACTION unknown action={action_name} session_id={sid or '-'} request_id={rid or '-'}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": f"unknown action: {action_name}",
            }

        try:
            async with self._lock:
                raw = await _maybe_await(handler(session_id=sid, request_id=rid, **kwargs))

            response = dict(raw) if isinstance(raw, dict) else {"result": raw}
            response.setdefault("ok", True)
            response.setdefault("action", action_name)
            response.setdefault("session_id", sid)
            response.setdefault("request_id", rid)
            response.setdefault("error", None)
            _ok = bool(response.get("ok", False))
            _err = response.get("error") if not _ok else None
            logger.info(
                f"CONTROLLER_ACTION end action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} ok={_ok}" + (f" error={_err!r}" if _err else "")
            )
            return response
        except Exception as exc:
            logger.error(
                f"CONTROLLER_ACTION error action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} error={exc}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": str(exc),
            }

    def register_builtin_actions(self) -> None:
        register_builtin_actions(controller=self)

    def register_example_actions(self) -> None:
        register_builtin_actions(controller=self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "actions": dict(self._actions),
            "action_specs": copy.deepcopy(self._action_specs),
            "runtime_runner": self._runtime_runner,
            "code_executor": self._code_executor,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self._actions.clear()
        self._actions.update(dict(snapshot.get("actions", {})))
        self._action_specs.clear()
        self._action_specs.update(copy.deepcopy(dict(snapshot.get("action_specs", {}))))
        self._runtime_runner = snapshot.get("runtime_runner")
        self._code_executor = snapshot.get("code_executor")


def _normalize_action_name(name: str) -> str:
    return (name or "").strip().lower()


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _normalize_offset(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        x = _to_int_or_none(value.get("x"))
        y = _to_int_or_none(value.get("y"))
    else:
        x = _to_int_or_none(getattr(value, "x", None))
        y = _to_int_or_none(getattr(value, "y", None))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _build_run_code_task(js_code: str, purpose: str) -> str:
    tool_input = json.dumps({"code": js_code}, ensure_ascii=False)
    return (
        f"Execute this browser operation: {purpose}.\n"
        "Call browser_run_code exactly once with this JSON input:\n"
        f"{tool_input}\n\n"
        "Then return your required top-level response JSON. "
        "Set its `final` field to the exact JSON result returned by browser_run_code."
    )


def _wrap_page_script(*parts: str) -> str:
    return "async (page) => {\n" + "".join(parts) + "}"


def _build_selector_resolution_helpers(payload_json: str) -> str:
    return (
        f"  const params = {payload_json};\n"
        "  if (params.url && String(params.url).trim()) {\n"
        "    await page.goto(String(params.url).trim());\n"
        "  }\n"
        "  const getTextBox = async (query, role) => {\n"
        "    const term = String(query || '').trim().toLowerCase();\n"
        "    if (!term) return null;\n"
        "    return await page.evaluate(({ term, role }) => {\n"
        "      const all = Array.from(document.querySelectorAll('body *'));\n"
        "      const score = (el) => {\n"
        "        const text = String(el.textContent || '').trim().toLowerCase();\n"
        "        if (!text) return -1;\n"
        "        if (text === term) return 2;\n"
        "        if (text.includes(term)) return 1;\n"
        "        return -1;\n"
        "      };\n"
        "      const toVisibleBox = (el) => {\n"
        "        if (!el) return null;\n"
        "        const candidates = role === 'target' && el.parentElement ? [el.parentElement, el] : [el];\n"
        "        for (const candidate of candidates) {\n"
        "          const rect = candidate.getBoundingClientRect();\n"
        "          if (rect && rect.width > 0 && rect.height > 0) {\n"
        "            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };\n"
        "          }\n"
        "        }\n"
        "        return null;\n"
        "      };\n"
        "      const exactMatches = all.filter((el) => score(el) === 2);\n"
        "      for (const el of exactMatches) {\n"
        "        const box = toVisibleBox(el);\n"
        "        if (box) return box;\n"
        "      }\n"
        "      const fuzzyMatches = all.filter((el) => score(el) === 1);\n"
        "      for (const el of fuzzyMatches) {\n"
        "        const box = toVisibleBox(el);\n"
        "        if (box) return box;\n"
        "      }\n"
        "      return null;\n"
        "    }, { term, role });\n"
        "  };\n"
        "  const extractTextFromHasText = (s) => {\n"
        "    if (!s || typeof s !== 'string') return s;\n"
        "    const m = String(s).match(/:has-text\\s*\\(\\s*['\"]([^'\"]*)['\"]\\s*\\)/);\n"
        "    return m ? m[1] : s;\n"
        "  };\n"
        "  const getPoint = async (selector, offset, role) => {\n"
        "    let box = null;\n"
        "    if (selector) {\n"
        "      try {\n"
        "        const el = await page.$(selector);\n"
        "        if (el) box = await el.boundingBox();\n"
        "      } catch (_err) {\n"
        "        box = null;\n"
        "      }\n"
        "    }\n"
        "    if (!box) {\n"
        "      const textTerm = extractTextFromHasText(selector) || selector;\n"
        "      box = await getTextBox(textTerm, role);\n"
        "    }\n"
        "    if (!box) return null;\n"
        "    if (offset && Number.isFinite(offset.x) && Number.isFinite(offset.y)) {\n"
        "      return { x: Math.trunc(box.x + offset.x), y: Math.trunc(box.y + offset.y) };\n"
        "    }\n"
        "    return { x: Math.trunc(box.x + box.width / 2), y: Math.trunc(box.y + box.height / 2) };\n"
        "  };\n"
    )


def _build_coordinate_resolution_body() -> str:
    return (
        "  let source = null;\n"
        "  let target = null;\n"
        "  if (params.element_source || params.element_target) {\n"
        "    if (params.element_source) {\n"
        "      source = await getPoint(params.element_source, params.element_source_offset, 'source');\n"
        "      if (!source) {\n"
        "        return { ok: false, error: 'Failed to determine source coordinates from selector."
        ' Use the exact visible text (e.g. "Learn more" not "More information")'
        " or a valid CSS/Playwright selector.', source: null, target: null };\n"
        "      }\n"
        "    }\n"
        "    if (params.element_target) {\n"
        "      target = await getPoint(params.element_target, params.element_target_offset, 'target');\n"
        "      if (!target) {\n"
        "        return { ok: false, error: 'Failed to determine target coordinates from selector',"
        " source, target: null };\n"
        "      }\n"
        "    }\n"
        "  } else {\n"
        "    const values = [params.coord_source_x, params.coord_source_y,"
        " params.coord_target_x, params.coord_target_y];\n"
        "    const allFinite = values.every((v) => Number.isFinite(v));\n"
        "    if (!allFinite) {\n"
        "      return { ok: false, error: 'Must provide either source/target selectors"
        " or source/target coordinates' };\n"
        "    }\n"
        "    source = { x: Math.trunc(params.coord_source_x), y: Math.trunc(params.coord_source_y) };\n"
        "    target = { x: Math.trunc(params.coord_target_x), y: Math.trunc(params.coord_target_y) };\n"
        "  }\n"
        "  return { ok: true, source, target, error: null };\n"
    )


def _build_drag_operation_body() -> str:
    return (
        "  let source = null;\n"
        "  let target = null;\n"
        "  if (params.element_source && params.element_target) {\n"
        "    source = await getPoint(params.element_source, params.element_source_offset, 'source');\n"
        "    target = await getPoint(params.element_target, params.element_target_offset, 'target');\n"
        "    if (!source || !target) {\n"
        "      return { ok: false, error: 'Failed to determine source or target coordinates from selectors',"
        " source, target };\n"
        "    }\n"
        "  } else {\n"
        "    const values = [params.coord_source_x, params.coord_source_y,"
        " params.coord_target_x, params.coord_target_y];\n"
        "    const allFinite = values.every((v) => Number.isFinite(v));\n"
        "    if (!allFinite) {\n"
        "      return { ok: false, error: 'Must provide either source/target selectors"
        " or source/target coordinates' };\n"
        "    }\n"
        "    source = { x: Math.trunc(params.coord_source_x), y: Math.trunc(params.coord_source_y) };\n"
        "    target = { x: Math.trunc(params.coord_target_x), y: Math.trunc(params.coord_target_y) };\n"
        "  }\n"
        "  const steps = Math.max(1, Number.isFinite(params.steps) ? Math.trunc(params.steps) : 10);\n"
        "  const delayMs = Math.max(0, Number.isFinite(params.delay_ms) ? Math.trunc(params.delay_ms) : 5);\n"
        "  try {\n"
        "    await page.mouse.move(source.x, source.y);\n"
        "    await page.mouse.down();\n"
        "    for (let i = 1; i <= steps; i += 1) {\n"
        "      const ratio = i / steps;\n"
        "      const x = Math.trunc(source.x + (target.x - source.x) * ratio);\n"
        "      const y = Math.trunc(source.y + (target.y - source.y) * ratio);\n"
        "      await page.mouse.move(x, y);\n"
        "      if (delayMs > 0) {\n"
        "        await page.waitForTimeout(delayMs);\n"
        "      }\n"
        "    }\n"
        "    await page.mouse.move(target.x, target.y);\n"
        "    await page.mouse.move(target.x, target.y);\n"
        "    await page.mouse.up();\n"
        "  } catch (error) {\n"
        "    return {\n"
        "      ok: false,\n"
        "      error: `Error during drag operation: ${String(error)}`,\n"
        "      source,\n"
        "      target,\n"
        "      steps,\n"
        "      delay_ms: delayMs,\n"
        "    };\n"
        "  }\n"
        "  const message = params.element_source && params.element_target\n"
        "    ? `Dragged element '${params.element_source}' to '${params.element_target}'`\n"
        "    : `Dragged from (${source.x}, ${source.y}) to (${target.x}, ${target.y})`;\n"
        "  return { ok: true, message, source, target, steps, delay_ms: delayMs, error: null };\n"
    )


def _build_coordinate_script(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return _wrap_page_script(
        _build_selector_resolution_helpers(payload_json),
        _build_coordinate_resolution_body(),
    )


def _build_drag_script(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return _wrap_page_script(
        _build_selector_resolution_helpers(payload_json),
        _build_drag_operation_body(),
    )


def _build_set_input_files_script(selector: str, paths: list[str]) -> str:
    selector_js = "'" + str(selector).replace("\\", "\\\\").replace("'", "\\'") + "'"
    paths_json = json.dumps(paths)
    return (
        "async (page) => {\n"
        "  try {\n"
        f"    await page.locator({selector_js}).setInputFiles({paths_json});\n"
        f"    return {{ ok: true, selector: {selector_js}, paths: {paths_json} }};\n"
        "  } catch (error) {\n"
        "    const msg = String(error);\n"
        "    if (msg.includes('strict mode violation')) {\n"
        f"      return {{ ok: false, error: msg, selector: {selector_js}, paths: {paths_json},"
        " hint: 'Multiple file inputs matched. Use a more specific selector"
        " (e.g. an id like #file-upload) targeting the visible input.' }};\n"
        "    }\n"
        f"    return {{ ok: false, error: msg, selector: {selector_js}, paths: {paths_json} }};\n"
        "  }\n"
        "}"
    )


def _build_batch_interact_script(payload: dict[str, Any]) -> str:
    """Build a Playwright function that executes compact deterministic browser steps.

    The model provides a structured plan; the runtime constructs the JS, so the
    browser worker does not need to emit arbitrary page scripts for common form
    and search-flow interactions.
    """
    payload_json = json.dumps(payload, ensure_ascii=False)
    return (
        "async (page) => {\n"
        f"  const payload = {payload_json};\n"
        "  const startedAt = Date.now();\n"
        "  const steps = Array.isArray(payload.steps) ? payload.steps : [];\n"
        "  const initialBrowserContext = page && typeof page.context === 'function' ? page.context() : null;\n"
        "  const initialTabCount = initialBrowserContext && typeof initialBrowserContext.pages === 'function'\n"
        "    ? initialBrowserContext.pages().length\n"
        "    : 1;\n"
        "  const defaultTimeout = Math.max(250, Number(payload.timeout_ms || 2500));\n"
        "  const defaultConditionTimeout = Math.max(\n"
        "    defaultTimeout,\n"
        "    Number(payload.condition_timeout_ms || 10000)\n"
        "  );\n"
        "  const conditionOps = new Set([\n"
        "    'wait_for_selector', 'wait_for_text', 'wait_for_load_state', 'wait_for_url',\n"
        "    'wait_for_first_card_title', 'wait_for_sort_state', 'wait_for_result_count',\n"
        "    'wait_for_dom_text_change', 'wait_for_stable', 'wait_for_tab'\n"
        "  ]);\n"
        "  const defaultAfter = Math.max(0, Number(payload.wait_after_each_ms || 0));\n"
        "  const generationId = String(payload.generation_id || 'g0');\n"
        "  const results = [];\n"
        "  const sleep = async (ms) => {\n"
        "    const delay = Math.max(0, Number(ms || 0));\n"
        "    if (delay <= 0) return;\n"
        "    await page.waitForTimeout(delay);\n"
        "  };\n"
        "  const compactText = (value, maxLen = 1200) => String(value || '')\n"
        "    .replace(/\\s+/g, ' ')\n"
        "    .trim()\n"
        "    .slice(0, maxLen);\n"
        "  const hasTarget = (step) => !!(\n"
        "    step.selector || step.role || step.label || step.placeholder ||\n"
        "    step.text || step.testid\n"
        "  );\n"
        "  const selectAllKey = 'ControlOrMeta+A';\n"
        "  const locatorFromStep = (step) => {\n"
        "    if (step.selector) return page.locator(String(step.selector));\n"
        "    if (step.role) {\n"
        "      if (step.name !== undefined && step.name !== null && String(step.name).length > 0) {\n"
        "        return page.getByRole(\n"
        "          String(step.role),\n"
        "          { name: String(step.name), exact: !!step.exact }\n"
        "        );\n"
        "      }\n"
        "      return page.getByRole(String(step.role));\n"
        "    }\n"
        "    if (step.label) {\n"
        "      return page.getByLabel(\n"
        "        String(step.label),\n"
        "        { exact: !!step.exact }\n"
        "      );\n"
        "    }\n"
        "    if (step.placeholder) {\n"
        "      return page.getByPlaceholder(\n"
        "        String(step.placeholder),\n"
        "        { exact: !!step.exact }\n"
        "      );\n"
        "    }\n"
        "    if (step.testid) return page.getByTestId(String(step.testid));\n"
        "    if (step.text) {\n"
        "      return page.getByText(\n"
        "        String(step.text),\n"
        "        { exact: !!step.exact }\n"
        "      );\n"
        "    }\n"
        "    throw new Error('step needs selector, role, label, placeholder, testid, or text');\n"
        "  };\n"
        "  const optionLocatorFromStep = (step) => {\n"
        "    if (step.option_selector || step.choose_selector) {\n"
        "      return page.locator(\n"
        "        String(step.option_selector || step.choose_selector)\n"
        "      );\n"
        "    }\n"
        "    if (step.option_role || step.choose_role) {\n"
        "      const role = String(step.option_role || step.choose_role);\n"
        "      const name = step.option_name ?? step.choose_name ?? step.choose_text ?? step.option_text;\n"
        "      if (name !== undefined && name !== null && String(name).length > 0) {\n"
        "        return page.getByRole(\n"
        "          role,\n"
        "          { name: String(name), exact: !!step.exact }\n"
        "        );\n"
        "      }\n"
        "      return page.getByRole(role);\n"
        "    }\n"
        "    const chosenText = step.choose_text ?? step.option_text ?? step.text_to_choose ?? step.value;\n"
        "    if (chosenText !== undefined && chosenText !== null && String(chosenText).length > 0) {\n"
        "      return page.getByText(\n"
        "        String(chosenText),\n"
        "        { exact: !!step.exact }\n"
        "      );\n"
        "    }\n"
        "    throw new Error(\n"
        "      'autocomplete/select_visible_text needs choose_text/option_text or option selector'\n"
        "    );\n"
        "  };\n"
        "  const firstLocator = (locator) => {\n"
        "    return locator && typeof locator.first === 'function' ? locator.first() : locator;\n"
        "  };\n"
        "  const validateTarget = async (locator, timeout, requireEnabled = true) => {\n"
        "    const matchCount = locator && typeof locator.count === 'function'\n"
        "      ? await locator.count()\n"
        "      : 1;\n"
        "    if (matchCount !== 1) {\n"
        "      throw new Error(`target must match exactly one element; matched ${matchCount}`);\n"
        "    }\n"
        "    const target = firstLocator(locator);\n"
        "    if (target && typeof target.waitFor === 'function') {\n"
        "      await target.waitFor({ state: 'visible', timeout });\n"
        "    }\n"
        "    const visible = target && typeof target.isVisible === 'function'\n"
        "      ? await target.isVisible()\n"
        "      : true;\n"
        "    const enabled = target && typeof target.isEnabled === 'function'\n"
        "      ? await target.isEnabled()\n"
        "      : true;\n"
        "    if (!visible) throw new Error('target is not visible');\n"
        "    if (requireEnabled && !enabled) throw new Error('target is not enabled');\n"
        "    return {\n"
        "      locator: target,\n"
        "      metadata: { match_count: matchCount, visible, enabled, generation_id: generationId }\n"
        "    };\n"
        "  };\n"
        "  const pollUntil = async (predicate, timeout, pollInterval, stableMs = 0) => {\n"
        "    const started = Date.now();\n"
        "    const interval = Math.max(50, Math.min(1000, Number(pollInterval || 100)));\n"
        "    let stableSince = 0;\n"
        "    let lastValue = null;\n"
        "    while (Date.now() - started <= timeout) {\n"
        "      const observation = await predicate();\n"
        "      if (observation && observation.ok) {\n"
        "        if (stableMs <= 0) return observation;\n"
        "        const serialized = JSON.stringify(observation.value);\n"
        "        if (serialized === lastValue) {\n"
        "          if (!stableSince) stableSince = Date.now();\n"
        "          if (Date.now() - stableSince >= stableMs) return observation;\n"
        "        } else {\n"
        "          lastValue = serialized;\n"
        "          stableSince = Date.now();\n"
        "        }\n"
        "      } else {\n"
        "        stableSince = 0;\n"
        "        lastValue = null;\n"
        "      }\n"
        "      await sleep(interval);\n"
        "    }\n"
        "    throw new Error(`condition timed out after ${timeout}ms`);\n"
        "  };\n"
        "  const clickWithTransientRetry = async (step, timeout, option = false, initialChecked = null) => {\n"
        "    const started = Date.now();\n"
        "    let lastError = null;\n"
        "    for (let attempt = 0; attempt < 2; attempt += 1) {\n"
        "      const remaining = Math.max(250, timeout - (Date.now() - started));\n"
        "      try {\n"
        "        const locator = option ? optionLocatorFromStep(step) : locatorFromStep(step);\n"
        "        const checked = attempt === 0 && initialChecked\n"
        "          ? initialChecked\n"
        "          : await validateTarget(locator, remaining, true);\n"
        "        await checked.locator.click({ timeout: remaining });\n"
        "        return checked;\n"
        "      } catch (error) {\n"
        "        lastError = error;\n"
        "        const message = String(error && error.message ? error.message : error).toLowerCase();\n"
        "        const transient = /(detached|not attached|intercept|not stable|"
        "outside of the viewport)/.test(message);\n"
        "        if (!transient || attempt > 0 || Date.now() - started >= timeout) throw error;\n"
        "        await sleep(Math.min(100, Math.max(0, timeout - (Date.now() - started))));\n"
        "      }\n"
        "    }\n"
        "    throw lastError || new Error('click failed');\n"
        "  };\n"
        "  const summarizeVisible = async () => {\n"
        "    return await page.evaluate(() => {\n"
        "      const text = (document.body && document.body.innerText) ? document.body.innerText : '';\n"
        "      return text.replace(/\\s+/g, ' ').trim().slice(0, 1200);\n"
        "    });\n"
        "  };\n"
        "  const collectExtracted = (items) => {\n"
        "    const extracted = {};\n"
        "    for (const item of items) {\n"
        "      if (!item || !item.field) continue;\n"
        "      extracted[item.field] = item.text !== undefined ? item.text : item.value;\n"
        "    }\n"
        "    return extracted;\n"
        "  };\n"
        "  const preflightTargets = new Map();\n"
        "  const preflightStartedAt = Date.now();\n"
        "  let preflightElapsedMs = 0;\n"
        "  let preflightTargetCount = 0;\n"
        "  const preflightPrimaryOps = new Set([\n"
        "    'click', 'fill', 'type', 'autocomplete', 'select_option', 'set_checked',\n"
        "    'extract_text', 'extract_value'\n"
        "  ]);\n"
        "  const preflightKey = (index, kind) => `${index}:${kind}`;\n"
        "  const preflightTarget = async (index, kind, locator, timeout, requireEnabled = true) => {\n"
        "    preflightTargetCount += 1;\n"
        "    const checked = await validateTarget(locator, timeout, requireEnabled);\n"
        "    preflightTargets.set(preflightKey(index, kind), checked);\n"
        "    return checked;\n"
        "  };\n"
        "  const getCheckedTarget = async (index, kind, locator, timeout, requireEnabled = true) => {\n"
        "    const cached = preflightTargets.get(preflightKey(index, kind));\n"
        "    return cached || validateTarget(locator, timeout, requireEnabled);\n"
        "  };\n"
        "  for (let i = 0; i < steps.length; i += 1) {\n"
        "    const step = steps[i] || {};\n"
        "    const op = String(step.op || '').trim().toLowerCase();\n"
        "    const timeout = Math.max(250, Number(step.timeout_ms || defaultTimeout));\n"
        "    try {\n"
        "      if (preflightPrimaryOps.has(op) || (op === 'press' && hasTarget(step))) {\n"
        "        const requireEnabled = op !== 'extract_text' && op !== 'extract_value';\n"
        "        await preflightTarget(i, 'primary', locatorFromStep(step), timeout, requireEnabled);\n"
        "      }\n"
        "      if (op === 'select_visible_text') {\n"
        "        await preflightTarget(i, 'option', optionLocatorFromStep(step), timeout);\n"
        "      } else if (op === 'autocomplete' && step.resolved_option_target_id) {\n"
        "        await preflightTarget(i, 'option', optionLocatorFromStep(step), timeout);\n"
        "      }\n"
        "    } catch (error) {\n"
        "      const message = String(error && error.message ? error.message : error);\n"
        "      preflightElapsedMs = Date.now() - preflightStartedAt;\n"
        "      return {\n"
        "        ok: false,\n"
        "        status: 'failed',\n"
        "        error: `batch_preflight_failed: steps[${i}] ${message}`,\n"
        "        steps: [{\n"
        "          index: i, op, ok: false, phase: 'preflight', error: message,\n"
        "          elapsed_ms: Date.now() - startedAt, generation_id: generationId\n"
        "        }],\n"
        "        extracted: collectExtracted(results),\n"
        "        conditions: [],\n"
        "        elapsed_ms: Date.now() - startedAt,\n"
        "        internal_steps_elapsed_ms: 0,\n"
        "        preflight_elapsed_ms: preflightElapsedMs,\n"
        "        preflight_target_count: preflightTargetCount,\n"
        "        url: page.url(),\n"
        "        title: await page.title().catch(() => ''),\n"
        "      };\n"
        "    }\n"
        "  }\n"
        "  preflightElapsedMs = Date.now() - preflightStartedAt;\n"
        "  for (let i = 0; i < steps.length; i += 1) {\n"
        "    const step = steps[i] || {};\n"
        "    const op = String(step.op || '').trim().toLowerCase();\n"
        "    const fallbackTimeout = conditionOps.has(op) ? defaultConditionTimeout : defaultTimeout;\n"
        "    const timeout = Math.max(250, Number(step.timeout_ms || fallbackTimeout));\n"
        "    const stepStartedAt = Date.now();\n"
        "    const item = { index: i, op, ok: false, elapsed_ms: 0, generation_id: generationId };\n"
        "    try {\n"
        "      if (!op) throw new Error('missing op');\n"
        "      if (op === 'click') {\n"
        "        const prechecked = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "        const target = await clickWithTransientRetry(step, timeout, false, prechecked);\n"
        "        item.target_validation = target.metadata;\n"
        "      } else if (op === 'fill' || op === 'type') {\n"
        "        const checked = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "        item.target_validation = checked.metadata;\n"
        "        const target = checked.locator;\n"
        "        const value = String(step.value ?? step.text_value ?? '');\n"
        "        if (op === 'type' || step.mode === 'type') {\n"
        "          await target.click({ timeout });\n"
        "          try {\n"
        "            await page.keyboard.press(selectAllKey);\n"
        "          } catch (_err) {}\n"
        "          try {\n"
        "            await page.keyboard.press('Backspace');\n"
        "          } catch (_err) {}\n"
        "          await page.keyboard.type(\n"
        "            value,\n"
        "            { delay: Math.max(0, Number(step.delay_ms || 0)) }\n"
        "          );\n"
        "        } else {\n"
        "          await target.fill(value, { timeout });\n"
        "        }\n"
        "      } else if (op === 'autocomplete') {\n"
        "        const checked = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "        item.target_validation = checked.metadata;\n"
        "        const target = checked.locator;\n"
        "        const value = String(step.value ?? step.query ?? step.text_value ?? '');\n"
        "        await target.click({ timeout });\n"
        "        try {\n"
        "          await page.keyboard.press(selectAllKey);\n"
        "        } catch (_err) {}\n"
        "        try {\n"
        "          await page.keyboard.press('Backspace');\n"
        "        } catch (_err) {}\n"
        "        if (step.fill_first) {\n"
        "          await target.fill(value, { timeout });\n"
        "        } else {\n"
        "          await page.keyboard.type(\n"
        "            value,\n"
        "            { delay: Math.max(0, Number(step.delay_ms || 0)) }\n"
        "          );\n"
        "        }\n"
        "        if (step.wait_after_type_ms !== undefined) {\n"
        "          await sleep(Math.max(0, Number(step.wait_after_type_ms || 0)));\n"
        "        }\n"
        "        const checkedOption = await getCheckedTarget(i, 'option', optionLocatorFromStep(step), timeout);\n"
        "        item.option_validation = checkedOption.metadata;\n"
        "        await clickWithTransientRetry(step, timeout, true, checkedOption);\n"
        "      } else if (op === 'select_visible_text') {\n"
        "        const checkedOption = await getCheckedTarget(i, 'option', optionLocatorFromStep(step), timeout);\n"
        "        item.target_validation = checkedOption.metadata;\n"
        "        await clickWithTransientRetry(step, timeout, true, checkedOption);\n"
        "      } else if (op === 'press') {\n"
        "        const key = String(step.key || 'Enter');\n"
        "        if (hasTarget(step)) {\n"
        "          const checkedTarget = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "          item.target_validation = checkedTarget.metadata;\n"
        "          await checkedTarget.locator.press(key, { timeout });\n"
        "        } else {\n"
        "          await page.keyboard.press(key);\n"
        "        }\n"
        "      } else if (op === 'select_option') {\n"
        "        const checkedTarget = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "        item.target_validation = checkedTarget.metadata;\n"
        "        const target = checkedTarget.locator;\n"
        "        if (step.values !== undefined) {\n"
        "          const values = Array.isArray(step.values)\n"
        "            ? step.values.map((item) => String(item))\n"
        "            : String(step.values);\n"
        "          await target.selectOption(values, { timeout });\n"
        "        } else {\n"
        "          const option = {};\n"
        "          if (step.value !== undefined || step.option_value !== undefined) {\n"
        "            option.value = String(step.option_value ?? step.value);\n"
        "          }\n"
        "          if (\n"
        "            step.label_value !== undefined ||\n"
        "            step.option_label !== undefined ||\n"
        "            step.option_text !== undefined ||\n"
        "            step.choose_text !== undefined\n"
        "          ) {\n"
        "            option.label = String(\n"
        "              step.label_value ?? step.option_label ?? step.option_text ?? step.choose_text\n"
        "            );\n"
        "          }\n"
        "          if (step.index !== undefined) option.index = Number(step.index);\n"
        "          if (!Object.keys(option).length) {\n"
        "            throw new Error(\n"
        "              'select_option requires value, values, option_value, option_text, '\n"
        "              + 'option_label, label_value, choose_text, or index'\n"
        "            );\n"
        "          }\n"
        "          await target.selectOption(option, { timeout });\n"
        "        }\n"
        "      } else if (op === 'set_checked') {\n"
        "        const checked = step.checked === undefined ? true : !!step.checked;\n"
        "        const checkedTarget = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout);\n"
        "        item.target_validation = checkedTarget.metadata;\n"
        "        await checkedTarget.locator.setChecked(checked, { timeout });\n"
        "      } else if (op === 'wait_for_selector') {\n"
        "        if (!step.selector) throw new Error('wait_for_selector requires selector');\n"
        "        await firstLocator(page.locator(String(step.selector))).waitFor({\n"
        "          state: String(step.state || 'visible'),\n"
        "          timeout,\n"
        "        });\n"
        "      } else if (op === 'wait_for_text') {\n"
        "        if (!step.text) throw new Error('wait_for_text requires text');\n"
        "        await firstLocator(page.getByText(String(step.text), { exact: !!step.exact }))\n"
        "          .waitFor({ timeout });\n"
        "      } else if (op === 'wait_for_load_state') {\n"
        "        await page.waitForLoadState(\n"
        "          String(step.state || 'domcontentloaded'),\n"
        "          { timeout }\n"
        "        );\n"
        "      } else if (op === 'wait_for_url') {\n"
        "        const exactUrl = String(step.url ?? step.expected_url ?? '');\n"
        "        const containsUrl = String(step.url_contains ?? '');\n"
        "        const patternText = String(step.url_pattern ?? '');\n"
        "        const observation = await pollUntil(async () => {\n"
        "          const currentUrl = String(page.url());\n"
        "          let matched = exactUrl ? currentUrl === exactUrl : true;\n"
        "          if (containsUrl) matched = matched && currentUrl.includes(containsUrl);\n"
        "          if (patternText) matched = matched && new RegExp(patternText).test(currentUrl);\n"
        "          return { ok: matched, value: currentUrl };\n"
        "        }, timeout, step.poll_interval_ms);\n"
        "        item.url = observation.value;\n"
        "      } else if (op === 'wait_for_first_card_title') {\n"
        "        const expected = String(step.expected_text ?? step.text ?? '');\n"
        "        const locator = page.locator(String(step.selector));\n"
        "        const observation = await pollUntil(async () => {\n"
        "          const count = await locator.count();\n"
        "          if (count < 1) return { ok: false, value: '' };\n"
        "          const title = compactText(await firstLocator(locator).innerText(), 300);\n"
        "          return { ok: !!title && (!expected || title.includes(expected)), value: title };\n"
        "        }, timeout, step.poll_interval_ms, Number(step.stable_ms || 0));\n"
        "        item.text = observation.value;\n"
        "      } else if (op === 'wait_for_sort_state') {\n"
        "        const locator = page.locator(String(step.selector));\n"
        "        const attribute = String(step.attribute || 'aria-sort');\n"
        "        const expected = String(step.expected_value ?? step.value ?? step.text ?? '');\n"
        "        const observation = await pollUntil(async () => {\n"
        "          if (await locator.count() !== 1) return { ok: false, value: '' };\n"
        "          const target = firstLocator(locator);\n"
        "          const value = attribute === 'text'\n"
        "            ? compactText(await target.innerText(), 300)\n"
        "            : String(await target.getAttribute(attribute) || '');\n"
        "          return { ok: value === expected, value };\n"
        "        }, timeout, step.poll_interval_ms);\n"
        "        item.value = observation.value;\n"
        "      } else if (op === 'wait_for_result_count') {\n"
        "        const locator = page.locator(String(step.selector));\n"
        "        const exactCount = step.count === undefined ? null : Number(step.count);\n"
        "        const minCount = step.min_count === undefined ? null : Number(step.min_count);\n"
        "        const maxCount = step.max_count === undefined ? null : Number(step.max_count);\n"
        "        const observation = await pollUntil(async () => {\n"
        "          const count = await locator.count();\n"
        "          const matched = exactCount !== null\n"
        "            ? count === exactCount\n"
        "            : (minCount === null || count >= minCount) && (maxCount === null || count <= maxCount);\n"
        "          return { ok: matched, value: count };\n"
        "        }, timeout, step.poll_interval_ms, Number(step.stable_ms || 0));\n"
        "        item.count = observation.value;\n"
        "      } else if (op === 'wait_for_dom_text_change') {\n"
        "        const locator = page.locator(String(step.selector));\n"
        "        const previousText = String(step.previous_text ?? step.initial_text ?? '');\n"
        "        const observation = await pollUntil(async () => {\n"
        "          if (await locator.count() !== 1) return { ok: false, value: '' };\n"
        "          const value = compactText(\n"
        "            await firstLocator(locator).innerText(), Number(step.max_chars || 1000)\n"
        "          );\n"
        "          return { ok: value !== previousText, value };\n"
        "        }, timeout, step.poll_interval_ms, Number(step.stable_ms || 0));\n"
        "        item.text = observation.value;\n"
        "      } else if (op === 'wait_for_stable') {\n"
        "        const locator = step.selector ? page.locator(String(step.selector)) : null;\n"
        "        const observation = await pollUntil(async () => {\n"
        "          const value = locator\n"
        "            ? compactText(await firstLocator(locator).innerText(), Number(step.max_chars || 1200))\n"
        "            : await summarizeVisible();\n"
        "          return { ok: true, value };\n"
        "        }, timeout, step.poll_interval_ms, Number(step.stable_ms || 500));\n"
        "        item.stable = true;\n"
        "      } else if (op === 'wait_for_tab') {\n"
        "        if (!page || typeof page.context !== 'function') {\n"
        "          throw new Error('wait_for_tab requires a Playwright browser context');\n"
        "        }\n"
        "        const minTabs = Math.max(1, Number(step.min_tabs || (initialTabCount + 1)));\n"
        "        const expectedUrl = String(step.url || step.expected_url || '');\n"
        "        const urlContains = String(step.url_contains || '');\n"
        "        const urlPattern = String(step.url_pattern || '');\n"
        "        const titleContains = String(step.title_contains || '');\n"
        "        const observation = await pollUntil(async () => {\n"
        "          const pages = page.context().pages();\n"
        "          const tabs = [];\n"
        "          for (let tabIndex = 0; tabIndex < pages.length; tabIndex += 1) {\n"
        "            const candidate = pages[tabIndex];\n"
        "            tabs.push({\n"
        "              index: tabIndex,\n"
        "              url: candidate.url(),\n"
        "              title: await candidate.title().catch(() => '')\n"
        "            });\n"
        "          }\n"
        "          const matches = tabs.filter((tab) => {\n"
        "            if (expectedUrl && tab.url !== expectedUrl) return false;\n"
        "            if (urlContains && !tab.url.includes(urlContains)) return false;\n"
        "            if (urlPattern) {\n"
        "              try { if (!(new RegExp(urlPattern)).test(tab.url)) return false; } catch (_) { return false; }\n"
        "            }\n"
        "            if (titleContains && !tab.title.includes(titleContains)) return false;\n"
        "            return true;\n"
        "          });\n"
        "          const matched = pages.length >= minTabs && matches.length > 0;\n"
        "          const matchedIndex = matched ? matches[matches.length - 1].index : -1;\n"
        "          return { ok: matched, value: { tabs, matched_index: matchedIndex } };\n"
        "        }, timeout, step.poll_interval_ms, Number(step.stable_ms || 0));\n"
        "        item.tabs = observation.value.tabs;\n"
        "        item.count = observation.value.tabs.length;\n"
        "        const matchedIndex = Number(observation.value.matched_index);\n"
        "        if (step.activate !== false && matchedIndex >= 0) {\n"
        "          const matchedPage = page.context().pages()[matchedIndex];\n"
        "          if (matchedPage) {\n"
        "            await matchedPage.bringToFront();\n"
        "            page = matchedPage;\n"
        "            item.url = page.url();\n"
        "          }\n"
        "        }\n"
        "      } else if (op === 'sleep') {\n"
        "        await sleep(Math.max(0, Number(step.ms || step.time_ms || 0)));\n"
        "      } else if (op === 'extract_text') {\n"
        "        const checkedTarget = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout, false);\n"
        "        item.target_validation = checkedTarget.metadata;\n"
        "        const rawText = await checkedTarget.locator.innerText({ timeout });\n"
        "        item.text = compactText(\n"
        "          rawText,\n"
        "          Number(step.max_chars || 500)\n"
        "        );\n"
        "        item.raw_text = String(rawText || '').slice(0, 1000);\n"
        "        item.selector = String(step.selector || '');\n"
        "        item.field = String(step.field || step.name || step.description || `field_${i + 1}`);\n"
        "      } else if (op === 'extract_value') {\n"
        "        const checkedTarget = await getCheckedTarget(i, 'primary', locatorFromStep(step), timeout, false);\n"
        "        item.target_validation = checkedTarget.metadata;\n"
        "        item.value = await checkedTarget.locator.inputValue({ timeout });\n"
        "        item.raw_text = String(item.value || '').slice(0, 1000);\n"
        "        item.selector = String(step.selector || '');\n"
        "        item.field = String(step.field || step.name || step.description || `field_${i + 1}`);\n"
        "      } else if (op === 'screenshot') {\n"
        "        const path = String(step.path || 'screenshots/batch_interact.png');\n"
        "        await page.screenshot({ path, fullPage: !!step.full_page });\n"
        "        item.path = path;\n"
        "      } else {\n"
        "        throw new Error(`unsupported op: ${op}`);\n"
        "      }\n"
        "      item.ok = true;\n"
        "      if (step.wait_after_ms !== undefined) {\n"
        "        await sleep(Math.max(0, Number(step.wait_after_ms || 0)));\n"
        "      } else if (defaultAfter > 0) {\n"
        "        await sleep(defaultAfter);\n"
        "      }\n"
        "    } catch (error) {\n"
        "      item.error = String(error && error.message ? error.message : error);\n"
        "      item.target = {\n"
        "        selector: step.selector || null,\n"
        "        role: step.role || null,\n"
        "        name: step.name || null,\n"
        "        label: step.label || null,\n"
        "        placeholder: step.placeholder || null,\n"
        "        text: step.text || null,\n"
        "        testid: step.testid || null,\n"
        "        choose_text: step.choose_text || step.option_text || null,\n"
        "      };\n"
        "      item.elapsed_ms = Date.now() - stepStartedAt;\n"
        "      results.push(item);\n"
        "      if (step.optional || payload.continue_on_error) continue;\n"
        "      return {\n"
        "        ok: false,\n"
        "        status: results.some((result) => result.ok) ? 'partial' : 'failed',\n"
        "        error: item.error,\n"
        "        steps: results,\n"
        "        extracted: collectExtracted(results),\n"
        "        conditions: results.filter((result) => conditionOps.has(result.op)),\n"
        "        elapsed_ms: Date.now() - startedAt,\n"
        "        internal_steps_elapsed_ms: results.reduce((sum, step) => sum + Number(step.elapsed_ms || 0), 0),\n"
        "        preflight_elapsed_ms: preflightElapsedMs,\n"
        "        preflight_target_count: preflightTargetCount,\n"
        "        url: page.url(),\n"
        "        title: await page.title().catch(() => ''),\n"
        "      };\n"
        "    }\n"
        "    item.elapsed_ms = Date.now() - stepStartedAt;\n"
        "    results.push(item);\n"
        "  }\n"
        "  const extracted = collectExtracted(results);\n"
        "  const failedSteps = results.filter((result) => result && !result.ok);\n"
        "  const conditions = results\n"
        "    .filter((result) => result && conditionOps.has(result.op))\n"
        "    .map((result) => {\n"
        "      const observed = {};\n"
        "      for (const key of ['url', 'text', 'value', 'count', 'stable', 'tabs']) {\n"
        "        if (result[key] !== undefined) observed[key] = result[key];\n"
        "      }\n"
        "      return {\n"
        "        index: result.index, op: result.op, ok: result.ok, error: result.error || null,\n"
        "        elapsed_ms: result.elapsed_ms, observed\n"
        "      };\n"
        "    });\n"
        "  return {\n"
        "    ok: failedSteps.length === 0,\n"
        "    status: failedSteps.length === 0 ? 'completed' : 'partial',\n"
        "    error: failedSteps.length === 0 ? null : 'one or more optional batch steps failed',\n"
        "    steps: results,\n"
        "    extracted,\n"
        "    conditions,\n"
        "    elapsed_ms: Date.now() - startedAt,\n"
        "    internal_steps_elapsed_ms: results.reduce((sum, step) => sum + Number(step.elapsed_ms || 0), 0),\n"
        "    preflight_elapsed_ms: preflightElapsedMs,\n"
        "    preflight_target_count: preflightTargetCount,\n"
        "    url: page.url(),\n"
        "    title: await page.title().catch(() => ''),\n"
        "  };\n"
        "}"
    )


def _build_drag_payload(
    *,
    url: str = "",
    source: str = "",
    target: str = "",
    element_source: str = "",
    element_target: str = "",
    element_source_offset: Any = None,
    element_target_offset: Any = None,
    source_x: Any = None,
    source_y: Any = None,
    target_x: Any = None,
    target_y: Any = None,
    coord_source_x: Any = None,
    coord_source_y: Any = None,
    coord_target_x: Any = None,
    coord_target_y: Any = None,
    steps: Any = None,
    delay_ms: Any = None,
) -> dict[str, Any]:
    source_selector = (element_source or "").strip()
    target_selector = (element_target or "").strip()
    if not source_selector:
        source_selector = (source or "").strip()
    if not target_selector:
        target_selector = (target or "").strip()

    sx = _to_int_or_none(coord_source_x)
    sy = _to_int_or_none(coord_source_y)
    tx = _to_int_or_none(coord_target_x)
    ty = _to_int_or_none(coord_target_y)
    if sx is None:
        sx = _to_int_or_none(source_x)
    if sy is None:
        sy = _to_int_or_none(source_y)
    if tx is None:
        tx = _to_int_or_none(target_x)
    if ty is None:
        ty = _to_int_or_none(target_y)

    return {
        "url": (url or "").strip(),
        "element_source": source_selector,
        "element_target": target_selector,
        "element_source_offset": _normalize_offset(element_source_offset),
        "element_target_offset": _normalize_offset(element_target_offset),
        "coord_source_x": sx,
        "coord_source_y": sy,
        "coord_target_x": tx,
        "coord_target_y": ty,
        "steps": _to_int_or_none(steps),
        "delay_ms": _to_int_or_none(delay_ms),
    }


def _has_selector_inputs(payload: dict[str, Any]) -> bool:
    return bool((payload.get("element_source") or "").strip()) and bool((payload.get("element_target") or "").strip())


def _has_source_selector(payload: dict[str, Any]) -> bool:
    """True if at least element_source is set (for get_element_coordinates, target is optional)."""
    return bool((payload.get("element_source") or "").strip())


def _has_coordinate_inputs(payload: dict[str, Any]) -> bool:
    return all(
        payload.get(k) is not None for k in ("coord_source_x", "coord_source_y", "coord_target_x", "coord_target_y")
    )


def _normalize_timeout_s(value: Any) -> int | None:
    parsed = _to_int_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _list_dir_files(root: Path) -> list[dict[str, Any]]:
    """Return a flat list of files under *root* with name, path, and size."""
    entries: list[dict[str, Any]] = []
    try:
        for item in sorted(root.iterdir()):
            if item.is_file():
                try:
                    size = item.stat().st_size
                except OSError:
                    size = -1
                entries.append({"name": item.name, "path": str(item), "size_bytes": size})
    except OSError:
        pass
    return entries


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


_DEFAULT_CONTROLLER = ActionController(
    actions=_ACTIONS,
    action_specs=_ACTION_SPECS,
    runtime_runner=_RUNTIME_RUNNER,
    lock=_LOCK,
)


def get_default_controller() -> ActionController:
    return _DEFAULT_CONTROLLER


def bind_runtime(runtime: Any) -> None:
    _DEFAULT_CONTROLLER.bind_runtime(runtime)
    global _RUNTIME_RUNNER
    _RUNTIME_RUNNER = _DEFAULT_CONTROLLER.runtime_runner


def bind_runtime_runner(runner: RuntimeRunner | None) -> None:
    _DEFAULT_CONTROLLER.bind_runtime_runner(runner)
    global _RUNTIME_RUNNER
    _RUNTIME_RUNNER = runner


def clear_runtime_runner() -> None:
    bind_runtime_runner(None)


def bind_code_executor(executor: CodeExecutor | None) -> None:
    _DEFAULT_CONTROLLER.bind_code_executor(executor)
    global _CODE_EXECUTOR
    _CODE_EXECUTOR = executor


def clear_code_executor() -> None:
    bind_code_executor(None)


def register_action(name: str, handler: ActionHandler, *, overwrite: bool = True) -> None:
    _DEFAULT_CONTROLLER.register_action(name=name, handler=handler, overwrite=overwrite)


def register_action_spec(
    name: str,
    *,
    summary: str = "",
    when_to_use: str = "",
    params: dict[str, str] | None = None,
) -> None:
    _DEFAULT_CONTROLLER.register_action_spec(
        name=name,
        summary=summary,
        when_to_use=when_to_use,
        params=params,
    )


def list_actions() -> list[str]:
    return _DEFAULT_CONTROLLER.list_actions()


def describe_actions() -> dict[str, dict[str, Any]]:
    return _DEFAULT_CONTROLLER.describe_actions()


async def run_action(
    action: str,
    session_id: str = "",
    request_id: str = "",
    **kwargs: Any,
) -> ActionResult:
    return await _DEFAULT_CONTROLLER.run_action(
        action=action,
        session_id=session_id,
        request_id=request_id,
        **kwargs,
    )


def _normalize_batch_interact_payload(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in kwargs.items() if value is not None}

    payload["session_id"] = str(payload.get("session_id") or "")
    payload["request_id"] = str(payload.get("request_id") or "")
    payload["continue_on_error"] = bool(payload.get("continue_on_error", False))
    return payload


def register_builtin_actions(controller: ActionController | None = None) -> None:
    ctl = controller or _DEFAULT_CONTROLLER

    async def ping(session_id: str = "", request_id: str = "", **kwargs: Any) -> ActionResult:
        return {
            "ok": True,
            "pong": True,
            "session_id": session_id,
            "request_id": request_id,
            "meta": kwargs,
        }

    async def echo(
        session_id: str = "",
        request_id: str = "",
        text: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        return {
            "ok": True,
            "text": text,
            "session_id": session_id,
            "request_id": request_id,
            "meta": kwargs,
        }

    async def browser_task(
        session_id: str = "",
        request_id: str = "",
        task: str = "",
        timeout_s: int | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        runner = ctl.runtime_runner
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_bound: call bind_runtime(...) before browser_task",
                "session_id": session_id,
                "request_id": request_id,
            }

        task_text = (task or "").strip()
        if not task_text:
            return {
                "ok": False,
                "error": "missing required parameter: task",
                "session_id": session_id,
                "request_id": request_id,
            }

        effective_timeout: int | None = None
        if timeout_s is not None:
            try:
                parsed = int(timeout_s)
                if parsed > 0:
                    effective_timeout = parsed
            except Exception:
                effective_timeout = None

        return await runner(
            task=task_text,
            session_id=session_id or None,
            request_id=request_id or None,
            timeout_s=effective_timeout,
        )

    async def browser_get_element_coordinates(
        session_id: str = "",
        request_id: str = "",
        url: str = "",
        source: str = "",
        target: str = "",
        *,
        element_source: str = "",
        element_target: str = "",
        element_source_offset: Any = None,
        element_target_offset: Any = None,
        source_x: Any = None,
        source_y: Any = None,
        target_x: Any = None,
        target_y: Any = None,
        coord_source_x: Any = None,
        coord_source_y: Any = None,
        coord_target_x: Any = None,
        coord_target_y: Any = None,
        timeout_s: int | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        del kwargs
        payload = _build_drag_payload(
            url=url,
            source=source,
            target=target,
            element_source=element_source,
            element_target=element_target,
            element_source_offset=element_source_offset,
            element_target_offset=element_target_offset,
            source_x=source_x,
            source_y=source_y,
            target_x=target_x,
            target_y=target_y,
            coord_source_x=coord_source_x,
            coord_source_y=coord_source_y,
            coord_target_x=coord_target_x,
            coord_target_y=coord_target_y,
        )
        if not (_has_source_selector(payload) or _has_coordinate_inputs(payload)):
            return {
                "ok": False,
                "error": (
                    "Missing location inputs. Provide at least element_source (element_target is optional), or "
                    "coord_source_x/coord_source_y/coord_target_x/coord_target_y. "
                    "Aliases source/target and source_x/source_y/target_x/target_y are also supported."
                ),
            }
        js_code = _build_coordinate_script(payload)
        code_executor = ctl.code_executor
        if code_executor is not None:
            try:
                raw = await code_executor(js_code)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"browser_run_code failed: {exc}",
                    "session_id": session_id,
                    "request_id": request_id,
                }
            parsed = extract_json_object(raw)
            if not parsed:
                return {
                    "ok": False,
                    "error": "Could not parse coordinate result JSON from browser_run_code output",
                    "raw_preview": str(raw)[:400],
                }
            return {
                "ok": bool(parsed.get("ok", False)),
                "source": parsed.get("source"),
                "target": parsed.get("target"),
                "error": parsed.get("error"),
            }
        # Fallback: route through LLM worker (requires runner to be bound)
        runner = ctl.runtime_runner
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_bound: call bind_runtime(...) before browser_get_element_coordinates",
                "session_id": session_id,
                "request_id": request_id,
            }
        task_prompt = _build_run_code_task(js_code, "resolve source/target coordinates")
        runtime_result = await runner(
            task=task_prompt,
            session_id=session_id or None,
            request_id=request_id or None,
            timeout_s=_normalize_timeout_s(timeout_s),
        )
        if not runtime_result.get("ok", False):
            return {
                "ok": False,
                "error": runtime_result.get("error") or "runtime error",
                "runtime": runtime_result,
            }
        parsed = extract_json_object(runtime_result.get("final"))
        if not parsed:
            final_preview = str(runtime_result.get("final", ""))[:400]
            return {
                "ok": False,
                "error": "Could not parse coordinate result JSON from runtime final output",
                "final_preview": final_preview,
                "runtime": runtime_result,
            }
        return {
            "ok": bool(parsed.get("ok", False)),
            "source": parsed.get("source"),
            "target": parsed.get("target"),
            "error": parsed.get("error"),
            "runtime": runtime_result,
        }

    async def list_upload_files(
        session_id: str = "",
        request_id: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        del kwargs
        upload_root = resolve_upload_root()
        if upload_root is None:
            return {
                "ok": False,
                "error": (
                    "BROWSER_UPLOAD_ROOT is not configured. "
                    "Set this env var to the directory where uploadable files are stored."
                ),
                "files": [],
                "session_id": session_id,
                "request_id": request_id,
            }
        if not upload_root.exists():
            return {
                "ok": False,
                "error": f"Upload root directory does not exist: {upload_root}",
                "files": [],
                "upload_root": str(upload_root),
                "session_id": session_id,
                "request_id": request_id,
            }
        files = _list_dir_files(upload_root)
        return {
            "ok": True,
            "upload_root": str(upload_root),
            "files": files,
            "session_id": session_id,
            "request_id": request_id,
        }

    async def browser_drag_and_drop(
        session_id: str = "",
        request_id: str = "",
        url: str = "",
        source: str = "",
        target: str = "",
        *,
        element_source: str = "",
        element_target: str = "",
        element_source_offset: Any = None,
        element_target_offset: Any = None,
        source_x: Any = None,
        source_y: Any = None,
        target_x: Any = None,
        target_y: Any = None,
        coord_source_x: Any = None,
        coord_source_y: Any = None,
        coord_target_x: Any = None,
        coord_target_y: Any = None,
        steps: Any = None,
        delay_ms: Any = None,
        timeout_s: int | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        del kwargs
        payload = _build_drag_payload(
            url=url,
            source=source,
            target=target,
            element_source=element_source,
            element_target=element_target,
            element_source_offset=element_source_offset,
            element_target_offset=element_target_offset,
            source_x=source_x,
            source_y=source_y,
            target_x=target_x,
            target_y=target_y,
            coord_source_x=coord_source_x,
            coord_source_y=coord_source_y,
            coord_target_x=coord_target_x,
            coord_target_y=coord_target_y,
            steps=steps,
            delay_ms=delay_ms,
        )
        if not (_has_selector_inputs(payload) or _has_coordinate_inputs(payload)):
            return {
                "ok": False,
                "error": (
                    "Missing drag inputs. Provide either "
                    "element_source + element_target, or "
                    "coord_source_x/coord_source_y/coord_target_x/coord_target_y. "
                    "Aliases source/target and source_x/source_y/target_x/target_y are also supported."
                ),
            }
        js_code = _build_drag_script(payload)
        code_executor = ctl.code_executor
        if code_executor is not None:
            try:
                raw = await code_executor(js_code)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"browser_run_code failed: {exc}",
                    "session_id": session_id,
                    "request_id": request_id,
                }
            parsed = extract_json_object(raw)
            if not parsed:
                return {
                    "ok": False,
                    "error": "Could not parse drag result JSON from browser_run_code output",
                    "raw_preview": str(raw)[:400],
                }
            return {
                "ok": bool(parsed.get("ok", False)),
                "message": parsed.get("message"),
                "source": parsed.get("source"),
                "target": parsed.get("target"),
                "steps": parsed.get("steps"),
                "delay_ms": parsed.get("delay_ms"),
                "error": parsed.get("error"),
            }
        # Fallback: route through LLM worker (requires runner to be bound)
        runner = ctl.runtime_runner
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_bound: call bind_runtime(...) before browser_drag_and_drop",
                "session_id": session_id,
                "request_id": request_id,
            }
        task_prompt = _build_run_code_task(js_code, "drag and drop")
        runtime_result = await runner(
            task=task_prompt,
            session_id=session_id or None,
            request_id=request_id or None,
            timeout_s=_normalize_timeout_s(timeout_s),
        )
        if not runtime_result.get("ok", False):
            return {
                "ok": False,
                "error": runtime_result.get("error") or "runtime error",
                "runtime": runtime_result,
            }
        parsed = extract_json_object(runtime_result.get("final"))
        if not parsed:
            final_preview = str(runtime_result.get("final", ""))[:400]
            return {
                "ok": False,
                "error": "Could not parse drag result JSON from runtime final output",
                "final_preview": final_preview,
                "runtime": runtime_result,
            }
        return {
            "ok": bool(parsed.get("ok", False)),
            "message": parsed.get("message"),
            "source": parsed.get("source"),
            "target": parsed.get("target"),
            "steps": parsed.get("steps"),
            "delay_ms": parsed.get("delay_ms"),
            "error": parsed.get("error"),
            "runtime": runtime_result,
        }

    async def browser_batch_interact(**kwargs: Any) -> ActionResult:
        """Execute multiple deterministic browser interactions with one LLM-visible tool call."""
        payload_args = _normalize_batch_interact_payload(kwargs)

        session_id = payload_args["session_id"]
        request_id = payload_args["request_id"]
        steps = payload_args.get("steps")
        timeout_ms = payload_args.get("timeout_ms")
        condition_timeout_ms = payload_args.get("condition_timeout_ms")
        wait_after_each_ms = payload_args.get("wait_after_each_ms")
        continue_on_error = bool(payload_args.get("continue_on_error", False))
        global_timeout_ms = payload_args.get("global_timeout_ms")
        generation_id = str(payload_args.get("generation_id") or "g0")

        validation_errors = validate_batch_steps(steps)
        if validation_errors:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_validation_failed: {validation_errors[0]}",
                "validation_errors": validation_errors,
                "session_id": session_id,
                "request_id": request_id,
            }

        original_step_count = len(steps)
        max_steps = 25
        safe_steps = steps
        try:
            per_step_timeout = int(timeout_ms or 2500)
        except (TypeError, ValueError):
            per_step_timeout = 2500
        per_step_timeout = max(250, min(30000, per_step_timeout))

        try:
            condition_timeout = int(condition_timeout_ms or 10000)
        except (TypeError, ValueError):
            condition_timeout = 10000
        condition_timeout = max(
            per_step_timeout,
            min(30000, condition_timeout),
        )

        try:
            after_each = int(wait_after_each_ms or 0)
        except (TypeError, ValueError):
            after_each = 0
        after_each = max(0, min(5000, after_each))

        if global_timeout_ms is None:
            condition_steps = 0
            for step in safe_steps:
                op = str(step.get("op") or "").strip().lower()
                if op in _BATCH_CONDITION_OPS:
                    condition_steps += 1
            action_steps = len(safe_steps) - condition_steps
            computed_timeout_ms = (
                action_steps * (per_step_timeout + after_each + 250)
                + condition_steps * (condition_timeout + after_each + 250)
                + 5000
            )
            effective_global_timeout_ms = min(
                120000,
                max(5000, computed_timeout_ms),
            )
        else:
            try:
                effective_global_timeout_ms = int(global_timeout_ms)
            except (TypeError, ValueError):
                effective_global_timeout_ms = 120000
            effective_global_timeout_ms = max(
                1000,
                min(180000, effective_global_timeout_ms),
            )

        payload = {
            "steps": safe_steps,
            "timeout_ms": per_step_timeout,
            "condition_timeout_ms": condition_timeout,
            "wait_after_each_ms": after_each,
            "continue_on_error": continue_on_error,
            "original_step_count": original_step_count,
            "executed_step_limit": max_steps,
            "generation_id": generation_id,
        }
        build_started_at = time.perf_counter()
        js_code = _build_batch_interact_script(payload)
        build_elapsed_ms = int(max(0.0, (time.perf_counter() - build_started_at) * 1000))
        script_size_bytes = len(js_code.encode("utf-8", "ignore"))
        code_executor = ctl.code_executor

        browser_agent_log_info(
            "[BROWSER_BATCH] start session_id=%s request_id=%s steps=%s "
            "timeout_ms=%s condition_timeout_ms=%s global_timeout_ms=%s "
            "script_size_bytes=%s",
            session_id or "-",
            request_id or "-",
            len(safe_steps),
            per_step_timeout,
            condition_timeout,
            effective_global_timeout_ms,
            script_size_bytes,
        )

        if code_executor is None:
            browser_agent_log_warning(
                "[BROWSER_BATCH] end ok=false session_id=%s request_id=%s error=%s",
                session_id or "-",
                request_id or "-",
                "browser_code_executor_not_ready",
            )
            return {
                "ok": False,
                "status": "failed",
                "error": "browser_code_executor_not_ready",
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
            }

        executor_started_at = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                code_executor(js_code),
                timeout=effective_global_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            error = f"browser_batch_interact timed out after {effective_global_timeout_ms}ms"
            browser_agent_log_warning(
                "[BROWSER_BATCH] end ok=false session_id=%s request_id=%s error=%s",
                session_id or "-",
                request_id or "-",
                error,
            )
            return {
                "ok": False,
                "status": "failed",
                "error": error,
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "metrics": {
                    "script_build_elapsed_ms": build_elapsed_ms,
                    "executor_elapsed_ms": int(
                        max(
                            0.0,
                            (time.perf_counter() - executor_started_at) * 1000,
                        )
                    ),
                    "script_size_bytes": script_size_bytes,
                },
            }
        except Exception as exc:
            error = f"browser_run_code failed: {exc}"
            browser_agent_log_warning(
                "[BROWSER_BATCH] end ok=false session_id=%s request_id=%s error=%s",
                session_id or "-",
                request_id or "-",
                error,
            )
            return {
                "ok": False,
                "status": "failed",
                "error": error,
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "metrics": {
                    "script_build_elapsed_ms": build_elapsed_ms,
                    "executor_elapsed_ms": int(
                        max(
                            0.0,
                            (time.perf_counter() - executor_started_at) * 1000,
                        )
                    ),
                    "script_size_bytes": script_size_bytes,
                },
            }

        executor_elapsed_ms = int(max(0.0, (time.perf_counter() - executor_started_at) * 1000))
        response_size_bytes = len(str(raw).encode("utf-8", "ignore"))
        rpc_metrics = _browser_rpc_metrics(raw)
        parse_started_at = time.perf_counter()
        unwrapped = _unwrap_browser_code_result(raw)
        parsed = extract_json_object(unwrapped)
        parse_elapsed_ms = int(max(0.0, (time.perf_counter() - parse_started_at) * 1000))
        if not parsed:
            error = "Could not parse browser_batch_interact result JSON from browser_run_code output"
            raw_text = str(unwrapped)
            browser_agent_log_warning(
                "[BROWSER_BATCH] end ok=false session_id=%s request_id=%s error=%s raw_preview=%r",
                session_id or "-",
                request_id or "-",
                error,
                raw_text[:200],
            )
            return {
                "ok": False,
                "status": "failed",
                "error": error,
                "raw_preview": raw_text[:400],
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "metrics": {
                    "script_build_elapsed_ms": build_elapsed_ms,
                    "executor_elapsed_ms": executor_elapsed_ms,
                    "result_parse_elapsed_ms": parse_elapsed_ms,
                    "script_size_bytes": script_size_bytes,
                    "response_size_bytes": response_size_bytes,
                    **rpc_metrics,
                },
            }

        parsed.setdefault("session_id", session_id)
        parsed.setdefault("request_id", request_id)
        parsed.setdefault("action", "browser_batch_interact")
        parsed.setdefault("original_step_count", original_step_count)
        parsed.setdefault("executed_step_limit", max_steps)
        parsed.setdefault("generation_id", generation_id)
        completed = parsed.get("steps") or parsed.get("completed_steps") or []
        completed_steps = completed if isinstance(completed, list) else []
        had_step_errors = any(isinstance(item, dict) and item.get("ok") is False for item in completed_steps)
        if had_step_errors:
            parsed["status"] = (
                "partial"
                if any(isinstance(item, dict) and item.get("ok") is True for item in completed_steps)
                else "failed"
            )
            parsed["ok"] = False
            if not parsed.get("error"):
                parsed["error"] = "one or more batch steps failed"
        elif parsed.get("ok") is False:
            parsed.setdefault("status", "failed")
        else:
            parsed["status"] = "completed"
            parsed["ok"] = True
        parsed["metrics"] = {
            "script_build_elapsed_ms": build_elapsed_ms,
            "executor_elapsed_ms": executor_elapsed_ms,
            "result_parse_elapsed_ms": parse_elapsed_ms,
            "script_size_bytes": script_size_bytes,
            "response_size_bytes": response_size_bytes,
            "internal_steps_elapsed_ms": int(parsed.get("internal_steps_elapsed_ms") or 0),
            "preflight_elapsed_ms": int(parsed.get("preflight_elapsed_ms") or 0),
            "preflight_target_count": int(parsed.get("preflight_target_count") or 0),
            **rpc_metrics,
        }
        browser_agent_log_info(
            "[BROWSER_BATCH] end ok=%s session_id=%s request_id=%s elapsed_ms=%s "
            "internal_steps_elapsed_ms=%s executor_elapsed_ms=%s "
            "script_size_bytes=%s response_size_bytes=%s "
            "transport_response_size_bytes=%s completed_steps=%s error=%s",
            bool(parsed.get("ok", False)),
            session_id or "-",
            request_id or "-",
            parsed.get("elapsed_ms"),
            parsed["metrics"]["internal_steps_elapsed_ms"],
            parsed["metrics"]["executor_elapsed_ms"],
            parsed["metrics"]["script_size_bytes"],
            parsed["metrics"]["response_size_bytes"],
            parsed["metrics"].get("transport_response_size_bytes", 0),
            len(completed) if isinstance(completed, list) else "?",
            parsed.get("error") or "-",
        )

        for item in completed_steps:
            if isinstance(item, dict):
                browser_agent_log_info(
                    "[BROWSER_BATCH] step index=%s op=%s ok=%s elapsed_ms=%s error=%s",
                    item.get("index"),
                    item.get("op"),
                    bool(item.get("ok", False)),
                    item.get("elapsed_ms"),
                    item.get("error") or "-",
                )

        compact_steps = []
        for item in completed_steps:
            compact = _compact_batch_step(item)
            if compact is not None:
                compact_steps.append(compact)
        extracted = parsed.get("extracted")
        field_provenance = _compact_extraction_provenance(
            completed_steps,
            generation_id,
        )
        compact_result = {
            "ok": bool(parsed.get("ok", False)),
            "status": str(parsed.get("status") or "failed"),
            "error": parsed.get("error"),
            "action": "browser_batch_interact",
            "execution_mode": "compact_rpc",
            "session_id": session_id,
            "request_id": request_id,
            "generation_id": generation_id,
            "steps": compact_steps,
            "extracted": dict(extracted) if isinstance(extracted, Mapping) else {},
            "field_provenance": field_provenance,
            "conditions": _compact_batch_conditions(parsed, completed_steps),
            "metrics": parsed["metrics"],
            "_runtime_page": {
                "url": str(parsed.get("url") or ""),
                "title": str(parsed.get("title") or ""),
            },
        }
        return compact_result

    async def browser_set_input_files(
        session_id: str = "",
        request_id: str = "",
        selector: str = "",
        paths: list | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        del kwargs
        effective_selector = (selector or "").strip() or 'input[type="file"]'
        effective_paths = [str(p) for p in (paths or []) if p]
        if not effective_paths:
            return {
                "ok": False,
                "error": "paths is required and must be non-empty",
                "session_id": session_id,
                "request_id": request_id,
            }
        js_code = _build_set_input_files_script(effective_selector, effective_paths)
        code_executor = ctl.code_executor
        if code_executor is not None:
            try:
                raw = await code_executor(js_code)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"browser_run_code failed: {exc}",
                    "session_id": session_id,
                    "request_id": request_id,
                }
            parsed = extract_json_object(raw)
            if not parsed:
                return {
                    "ok": False,
                    "error": "Could not parse set_input_files result JSON from browser_run_code output",
                    "raw_preview": str(raw)[:400],
                }
            return {
                "ok": bool(parsed.get("ok", False)),
                "selector": parsed.get("selector", effective_selector),
                "paths": parsed.get("paths", effective_paths),
                "error": parsed.get("error"),
            }
        # Fallback: route through LLM worker (requires runner to be bound)
        runner = ctl.runtime_runner
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_bound: call bind_runtime(...) before browser_set_input_files",
                "session_id": session_id,
                "request_id": request_id,
            }
        task_prompt = _build_run_code_task(js_code, f"set input files on {effective_selector}")
        runtime_result = await runner(
            task=task_prompt,
            session_id=session_id or None,
            request_id=request_id or None,
            timeout_s=None,
        )
        if not runtime_result.get("ok", False):
            return {
                "ok": False,
                "error": runtime_result.get("error") or "runtime error",
                "runtime": runtime_result,
            }
        parsed = extract_json_object(runtime_result.get("final"))
        if not parsed:
            return {
                "ok": False,
                "error": "Could not parse set_input_files result JSON from runtime final output",
                "raw_preview": str(runtime_result.get("final", ""))[:400],
                "runtime": runtime_result,
            }
        return {
            "ok": bool(parsed.get("ok", False)),
            "selector": parsed.get("selector", effective_selector),
            "paths": parsed.get("paths", effective_paths),
            "error": parsed.get("error"),
            "runtime": runtime_result,
        }

    ctl.register_action("ping", ping, overwrite=True)
    ctl.register_action_spec(
        "ping",
        summary="Health check action.",
        when_to_use="Use to verify controller dispatch and session/request threading.",
    )
    ctl.register_action("echo", echo, overwrite=True)
    ctl.register_action_spec(
        "echo",
        summary="Echoes provided text and metadata.",
        when_to_use="Use for debugging payload passthrough through browser_custom_action.",
        params={"text": "string: text to echo back"},
    )
    ctl.register_action("browser_task", browser_task, overwrite=True)
    ctl.register_action_spec(
        "browser_task",
        summary="Runs a free-form browser task through runtime.run_browser_task.",
        when_to_use="Use for generic website tasks when no specialized custom action applies.",
        params={
            "task": "string: required task prompt for the browser worker",
            "timeout_s": "int: optional positive timeout override",
        },
    )
    ctl.register_action("run_browser_task", browser_task, overwrite=True)
    ctl.register_action_spec(
        "run_browser_task",
        summary="Alias of browser_task.",
        when_to_use="Same behavior as browser_task.",
        params={
            "task": "string: required task prompt for the browser worker",
            "timeout_s": "int: optional positive timeout override",
        },
    )
    ctl.register_action(
        "browser_get_element_coordinates",
        browser_get_element_coordinates,
        overwrite=True,
    )
    ctl.register_action_spec(
        "browser_get_element_coordinates",
        summary="Resolves source/target screen coordinates by selectors or explicit coordinates.",
        when_to_use=(
            "Use when you need coordinates for one element (element_source only) or two (source + target). "
            "element_target is optional."
        ),
        params={
            "url": "string: optional URL to open before resolving coordinates",
            "element_source": "string: source selector/text alias (required for selector mode)",
            "element_target": "string: target selector/text alias (optional)",
            "coord_source_x": "int: source x coordinate",
            "coord_source_y": "int: source y coordinate",
            "coord_target_x": "int: target x coordinate",
            "coord_target_y": "int: target y coordinate",
            "source/target": "string aliases for element_source/element_target",
            "source_x/source_y/target_x/target_y": "int aliases for coord_* fields",
        },
    )
    ctl.register_action(
        "browser_drag_and_drop",
        browser_drag_and_drop,
        overwrite=True,
    )
    ctl.register_action_spec(
        "browser_drag_and_drop",
        summary="Performs drag-and-drop using selectors or explicit coordinates.",
        when_to_use=("Use for drag-and-drop tasks instead of generic browser_run_task text-only instructions."),
        params={
            "url": "string: optional URL to open before drag-and-drop",
            "element_source": "string: source selector/text alias",
            "element_target": "string: target selector/text alias",
            "coord_source_x": "int: source x coordinate",
            "coord_source_y": "int: source y coordinate",
            "coord_target_x": "int: target x coordinate",
            "coord_target_y": "int: target y coordinate",
            "steps": "int: optional drag interpolation steps",
            "delay_ms": "int: optional delay between drag steps",
            "source/target": "string aliases for element_source/element_target",
            "source_x/source_y/target_x/target_y": "int aliases for coord_* fields",
        },
    )

    ctl.register_action(
        "browser_batch_interact",
        browser_batch_interact,
        overwrite=True,
    )
    ctl.register_action_spec(
        "browser_batch_interact",
        summary=(
            "Executes multiple deterministic page interactions in one Playwright code call "
            "and returns compact per-step status."
        ),
        when_to_use=(
            "Use for multi-field forms with several known controls, search boxes with autocomplete, "
            "dropdown/date-picker flows, filter panels, and short click/type/wait/extract sequences. "
            "Prefer this over many separate browser_click, browser_type, condition-wait, and "
            "browser_evaluate turns when the next actions are already known. Do not use it for a single "
            "uncertain click or when the page state must be inspected first."
        ),
        params={
            "steps": (
                "list[object]: required, 1-25 items. Each step has op plus target fields. Supported op values: "
                "click, fill, type, autocomplete, select_visible_text, press, select_option, set_checked, "
                "wait_for_selector, wait_for_text, wait_for_load_state, wait_for_url, "
                "wait_for_first_card_title, wait_for_sort_state, wait_for_result_count, "
                "wait_for_dom_text_change, wait_for_stable, wait_for_tab, extract_text, extract_value, "
                "screenshot. Targets may use selector, role+name, label, placeholder, testid, or text. "
                "For fill/type/autocomplete use value. For autocomplete also use choose_text/option_text "
                "or option_selector/choose_selector. For native select_option, use value/option_value, "
                "option_text/option_label/label_value, index, or values for MCP-style multi/single selection."
            ),
            "timeout_ms": "int: default timeout per step, default 2500, max 30000",
            "condition_timeout_ms": ("int: default timeout for condition waits, default 10000, max 30000"),
            "wait_after_each_ms": ("int: optional pause after successful steps, max 5000; prefer condition wait ops"),
            "continue_on_error": "bool: when true, continue after failed optional/non-critical steps",
            "global_timeout_ms": (
                "int: hard ceiling for the full batch, default derived from step count "
                "and capped at 120000, explicit max 180000"
            ),
        },
    )
    ctl.register_action(
        "browser_set_input_files",
        browser_set_input_files,
        overwrite=True,
    )
    ctl.register_action_spec(
        "browser_set_input_files",
        summary=(
            "Sets files on an <input type='file'> element. Requires prior page inspection"
            " to select the correct input — do not call this without first reading the page snapshot."
        ),
        when_to_use=(
            "Use for all file upload tasks. Does NOT require a file chooser dialog — sets files directly "
            "on the DOM element. Call list_upload_files first to get absolute paths, then call this action. "
            "IMPORTANT: pages may have multiple file inputs (e.g. a visible input plus a hidden Dropzone input). "
            "Always prefer a specific selector such as '#file-upload' or 'input#id' over the generic "
            "'input[type=\"file\"]' to avoid strict mode violations. If you have not yet inspected the page, "
            "delegate this action to browser_run_task so the worker can read the page snapshot first."
        ),
        params={
            "selector": (
                "string: CSS/Playwright selector targeting exactly one file input "
                "(default: 'input[type=\"file\"]'). Use a specific ID selector like '#file-upload' "
                "when the page has more than one file input element."
            ),
            "paths": (
                "list[string]: absolute file paths to set on the input (required, non-empty). "
                "Parameter name is 'paths' — not 'files', not 'file_paths'."
            ),
        },
    )
    ctl.register_action(
        "list_upload_files",
        list_upload_files,
        overwrite=True,
    )
    ctl.register_action_spec(
        "list_upload_files",
        summary="Lists files available for upload from the configured BROWSER_UPLOAD_ROOT directory.",
        when_to_use=(
            "Call this to discover what files are available and get their exact absolute paths "
            "before calling browser_set_input_files to attach them to a file input. "
            "Returns a list of {name, path, size_bytes} entries."
        ),
    )


def register_example_actions(controller: ActionController | None = None) -> None:
    register_builtin_actions(controller=controller)


__all__ = [
    "BaseController",
    "ActionController",
    "get_default_controller",
    "bind_runtime",
    "bind_runtime_runner",
    "clear_runtime_runner",
    "bind_code_executor",
    "clear_code_executor",
    "register_action",
    "register_action_spec",
    "register_builtin_actions",
    "register_example_actions",
    "browser_worker_action_context",
    "list_actions",
    "describe_actions",
    "run_action",
]

sys.modules.setdefault("controllers.action", sys.modules[__name__])
