# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded cart DOM adapter and deterministic SKU/quantity verification."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..utils.parsing import decode_mcp_result
from .evidence import same_page_url

# One fixed read, bounded rows, duplicate/missing identities are errors. No page
# text is interpreted as code and no DOM writes are available through this tool.
CART_READER = """async (page) => await page.evaluate((spec) => {
  const rows = [...document.querySelectorAll(spec.items_selector)];
  if (rows.length > 100) return {ok:false, error:'cart_reader_truncated'};
  const counts = [...document.querySelectorAll(spec.count_selector)];
  if (counts.length !== 1) return {ok:false, error:'cart_coverage_unknown'};
  const count = String(counts[0].value ?? counts[0].textContent).trim();
  if (!/^\\d{1,4}$/.test(count) || Number(count) !== rows.length)
    return {ok:false, error:'cart_rows_incomplete'};
  const items = Object.create(null);
  for (const row of rows) {
    const sku = row.getAttribute(spec.sku_attribute);
    const fields = [...row.querySelectorAll(spec.quantity_selector)];
    if (!sku || Object.hasOwn(items, sku) || fields.length !== 1) return {ok:false, error:'cart_identity_ambiguous'};
    const raw = String(fields[0].value ?? fields[0].textContent).trim();
    if (!/^\\d{1,4}$/.test(raw)) return {ok:false, error:'cart_quantity_ambiguous'};
    items[sku] = Number(raw);
  }
  return {ok:true, complete:true, coverage_count:Number(count), items, url:location.href};
}, __SPEC__)"""

# Fixed DOM inspection lets the LLM discover a reader without using an arbitrary
# evaluate script (which correctly closes the pre-write baseline boundary).
CART_INSPECTOR = """async (page) => await page.evaluate(() => {
  const visible = el => Boolean(el.getClientRects().length);
  const selector = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let node = el; node && node !== document.body && parts.length < 6; node = node.parentElement) {
      const siblings = node.parentElement
        ? [...node.parentElement.children].filter(n => n.tagName === node.tagName) : [];
      parts.unshift(node.tagName.toLowerCase() + ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')');
    }
    const value = 'body > ' + parts.join(' > ');
    return document.querySelectorAll(value).length === 1 ? value : '';
  };
  const candidates = [];
  for (const attr of ['data-sku', 'data-sku-id', 'data-skuid', 'data-variant-id']) {
    const rows = [...document.querySelectorAll('[' + attr + ']')].filter(visible);
    if (!rows.length || rows.length > 100) continue;
    const skus = rows.map(row => row.getAttribute(attr));
    if (skus.some(sku => !sku) || new Set(skus).size !== skus.length) continue;
    const quantity = ['input[type="number"]', 'input[class*="qty" i]', 'input[name*="quantity" i]',
      'input[class*="number-input" i]'].find(sel => rows.every(row => row.querySelectorAll(sel).length === 1));
    candidates.push({items_selector:'[' + attr + ']', sku_attribute:attr,
      quantity_selector:quantity || null, observed_skus:skus.slice(0,20), rows:rows.length});
  }
  const counts = [...document.querySelectorAll('[id*="cart" i],[class*="cart-count" i],'
    + '[class*="cart-num" i],[data-cart-count],[data-cart-lines]')].filter(visible)
    .filter(el => /^\\d{1,4}$/.test(String(el.value ?? el.textContent).trim())).slice(0,10)
    .map(el => ({selector:selector(el), value:String(el.value ?? el.textContent).trim(),
                label:el.getAttribute('aria-label') || el.getAttribute('title') || ''}));
  return {ok:true, url:location.href, reader_candidates:candidates, count_candidates:counts,
          complete:false, note:'Hints only. Confirm a distinct-line count and SKU+variant scope before baseline.'};
})"""


async def inspect_cart(runtime: Any) -> dict[str, Any]:
    raw = await runtime.call_playwright_run_code_unsafe(CART_INSPECTOR)
    data = decode_mcp_result(runtime.unwrap_mcp_text_result(raw))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ValueError("cart_inspection_unavailable: use existing observed targets or return partial")
    return data


