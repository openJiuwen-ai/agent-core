# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generic browser smoke evidence for locally delivered web artifacts."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

_MAX_CONTROLS = 12
_BROWSER_START_TIMEOUT_SECONDS = 10.0
_PAGE_SETTLE_SECONDS = 0.8


class WebArtifactAdapter:
    """Observe a web artifact through a real browser without domain assumptions."""

    name = "web"

    @staticmethod
    def supports(artifacts_dir: Path) -> bool:
        return (artifacts_dir / "index.html").is_file()

    def collect(
        self,
        artifacts_dir: Path,
        evidence_dir: Path,
        *,
        viewport_widths: list[int] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = artifacts_dir / "index.html"
        browser = _find_browser_executable()
        if browser is None:
            return _unavailable("browser_executable_not_found", entry)
        try:
            import websocket  # type: ignore[import-untyped]
        except ImportError:
            return _unavailable("websocket_client_not_installed", entry)

        evidence_dir.mkdir(parents=True, exist_ok=True)
        port = _reserve_local_port()
        profile = tempfile.mkdtemp(prefix="ach-web-evidence-")
        try:
            process = subprocess.Popen(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    "--remote-allow-origins=*",
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile}",
                    entry.resolve().as_uri(),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                page_ws = _wait_for_page_websocket(port)
                conn = websocket.create_connection(page_ws, timeout=2, origin="http://localhost")
                try:
                    client = _CdpClient(conn)
                    result = _collect_page_evidence(
                        client,
                        entry,
                        evidence_dir,
                        viewport_widths=viewport_widths or [],
                        verification=verification or {},
                    )
                finally:
                    conn.close()
            except Exception as exc:  # Browser failures become evidence, not judge crashes.
                return {
                    "adapter": self.name,
                    "status": "failed",
                    "entrypoint": "artifacts/index.html",
                    "observations": [
                        {
                            "type": "browser_execution",
                            "status": "failed",
                            "details": f"{type(exc).__name__}: {str(exc)[:300]}",
                        }
                    ],
                }
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            return result
        finally:
            shutil.rmtree(profile, ignore_errors=True)


class _CdpClient:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._next_id = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._conn.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            timed_out = False
            try:
                message = json.loads(self._conn.recv())
            except Exception as exc:
                if type(exc).__name__ in {"WebSocketTimeoutException", "TimeoutError"}:
                    timed_out = True
                else:
                    raise
            if timed_out:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result", {})
            self.events.append(message)
        raise TimeoutError(f"CDP call timed out: {method}")

    def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        self._conn.settimeout(0.1)
        while time.monotonic() < deadline:
            try:
                self.events.append(json.loads(self._conn.recv()))
            except Exception as exc:
                if type(exc).__name__ not in {"WebSocketTimeoutException", "TimeoutError"}:
                    raise

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return result.get("result", {}).get("value")


def _collect_page_evidence(
    client: _CdpClient,
    entry: Path,
    evidence_dir: Path,
    *,
    viewport_widths: list[int],
    verification: dict[str, Any],
) -> dict[str, Any]:
    for domain in ("Page.enable", "Runtime.enable", "Log.enable"):
        client.call(domain)
    client.call(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": _INTERACTION_LISTENER_INSTRUMENTATION_SCRIPT},
    )
    client.call("Page.reload", {"ignoreCache": True})
    client.drain(_PAGE_SETTLE_SECONDS)

    snapshot = client.evaluate(_PAGE_SNAPSHOT_SCRIPT) or {}
    screenshot = client.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    screenshot_path = evidence_dir / "web_initial.png"
    screenshot_path.write_bytes(base64.b64decode(screenshot.get("data", "")))

    probes: list[dict[str, Any]] = []
    control_count = min(int(snapshot.get("actionable_control_count", 0)), _MAX_CONTROLS)
    for index in range(control_count):
        client.call("Page.reload", {"ignoreCache": True})
        client.drain(0.35)
        probe = client.evaluate(_interaction_probe_script(index))
        if isinstance(probe, dict):
            probes.append(probe)
        client.drain(0.15)

    errors = _extract_runtime_errors(client.events)
    viewport_measurements = _collect_viewport_measurements(
        client,
        viewport_widths,
    )
    if verification.get("steps"):
        # Smoke probes and viewport measurements may mutate page state.  A case
        # contract must always start from the artifact's clean initial state.
        client.call("Page.reload", {"ignoreCache": True})
        client.drain(_PAGE_SETTLE_SECONDS)
    verification_result = _run_verification_contract(client, verification)
    observations = [
        {
            "type": "browser_execution",
            "status": "passed" if snapshot.get("ready_state") == "complete" else "failed",
            "details": {
                "ready_state": snapshot.get("ready_state"),
                "title": snapshot.get("title", ""),
                "visible_text_chars": snapshot.get("visible_text_chars", 0),
                "body_size": snapshot.get("body_size", {}),
            },
        },
        {
            "type": "runtime_errors",
            "status": "passed" if not errors else "failed",
            "details": errors[:20],
        },
        {
            "type": "interaction_smoke",
            "status": "observed" if probes else "not_applicable",
            "details": {
                "scope": "initially visible enabled buttons only; not full task validation",
                "probes": probes,
            },
        },
    ]
    if viewport_measurements:
        observations.append(
            {
                "type": "responsive_touch_targets",
                "status": "observed",
                "details": {
                    "element_scope": (
                        "native controls, explicit interaction roles, links, and "
                        "elements that registered click/touch/pointer listeners"
                    ),
                    "viewports": viewport_measurements,
                },
            }
        )
    if verification_result:
        observations.append(
            {
                "type": "case_web_verification",
                "status": "passed" if verification_result.get("passed") else "failed",
                "details": verification_result,
            }
        )
    return {
        "adapter": "web",
        "status": "collected",
        "entrypoint": "artifacts/index.html",
        "browser_engine": "chromium_cdp",
        "screenshot": f"judge/evidence/{screenshot_path.name}",
        "observations": observations,
        "evidence_limitations": [
            "Smoke evidence does not prove domain semantics or complete user workflows.",
            "Only controls visible on initial load are probed independently.",
        ],
    }


def _run_verification_contract(
    client: _CdpClient,
    verification: dict[str, Any],
) -> dict[str, Any] | None:
    """Execute a bounded declarative case contract without evaluating case code."""
    steps = verification.get("steps") if isinstance(verification, dict) else None
    if (
        not isinstance(steps, list)
        or not steps
        or not any(isinstance(step, dict) and step.get("assert") for step in steps)
    ):
        return None
    results: list[dict[str, Any]] = []
    passed = True
    for index, step in enumerate(steps[:20]):
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "") or "")
        assertion = str(step.get("assert", "") or "")
        selector = str(step.get("selector", "") or "")[:300]
        if action == "wait":
            milliseconds = max(0, min(int(step.get("milliseconds", 0) or 0), 3000))
            client.drain(milliseconds / 1000)
            results.append({"index": index, "action": action, "passed": True})
            continue
        if action == "click":
            outcome = client.evaluate(_verification_click_script(selector)) or {}
        elif assertion:
            contract_error = _verification_assertion_contract_error(
                assertion,
                step.get("value"),
            )
            if contract_error:
                outcome = {"passed": False, "reason": contract_error}
            else:
                outcome = client.evaluate(_verification_assertion_script(assertion, selector, step.get("value"))) or {}
        else:
            continue
        item = {"index": index, **step, **(outcome if isinstance(outcome, dict) else {})}
        item_passed = bool(item.get("passed"))
        passed = passed and item_passed
        results.append(item)
        if not item_passed:
            break
    return {"passed": passed, "steps": results}


