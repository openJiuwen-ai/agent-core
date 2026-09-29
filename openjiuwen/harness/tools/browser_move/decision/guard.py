# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Executor-owned checks for a single compiled policy decision."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# This state stays local and is checked again after permission hooks.
PAGE_STATE_JS = """() => {
  const registry = window.__openjiuwenDecisionNodes || (window.__openjiuwenDecisionNodes = {
    document: String(Date.now()) + ':' + String(Math.random()), nodes: new WeakMap(), next: 0
  });
  return {document: registry.document, history_length: history.length,
    can_go_back: typeof window.navigation?.canGoBack === 'boolean' ? window.navigation.canGoBack : null,
    scroll_x: window.scrollX, scroll_y: window.scrollY,
    width: window.innerWidth, height: window.innerHeight,
    page_height: document.documentElement.scrollHeight};
}"""

# Shared by the observation probe and execution gate. These fields stay local.
NODE_STATE_JS = """(el) => {
  const registry = window.__openjiuwenDecisionNodes;
  if (!registry || !registry.nodes.has(el)) return null;
  const form = el.closest('form,[role="dialog"]');
  const fields = form ? Array.from(form.querySelectorAll('input,textarea,select')) : [];
  if (fields.length > 128) return null;
  const options = el.tagName === 'SELECT' ?
    Array.from(el.options).map(o => [o.value,o.label,o.disabled,o.selected]) : null;
  const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/)
    .map(id => document.getElementById(id)?.textContent || '').join(' ');
  // A local fingerprint detects changes without retaining other field contents.
  const fingerprint = (text) => {
    let hash = 2166136261;
    for (let i=0; i<text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return String(hash >>> 0) + ':' + text.length;
  };
  return {
    document: registry.document, node: registry.nodes.get(el),
    value: 'value' in el ? String(el.value) : null,
    checked: 'checked' in el ? Boolean(el.checked) : el.getAttribute('aria-checked'),
    expanded: el.getAttribute('aria-expanded'), options: JSON.stringify(options),
    selection: JSON.stringify([el.getAttribute('aria-selected'), el.getAttribute('aria-sort'),
      el.getAttribute('aria-current'), el.getAttribute('data-state')]),
    signature: JSON.stringify([el.tagName, el.getAttribute('role'), el.getAttribute('type'),
      el.getAttribute('href'), el.getAttribute('aria-label'), labelled,
      Array.from(el.labels || []).map(l => l.textContent), el.textContent, el.getAttribute('formaction')]),
    region: fingerprint(form ? form.innerText : ''),
    fields: fingerprint(JSON.stringify(fields.map(f => [f.name, f.value, f.checked, f.disabled])))
  };
}"""


@dataclass(frozen=True)
class DecisionGuard:
    session_id: str
    page_id: str
    generation_id: str
    url: str
    target_id: str
    arguments: str
    node_guard: dict[str, Any]
    task_key: str
    deadline_at: float
    tool_name: str = "browser_batch_interact"
    phase_version: int = 0
    first_result: dict[str, Any] | None = None


def canonical_arguments(arguments: Any) -> str:
    value = json.loads(arguments) if isinstance(arguments, str) else arguments
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_binding(runtime: Any, guard: DecisionGuard, inputs: Any, session: Any) -> Any:
    if session is None or session.get_session_id() != guard.session_id:
        raise ValueError("browser_policy_wrong_session")
    phase = session.get_state("__browser_phase_budget_state__") or {}
    owner = phase.get("query_id") or session.get_session_id()
    task_key = f"{owner}:{phase.get('task_id')}:{phase.get('deadline_started_at')}"
    if task_key != guard.task_key:
        raise ValueError("browser_policy_wrong_task")
    if int((phase.get("active_phase_contract") or {}).get("version", 0)) != guard.phase_version:
        raise ValueError("browser_policy_phase_changed")
    if inputs.tool_name != guard.tool_name or canonical_arguments(inputs.tool_args) != guard.arguments:
        raise ValueError("browser_policy_arguments_changed")
    state = runtime._ensure_page_state()  # Runtime owns this check; never refresh a stale policy target.
    if state.page_id != guard.page_id or state.generation_id != guard.generation_id or state.url != guard.url:
        raise ValueError("browser_policy_stale_page")
    if not guard.target_id and guard.tool_name in {
        "browser_page_action", "browser_phase", "browser_probe_interactives", "browser_probe_cards"
    }:
        return None
    target = state.get_target(guard.target_id)
    if target is None or target.generation_id != guard.generation_id or not target.selector:
        raise ValueError("browser_policy_stale_target")
    return target