async def read_cart(runtime: Any, item: dict[str, Any], state: dict[str, Any], *, baseline: bool) -> None:
    from .phase_contract import unresolved_writes

    spec = item["spec"]
    item["status"] = "unknown"
    raw = await runtime.call_playwright_run_code_unsafe(CART_READER.replace("__SPEC__", json.dumps(spec)))
    data = decode_mcp_result(runtime.unwrap_mcp_text_result(raw))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}
    complete = isinstance(data, dict) and data.get("ok") and data.get("complete") is True
    if not complete or not isinstance(data.get("items"), dict):
        item["status"] = "unknown"
        item["reason"] = "cart_read_incomplete_or_unrecognized"
        return
    items = data.get("items", {})
    if len(items) > 100 or any(
        not isinstance(key, str) or type(value) is not int or value < 0 for key, value in items.items()
    ):
        item["status"] = "unknown"
        return
    if baseline and "baseline" not in item:
        # A baseline taken after a possible write cannot prove a delta.
        if unresolved_writes(state) or state.get("cart_write_seen") or state.get("cart_baseline_closed"):
            item["status"] = "unknown"
            item["reason"] = "cart_baseline_not_available_before_write"
            return
        # Empty selectors on the wrong page cannot certify an empty cart.
        if not items and data.get("coverage_count") != 0:
            item["reason"] = "cart_baseline_empty_or_unrecognized"
            return
        item["baseline"] = {
            "items": dict(items),
            "url": data.get("url"),
            "observed_at": time.time(),
        }
    before = item.get("baseline")
    if not before or not same_page_url(data.get("url"), before["url"]):
        item["status"] = "unknown"
        item["reason"] = "cart_source_changed_or_baseline_missing"
        return
    expected = dict(before["items"])
    for sku, delta in spec["deltas"].items():
        expected[sku] = expected.get(sku, 0) + delta
    expected = {sku: qty for sku, qty in expected.items() if qty != 0}
    scope = (
        set(before["items"]) | set(expected) | set(items)
        if spec.get("preserve_existing", True)
        else set(spec["deltas"])
    )
    item["status"] = "satisfied" if all(items.get(sku, 0) == expected.get(sku, 0) for sku in scope) else "unsatisfied"
    item.pop("reason", None)
    item["observed_at"] = time.time()
    state["phase_observation_sequence"] = int(state.get("phase_observation_sequence", 0)) + 1
    item["observed_sequence"] = state["phase_observation_sequence"]
    item["evidence_ref"] = {
        "source": data.get("url"),
        "reader": "cart_sku_quantity",
        "observed_at": item["observed_at"],
    }
    item["observed_items"] = dict(items)
    # A multi-item phase can have an intermediate, verified cart effect. Do not
    # demand the second addition before allowing it after reconciling the first.
    # All quantities must stay within the requested deltas and unrelated items
    # must remain exact. This settles the action, not the whole phase.
    for entry in unresolved_writes(state):
        previous = (entry.get("cart_before") or {}).get(item["id"])
        if previous is None or item["id"] not in entry.get("effect_condition_ids", []):
            continue
        within = all(
            min(before["items"].get(sku, 0), expected.get(sku, 0))
            <= items.get(sku, 0)
            <= max(before["items"].get(sku, 0), expected.get(sku, 0))
            for sku in scope
        )
        effect_skus = (entry.get("effect_skus") or {}).get(item["id"]) or spec["deltas"]
        progressed = any((items.get(sku, 0) - previous.get(sku, 0)) * spec["deltas"][sku] > 0 for sku in effect_skus)
        monotonic = all(
            (items.get(sku, 0) - previous.get(sku, 0)) * delta >= 0 for sku, delta in spec["deltas"].items()
        )
        quantities_ok = within and progressed and monotonic
        settled = entry.get("single_write") or (
            item["status"] == "satisfied" and entry.get("execution_state") != "dispatched_unknown"
        )
        observed_after = item["observed_sequence"] > entry.get("dispatch_observation_sequence", float("inf"))
        if quantities_ok and settled and observed_after:
            from .execution_journal import verify_effects

            verified_steps = [
                s["index"]
                for s in entry.get("steps", [])
                if s.get("effect_domain") == "cart" and s.get("executed") is not False
            ]
            verify_effects(state, entry, item["evidence_ref"], verified_steps)