def _verification_assertion_contract_error(assertion: str, value: Any) -> str:
    if assertion in {
        "has_class",
        "not_has_class",
        "text_contains",
        "computed_style_not_default",
    }:
        if not str(value or "").strip():
            return "missing_expected_value"
    if assertion in {"count_equals", "count_at_least", "count_at_most"}:
        if isinstance(value, bool):
            return "invalid_count_value"
        if isinstance(value, int):
            numeric_value = value
        elif isinstance(value, float) and value.is_integer():
            numeric_value = int(value)
        elif isinstance(value, str) and value.strip().isdigit():
            numeric_value = int(value.strip())
        else:
            return "invalid_count_value"
        if numeric_value < 0:
            return "invalid_count_value"
        if assertion == "count_at_least" and numeric_value == 0:
            return "vacuous_count_value"
    return ""


def _verification_click_script(selector: str) -> str:
    encoded = json.dumps(selector)
    return f"""
(() => {{
  const selector = {encoded};
  let element;
  try {{ element = document.querySelector(selector); }}
  catch (error) {{ return {{passed: false, reason: 'invalid_selector'}}; }}
  if (!element) return {{passed: false, reason: 'selector_not_found'}};
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none')
    return {{passed: false, reason: 'element_not_visible'}};
  element.click();
  return {{passed: true}};
}})()
"""