async def validate_guard(runtime: Any, guard: DecisionGuard, inputs: Any, session: Any) -> None:
    target = validate_binding(runtime, guard, inputs, session)
    if guard.tool_name == "browser_phase":
        return
    if target is None and guard.tool_name in {
        "browser_page_action", "browser_probe_interactives", "browser_probe_cards"
    }:
        from ..playwright_runtime.policy_page_action import READ_PAGE_OPERATIONS

        observing = guard.tool_name in {"browser_probe_interactives", "browser_probe_cards"} or (
            guard.tool_name == "browser_page_action" and json.loads(guard.arguments).get("op") in READ_PAGE_OPERATIONS
        )
        args = json.dumps({"url": guard.url, "guard": guard.node_guard, "observing": observing}, ensure_ascii=True)
        script = """async (page) => {
          const args = ARGS;
          if (page.url() !== args.url) return {ok:false};
          const current = await page.evaluate(PAGE_STATE);
          // Readers intentionally refresh the current viewport; document identity
          // remains mandatory, but asynchronous layout changes are not a stale target.
          return {ok:args.observing ? current.document === args.guard.document :
            JSON.stringify(current) === JSON.stringify(args.guard)};
        }""".replace("PAGE_STATE", PAGE_STATE_JS).replace("ARGS", args)
        if guard.first_result:
            from ..playwright_runtime.probes import build_card_probe_js
            from ..playwright_runtime.site_profiles import site_profiles_for_url

            probe = build_card_probe_js(max_cards=12, viewport_only=False, include_buttons=False,
                                        site_profiles=site_profiles_for_url(guard.url), generation_id=guard.generation_id)
            expected = json.dumps(guard.first_result, ensure_ascii=True)
            script = """async (page) => {
              const pageCheck = await (PAGE_CHECK)(page);
              if (!pageCheck.ok) return pageCheck;
              const observed = await (CARD_PROBE)(page), expected = EXPECTED;
              const first = (observed.cards || []).filter(card => card.result_index === 1 &&
                card.order_known === true && !card.is_ad &&
                ['main_result','primary_result','main_results'].includes(card.region));
              return {ok:observed.ok === true && first.length === 1 && first[0].title === expected.title &&
                (first[0].primary_link || first[0].href) === expected.href};
            }""".replace("PAGE_CHECK", script).replace("CARD_PROBE", probe).replace("EXPECTED", expected)
        await _validate_script(runtime, script)
        return
    # The observed DOM node must still be the same node, in the same document,
    # with the same field/option state. Playwright performs actionability at execution.
    args = json.dumps({"selector": target.selector, "url": guard.url, "guard": guard.node_guard}, ensure_ascii=False)
    script = """async (page) => {
      const args = ARGS;
      if (page.url() !== args.url) return {ok:false};
      const target = page.locator(args.selector);
      if (await target.count() !== 1) return {ok:false};
      return {ok:await target.evaluate((el, expected) => {
        const current = (NODE_STATE)(el);
        return current !== null && JSON.stringify(current) === JSON.stringify(expected) &&
          !el.matches(':disabled,[readonly]') && !el.closest('[inert],[aria-disabled="true"]');
      }, args.guard)};
    }""".replace("NODE_STATE", NODE_STATE_JS).replace("ARGS", args)
    await _validate_script(runtime, script)


async def _validate_script(runtime: Any, script: str) -> None:
    result = await runtime._call_playwright_run_code_unsafe(script)
    result = runtime._unwrap_mcp_text_result(result)
    # Use the same transport decoder as runtime observations, not arbitrary JSON mining.
    from ..utils.parsing import decode_mcp_result

    payload = decode_mcp_result(result)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("browser_policy_stale_node")
