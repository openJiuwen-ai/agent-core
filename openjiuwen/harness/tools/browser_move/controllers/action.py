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


def _unwrap_browser_code_result(raw: Any) -> Any:
    """Extract text/data payloads from common browser_run_code/MCP result shapes."""
    if isinstance(raw, dict):
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
                f"request_id={rid or '-'} ok={_ok}"
                + (f" error={_err!r}" if _err else "")
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
        " Use the exact visible text (e.g. \"Learn more\" not \"More information\")"
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
        "  const defaultTimeout = Math.max(250, Number(payload.timeout_ms || 5000));\n"
        "  const defaultAfter = Math.max(0, Number(payload.wait_after_each_ms || 0));\n"
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
        "  const pressSelectAll = async () => {\n"
        "    try { await page.keyboard.press('Control+A'); }\n"
        "    catch (_err) { await page.keyboard.press('Meta+A').catch(() => {}); }\n"
        "  };\n"
        "  const locatorFromStep = (step) => {\n"
        "    if (step.selector) return page.locator(String(step.selector)).first();\n"
        "    if (step.role) {\n"
        "      if (step.name !== undefined && step.name !== null && String(step.name).length > 0) {\n"
        "        return page.getByRole(\n"
        "          String(step.role),\n"
        "          { name: String(step.name), exact: !!step.exact }\n"
        "        ).first();\n"
        "      }\n"
        "      return page.getByRole(String(step.role)).first();\n"
        "    }\n"
        "    if (step.label) {\n"
        "      return page.getByLabel(\n"
        "        String(step.label),\n"
        "        { exact: !!step.exact }\n"
        "      ).first();\n"
        "    }\n"
        "    if (step.placeholder) {\n"
        "      return page.getByPlaceholder(\n"
        "        String(step.placeholder),\n"
        "        { exact: !!step.exact }\n"
        "      ).first();\n"
        "    }\n"
        "    if (step.testid) return page.getByTestId(String(step.testid)).first();\n"
        "    if (step.text) {\n"
        "      return page.getByText(\n"
        "        String(step.text),\n"
        "        { exact: !!step.exact }\n"
        "      ).first();\n"
        "    }\n"
        "    throw new Error('step needs selector, role, label, placeholder, testid, or text');\n"
        "  };\n"
        "  const optionLocatorFromStep = (step) => {\n"
        "    if (step.option_selector || step.choose_selector) {\n"
        "      return page.locator(\n"
        "        String(step.option_selector || step.choose_selector)\n"
        "      ).first();\n"
        "    }\n"
        "    if (step.option_role || step.choose_role) {\n"
        "      const role = String(step.option_role || step.choose_role);\n"
        "      const name = step.option_name ?? step.choose_name ?? step.choose_text ?? step.option_text;\n"
        "      if (name !== undefined && name !== null && String(name).length > 0) {\n"
        "        return page.getByRole(\n"
        "          role,\n"
        "          { name: String(name), exact: !!step.exact }\n"
        "        ).first();\n"
        "      }\n"
        "      return page.getByRole(role).first();\n"
        "    }\n"
        "    const chosenText = step.choose_text ?? step.option_text ?? step.text_to_choose ?? step.value;\n"
        "    if (chosenText !== undefined && chosenText !== null && String(chosenText).length > 0) {\n"
        "      return page.getByText(\n"
        "        String(chosenText),\n"
        "        { exact: !!step.exact }\n"
        "      ).first();\n"
        "    }\n"
        "    throw new Error(\n"
        "      'autocomplete/select_visible_text needs choose_text/option_text or option selector'\n"
        "    );\n"
        "  };\n"
        "  const summarizeVisible = async () => {\n"
        "    return await page.evaluate(() => {\n"
        "      const text = (document.body && document.body.innerText) ? document.body.innerText : '';\n"
        "      return text.replace(/\\s+/g, ' ').trim().slice(0, 1200);\n"
        "    });\n"
        "  };\n"
        "  for (let i = 0; i < steps.length; i += 1) {\n"
        "    const step = steps[i] || {};\n"
        "    const op = String(step.op || '').trim().toLowerCase();\n"
        "    const timeout = Math.max(250, Number(step.timeout_ms || defaultTimeout));\n"
        "    const stepStartedAt = Date.now();\n"
        "    const item = { index: i, op, ok: false, elapsed_ms: 0 };\n"
        "    try {\n"
        "      if (!op) throw new Error('missing op');\n"
        "      if (op === 'click') {\n"
        "        await locatorFromStep(step).click({ timeout });\n"
        "      } else if (op === 'fill' || op === 'type') {\n"
        "        const target = locatorFromStep(step);\n"
        "        const value = String(step.value ?? step.text_value ?? '');\n"
        "        if (op === 'type' || step.mode === 'type') {\n"
        "          await target.click({ timeout });\n"
        "          try {\n"
        "            await pressSelectAll();\n"
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
        "        const target = locatorFromStep(step);\n"
        "        const value = String(step.value ?? step.query ?? step.text_value ?? '');\n"
        "        await target.click({ timeout });\n"
        "        try {\n"
        "          await pressSelectAll();\n"
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
        "        const option = optionLocatorFromStep(step);\n"
        "        await option.waitFor({ state: 'visible', timeout });\n"
        "        await option.click({ timeout });\n"
        "      } else if (op === 'select_visible_text') {\n"
        "        const option = optionLocatorFromStep(step);\n"
        "        await option.waitFor({ state: 'visible', timeout });\n"
        "        await option.click({ timeout });\n"
        "      } else if (op === 'press') {\n"
        "        const key = String(step.key || 'Enter');\n"
        "        if (hasTarget(step)) {\n"
        "          await locatorFromStep(step).press(key, { timeout });\n"
        "        } else {\n"
        "          await page.keyboard.press(key);\n"
        "        }\n"
        "      } else if (op === 'select_option') {\n"
        "        const target = locatorFromStep(step);\n"
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
        "        await locatorFromStep(step).setChecked(checked, { timeout });\n"
        "      } else if (op === 'wait_for_selector') {\n"
        "        if (!step.selector) throw new Error('wait_for_selector requires selector');\n"
        "        await page.locator(String(step.selector)).first().waitFor({\n"
        "          state: String(step.state || 'visible'),\n"
        "          timeout,\n"
        "        });\n"
        "      } else if (op === 'wait_for_text') {\n"
        "        if (!step.text) throw new Error('wait_for_text requires text');\n"
        "        await page.getByText(String(step.text), { exact: !!step.exact })\n"
        "          .first()\n"
        "          .waitFor({ timeout });\n"
        "      } else if (op === 'wait_for_load_state') {\n"
        "        await page.waitForLoadState(\n"
        "          String(step.state || 'domcontentloaded'),\n"
        "          { timeout }\n"
        "        );\n"
        "      } else if (op === 'sleep') {\n"
        "        await sleep(Math.max(0, Number(step.ms || step.time_ms || 0)));\n"
        "      } else if (op === 'extract_text') {\n"
        "        item.text = compactText(\n"
        "          await locatorFromStep(step).innerText({ timeout }),\n"
        "          Number(step.max_chars || 500)\n"
        "        );\n"
        "      } else if (op === 'extract_value') {\n"
        "        item.value = await locatorFromStep(step).inputValue({ timeout });\n"
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
        "        error: item.error,\n"
        "        failed_step: item,\n"
        "        completed_steps: results,\n"
        "        elapsed_ms: Date.now() - startedAt,\n"
        "        url: page.url(),\n"
        "        title: await page.title().catch(() => ''),\n"
        "        visible_text_preview: await summarizeVisible().catch(() => ''),\n"
        "      };\n"
        "    }\n"
        "    item.elapsed_ms = Date.now() - stepStartedAt;\n"
        "    results.push(item);\n"
        "  }\n"
        "  const failedSteps = results.filter((item) => !item.ok);\n"
        "  const successfulSteps = results.filter((item) => item.ok);\n"
        "  const allStepsOk = failedSteps.length === 0;\n"
        "  return {\n"
        "    ok: allStepsOk,\n"
        "    error: allStepsOk ? null : 'one_or_more_steps_failed',\n"
        "    partial: failedSteps.length > 0 && successfulSteps.length > 0,\n"
        "    all_steps_ok: allStepsOk,\n"
        "    had_step_errors: failedSteps.length > 0,\n"
        "    steps_ok: successfulSteps.length,\n"
        "    steps_failed: failedSteps.length,\n"
        "    steps: results,\n"
        "    elapsed_ms: Date.now() - startedAt,\n"
        "    url: page.url(),\n"
        "    title: await page.title().catch(() => ''),\n"
        "    visible_text_preview: await summarizeVisible().catch(() => ''),\n"
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
        payload.get(k) is not None
        for k in ("coord_source_x", "coord_source_y", "coord_target_x", "coord_target_y")
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
    payload = {
        key: value
        for key, value in kwargs.items()
        if value is not None
    }

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
        wait_after_each_ms = payload_args.get("wait_after_each_ms")
        continue_on_error = bool(payload_args.get("continue_on_error", False))
        global_timeout_ms = payload_args.get("global_timeout_ms")

        if not isinstance(steps, list) or not steps:
            return {
                "ok": False,
                "error": "steps is required and must be a non-empty list",
                "session_id": session_id,
                "request_id": request_id,
            }

        original_step_count = len(steps)
        max_steps = 25
        safe_steps = steps[:max_steps]
        truncated = original_step_count > len(safe_steps)
        dropped_step_count = original_step_count - len(safe_steps)
        if truncated:
            browser_agent_log_warning(
                "[BROWSER_BATCH] truncated session_id=%s request_id=%s "
                "original_steps=%s executed_steps=%s dropped_steps=%s",
                session_id or "-",
                request_id or "-",
                original_step_count,
                len(safe_steps),
                dropped_step_count,
            )
        try:
            per_step_timeout = int(timeout_ms or 5000)
        except (TypeError, ValueError):
            per_step_timeout = 5000
        per_step_timeout = max(250, min(30000, per_step_timeout))

        try:
            after_each = int(wait_after_each_ms or 0)
        except (TypeError, ValueError):
            after_each = 0
        after_each = max(0, min(5000, after_each))

        if global_timeout_ms is None:
            step_budget_ms = per_step_timeout + after_each + 250
            computed_timeout_ms = step_budget_ms * len(safe_steps) + 5000
            effective_global_timeout_ms = min(
                90000,
                max(5000, computed_timeout_ms),
            )
        else:
            try:
                effective_global_timeout_ms = int(global_timeout_ms)
            except (TypeError, ValueError):
                effective_global_timeout_ms = 60000
            effective_global_timeout_ms = max(
                1000,
                min(120000, effective_global_timeout_ms),
            )

        payload = {
            "steps": safe_steps,
            "timeout_ms": per_step_timeout,
            "wait_after_each_ms": after_each,
            "continue_on_error": continue_on_error,
            "original_step_count": original_step_count,
            "executed_step_limit": max_steps,
            "truncated": truncated,
            "dropped_step_count": dropped_step_count,
        }
        js_code = _build_batch_interact_script(payload)
        code_executor = ctl.code_executor

        browser_agent_log_info(
            "[BROWSER_BATCH] start session_id=%s request_id=%s steps=%s "
            "timeout_ms=%s global_timeout_ms=%s",
            session_id or "-",
            request_id or "-",
            len(safe_steps),
            per_step_timeout,
            effective_global_timeout_ms,
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
                "error": "browser_code_executor_not_ready",
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "truncated": truncated,
                "dropped_step_count": dropped_step_count,
            }

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
                "error": error,
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "truncated": truncated,
                "dropped_step_count": dropped_step_count,
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
                "error": error,
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "truncated": truncated,
                "dropped_step_count": dropped_step_count,
            }

        unwrapped = _unwrap_browser_code_result(raw)
        parsed = extract_json_object(unwrapped)
        if not parsed:
            error = "Could not parse browser_batch_interact result JSON from browser_run_code output"
            raw_text = str(unwrapped)
            browser_agent_log_warning(
                "[BROWSER_BATCH] end ok=false session_id=%s request_id=%s "
                "error=%s raw_preview=%r",
                session_id or "-",
                request_id or "-",
                error,
                raw_text[:200],
            )
            return {
                "ok": False,
                "error": error,
                "raw_preview": raw_text[:400],
                "session_id": session_id,
                "request_id": request_id,
                "steps_requested": len(safe_steps),
                "original_step_count": original_step_count,
                "executed_step_limit": max_steps,
                "truncated": truncated,
                "dropped_step_count": dropped_step_count,
            }

        parsed.setdefault("session_id", session_id)
        parsed.setdefault("request_id", request_id)
        parsed.setdefault("action", "browser_batch_interact")
        parsed.setdefault("original_step_count", original_step_count)
        parsed.setdefault("executed_step_limit", max_steps)
        parsed.setdefault("truncated", truncated)
        parsed.setdefault("dropped_step_count", dropped_step_count)
        completed = parsed.get("steps") or parsed.get("completed_steps") or []
        completed_steps = completed if isinstance(completed, list) else []
        failed_steps = [
            item for item in completed_steps
            if isinstance(item, dict) and not bool(item.get("ok", False))
        ]
        successful_steps = [
            item for item in completed_steps
            if isinstance(item, dict) and bool(item.get("ok", False))
        ]
        if failed_steps:
            parsed["ok"] = False
            parsed["error"] = parsed.get("error") or "one_or_more_steps_failed"
        parsed["all_steps_ok"] = bool(parsed.get("ok", False)) and not failed_steps
        parsed["had_step_errors"] = bool(failed_steps) or not bool(parsed.get("ok", False))
        parsed["partial"] = bool(failed_steps and successful_steps)
        parsed["steps_ok"] = len(successful_steps)
        parsed["steps_failed"] = len(failed_steps)

        browser_agent_log_info(
            "[BROWSER_BATCH] end ok=%s session_id=%s request_id=%s elapsed_ms=%s "
            "completed_steps=%s error=%s",
            bool(parsed.get("ok", False)),
            session_id or "-",
            request_id or "-",
            parsed.get("elapsed_ms"),
            len(completed) if isinstance(completed, list) else "?",
            parsed.get("error") or "-",
        )

        for item in completed_steps:
            if isinstance(item, dict):
                browser_agent_log_info(
                    "[BROWSER_BATCH] step index=%s op=%s ok=%s elapsed_ms=%s "
                    "error=%s",
                    item.get("index"),
                    item.get("op"),
                    bool(item.get("ok", False)),
                    item.get("elapsed_ms"),
                    item.get("error") or "-",
                )

        return parsed

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
        when_to_use=(
            "Use for drag-and-drop tasks instead of generic browser_run_task text-only instructions."
        ),
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
            "Prefer this over many separate browser_click, browser_type, browser_wait_for, and "
            "browser_evaluate turns when the next actions are already known. Do not use it for a single "
            "uncertain click or when the page state must be inspected first."
        ),
        params={
            "steps": (
                "list[object]: required, max 25. Each step has op plus target fields. Supported op values: "
                "click, fill, type, autocomplete, select_visible_text, press, select_option, set_checked, "
                "wait_for_selector, wait_for_text, wait_for_load_state, sleep, extract_text, extract_value, "
                "screenshot. Targets may use selector, role+name, label, placeholder, testid, or text. "
                "For fill/type/autocomplete use value. For autocomplete also use choose_text/option_text "
                "or option_selector/choose_selector. For native select_option, use value/option_value, "
                "option_text/option_label/label_value, index, or values for MCP-style multi/single selection."
            ),
            "timeout_ms": "int: default timeout per step, default 5000, max 30000",
            "wait_after_each_ms": "int: optional small pause after successful steps, max 5000",
            "continue_on_error": "bool: when true, continue after failed optional/non-critical steps",
            "global_timeout_ms": "int: hard ceiling for the full batch, default derived from step count, max 120000",
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