def _verification_assertion_script(assertion: str, selector: str, value: Any) -> str:
    encoded_assertion = json.dumps(assertion)
    encoded_selector = json.dumps(selector)
    encoded_value = json.dumps(value)
    return f"""
(() => {{
  const kind = {encoded_assertion};
  const selector = {encoded_selector};
  const expected = {encoded_value};
  let elements;
  try {{ elements = Array.from(document.querySelectorAll(selector)); }}
  catch (error) {{ return {{passed: false, reason: 'invalid_selector'}}; }}
  const element = elements[0];
  const visible = element && (() => {{
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }})();
  let actual = null;
  let passed = false;
  if (kind === 'exists') {{ actual = elements.length; passed = elements.length > 0; }}
  else if (kind === 'visible') {{ actual = Boolean(visible); passed = Boolean(visible); }}
  else if (kind === 'hidden') {{ actual = Boolean(visible); passed = !visible; }}
  else if (kind === 'has_class') {{ actual = Boolean(element && element.classList.contains(String(expected))); passed = actual; }}
  else if (kind === 'not_has_class') {{ actual = Boolean(element && element.classList.contains(String(expected))); passed = Boolean(element) && !actual; }}
  else if (kind === 'text_contains') {{ actual = element ? (element.textContent || '') : ''; passed = actual.includes(String(expected)); }}
  else if (kind === 'enabled') {{ actual = Boolean(element && !element.disabled); passed = actual; }}
  else if (kind === 'disabled') {{ actual = Boolean(element && element.disabled); passed = actual; }}
  else if (kind === 'count_equals') {{ actual = elements.length; passed = actual === Number(expected); }}
  else if (kind === 'count_at_least') {{ actual = elements.length; passed = actual >= Number(expected); }}
  else if (kind === 'count_at_most') {{ actual = elements.length; passed = actual <= Number(expected); }}
  else if (kind === 'computed_style_not_default') {{
    const property = String(expected || '').trim().slice(0, 100);
    if (!element) return {{passed: false, reason: 'selector_not_found'}};
    if (!property) return {{passed: false, reason: 'missing_css_property'}};
    const value = getComputedStyle(element).getPropertyValue(property).trim();
    const baseline = document.createElement(element.tagName);
    baseline.style.all = 'initial';
    baseline.style.position = 'absolute';
    baseline.style.visibility = 'hidden';
    document.body.appendChild(baseline);
    const defaultValue = getComputedStyle(baseline).getPropertyValue(property).trim();
    baseline.remove();
    actual = {{property, value, default_value: defaultValue}};
    passed = Boolean(value) && value !== defaultValue;
  }}
  else return {{passed: false, reason: 'unsupported_assertion'}};
  const class_names = element ? Array.from(element.classList) : [];
  return {{passed, actual, class_names}};
}})()
"""


def _collect_viewport_measurements(
    client: _CdpClient,
    viewport_widths: list[int],
) -> list[dict[str, Any]]:
    measurements: list[dict[str, Any]] = []
    for width in sorted({value for value in viewport_widths if 240 <= value <= 4096}):
        client.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": 900,
                "deviceScaleFactor": 1,
                "mobile": width <= 767,
            },
        )
        client.call("Page.reload", {"ignoreCache": True})
        client.drain(_PAGE_SETTLE_SECONDS)
        value = client.evaluate(_TOUCH_TARGET_MEASUREMENT_SCRIPT)
        if isinstance(value, dict):
            measurements.append({"width": width, **value})
    return measurements