def _has_open_baseline(condition: dict[str, Any]) -> bool:
    if not condition.get("baseline") or "observed_items" not in condition:
        return False
    return condition.get("status") == "unsatisfied" and not condition.get("reason")


def prepare_effects(entry: dict[str, Any], state: dict[str, Any]) -> None:
    """Attach optional reader context; never require a cart proof protocol for dispatch."""
    conditions = [c for c in state.get("phase_requirements", []) if c["kind"] == "cart_delta"]
    available = [c for c in conditions if _has_open_baseline(c)]
    entry["cart_mutation"] = False
    if not available:
        return
    cart_steps = []
    cart_context = bool(re.search(r"购物车|加购|\bcart\b", str(state.get("goal", "")), re.I)) or any(
        c["kind"] == "cart_delta" for c in state.get("phase_requirements", [])
    )
    for step in entry["steps"]:
        control = step.get("control") or {}
        label = " ".join(str(control.get(key) or "") for key in ("name", "text", "kind"))
        explicit_cart = re.search(r"加购|加入.{0,5}购物车|add.{0,8}cart|remove.{0,12}cart", label, re.I)
        quantity_change = cart_context and re.search(r"数量|quantity|移除", label, re.I)
        capability = (control.get("decision_state") or {}).get("effect") or {}
        declared_cart = capability.get("domain") == "cart" and capability.get("operation") in {
            "add", "remove", "set_quantity"
        }
        # An unbound click followed by an Added check is a potential cart effect.
        # This only raises the safety requirement; text alone never proves success.
        confirmation = cart_context and step["op"] in {"click", "press", "press_key"} and any(
            s["index"] > step["index"] and re.fullmatch(
                r"(?:added(?: to (?:the )?cart)?|已加入(?:购物车)?|添加成功)", s.get("expected_feedback", ""), re.I
            ) for s in entry["steps"]
        )
        if step["impact"] != "read" and any((declared_cart, explicit_cart, quantity_change, confirmation)):
            step.update(impact="business", effect_domain="cart")
            cart_steps.append(step)
    entry["cart_mutation"] = bool(cart_steps)
    if not cart_steps:
        return
    for step in cart_steps:
        capability = ((step.get("control") or {}).get("decision_state") or {}).get("effect") or {}
        identities = capability.get("identities") or {}
        if identities and not any(identities.get(c["spec"]["sku_attribute"]) in c["spec"]["deltas"] for c in available):
            raise ValueError("cart_target_sku_not_in_requested_deltas: observed target conflicts with the bound reader")
    entry["effect_skus"] = {}
    for condition in available:
        identities = [(((s.get("control") or {}).get("decision_state") or {}).get("effect") or {}).get("identities")
                      or {} for s in cart_steps]
        skus = [identity.get(condition["spec"]["sku_attribute"]) for identity in identities]
        if all(skus):
            entry["effect_skus"][condition["id"]] = [sku for sku in skus if sku in condition["spec"]["deltas"]]
    entry["single_write"] = len(cart_steps) == 1 and all(
        s["impact"] in {"read", "local_ui"} or s in cart_steps for s in entry["steps"]
    )
    entry["cart_before"] = {c["id"]: dict(c["observed_items"]) for c in available}
    entry["effect_condition_ids"] = [c["id"] for c in available]


def record_effects(state: dict[str, Any], call_id: str) -> None:
    """Close baseline collection only for actual or possible business effects."""
    entry = next((e for e in state.get("execution_journal", []) if e["call_id"] == call_id), None)
    if entry is None:
        return
    affected = [
        s
        for s in entry.get("steps", [])
        if s.get("executed") is not False and s.get("impact") in {"business", "unknown"}
    ]
    if not affected:
        return
    if not any(c["kind"] == "cart_delta" for c in state.get("phase_requirements", [])):
        return
    state["cart_baseline_closed"] = True
    for item in state.get("phase_requirements", []):
        if item["kind"] == "cart_delta":
            item["status"] = "unknown"
    if any(s.get("effect_domain") == "cart" for s in affected):
        state["cart_write_seen"] = True