def _extract_runtime_errors(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for event in events:
        method = event.get("method")
        params = event.get("params", {})
        if method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails", {})
            exception = detail.get("exception", {})
            text = exception.get("description") if isinstance(exception, dict) else ""
            errors.append(
                {
                    "source": "page_exception",
                    "text": str(text or detail.get("text", ""))[:500],
                }
            )
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            if entry.get("level") in {"error", "warning"}:
                errors.append({"source": f"console_{entry.get('level')}", "text": str(entry.get("text", ""))[:500]})
    return errors


def _wait_for_page_websocket(port: int) -> str:
    deadline = time.monotonic() + _BROWSER_START_TIMEOUT_SECONDS
    url = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310 - localhost CDP only
                targets = json.loads(response.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
        except (OSError, ValueError):
            time.sleep(0.1)
    raise TimeoutError("browser CDP endpoint did not become ready")


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_browser_executable() -> str | None:
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _unavailable(reason: str, entry: Path) -> dict[str, Any]:
    return {
        "adapter": "web",
        "status": "unavailable",
        "entrypoint": entry.name,
        "observations": [
            {
                "type": "browser_execution",
                "status": "not_collected",
                "details": reason,
            }
        ],
    }


_PAGE_SNAPSHOT_SCRIPT = r"""
(() => {
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const controls = [...document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]')]
    .filter(el => visible(el) && !el.disabled);
  const body = document.body?.getBoundingClientRect();
  return {
    ready_state: document.readyState,
    title: document.title,
    visible_text_chars: (document.body?.innerText || '').trim().length,
    body_size: {width: Math.round(body?.width || 0), height: Math.round(body?.height || 0)},
    actionable_control_count: controls.length,
    controls: controls.slice(0, 20).map((el, index) => ({
      index, tag: el.tagName.toLowerCase(), id: el.id || '',
      label: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120)
    }))
  };
})()
"""


_INTERACTION_LISTENER_INSTRUMENTATION_SCRIPT = r"""
(() => {
  const original = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(type, listener, options) {
    const normalized = String(type || '').toLowerCase();
    if (['click', 'touchstart', 'touchend', 'pointerdown', 'pointerup'].includes(normalized)) {
      const current = Array.isArray(this.__achInteractionTypes) ? this.__achInteractionTypes : [];
      if (!current.includes(normalized)) {
        Object.defineProperty(this, '__achInteractionTypes', {
          value: [...current, normalized], configurable: true, writable: true
        });
      }
    }
    return original.call(this, type, listener, options);
  };
})()
"""


_TOUCH_TARGET_MEASUREMENT_SCRIPT = r"""
(() => {
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const nativeSelector = 'button, input, select, textarea, a[href], [role=button], [tabindex]';
  const elements = [...document.querySelectorAll('*')].filter(el => {
    if (!visible(el) || el.disabled) return false;
    return el.matches(nativeSelector) || (Array.isArray(el.__achInteractionTypes) && el.__achInteractionTypes.length > 0);
  });
  const controls = elements.slice(0, 100).map((el, index) => {
    const r = el.getBoundingClientRect();
    return {
      index,
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: typeof el.className === 'string' ? el.className.slice(0, 160) : '',
      label: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120),
      interaction_types: Array.isArray(el.__achInteractionTypes) ? el.__achInteractionTypes : [],
      width: Math.round(r.width * 10) / 10,
      height: Math.round(r.height * 10) / 10
    };
  });
  return {
    interactive_count: elements.length,
    controls
  };
})()
"""


def _interaction_probe_script(index: int) -> str:
    return rf"""
(async () => {{
  const visible = el => {{
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  }};
  const controls = [...document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]')]
    .filter(el => visible(el) && !el.disabled);
  const el = controls[{index}];
  if (!el) return {{index: {index}, status: 'not_found'}};
  const before = (document.body?.innerText || '').slice(0, 20000);
  const beforeDom = (document.body?.outerHTML || '').slice(0, 100000);
  const beforeUrl = location.href;
  const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120);
  el.click();
  await new Promise(resolve => setTimeout(resolve, 350));
  const after = (document.body?.innerText || '').slice(0, 20000);
  const afterDom = (document.body?.outerHTML || '').slice(0, 100000);
  return {{
    index: {index}, label, status: 'clicked',
    visible_text_changed: before !== after,
    dom_changed: beforeDom !== afterDom,
    url_changed: beforeUrl !== location.href
  }};
}})()
"""


__all__ = ["WebArtifactAdapter"]
