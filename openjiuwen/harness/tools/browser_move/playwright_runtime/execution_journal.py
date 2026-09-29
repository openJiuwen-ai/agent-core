# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared dispatch/step facts; domain adapters supply business-effect requirements."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .browser_logging import browser_agent_log_info
from .evidence import observed_label
from .phase_contract import node_identity, same_node, save, task_state, unresolved_writes

_ACTIVE: ContextVar[tuple[Any, str, dict[str, bool]] | None] = ContextVar("browser_execution_receipt", default=None)
_READ_OPS = {
    "navigate",
    "navigate_back",
    "scroll",
    "screenshot",
    "extract",
    "get_text",
    "extract_text",
    "extract_value",
    "sleep",
}
_READ_TOOLS = ("snapshot", "probe_", "browser_find", "get_", "recall", "runtime_health", "browser_phase", "wait_for")


def is_write(tool: str, args: dict[str, Any]) -> bool:
    name = tool.lower()
    if "browser" not in name or any(token in name for token in _READ_TOOLS):
        return False
    if "navigate" in name or "screenshot" in name or name.endswith("browser_page_action"):
        return False
    if name.endswith("browser_tabs"):
        return args.get("action") not in {"list", "select"}
    if "batch_interact" in name:
        return any(
            step.get("op") not in _READ_OPS and not str(step.get("op", "")).startswith("wait")
            for step in args.get("steps", [])
            if isinstance(step, dict)
        )
    return True  # Unknown scripts cannot declare themselves read-only.


def arguments(inputs: Any) -> dict[str, Any]:
    value = getattr(inputs, "tool_args", {})
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _id(inputs: Any) -> str:
    return str(getattr(getattr(inputs, "tool_call", None), "id", "") or "")


def _control(runtime: Any, step: dict[str, Any]) -> dict[str, Any]:
    if runtime is None:
        return {}
    page = runtime._ensure_page_state()
    export = getattr(page, "export_decision_targets", None)
    if not callable(export):
        return {}
    matches = []
    for control in export():
        target = page.get_target(control["target_id"])
        identifiers = {control["target_id"], getattr(target, "selector", None), getattr(target, "ref", None)}
        if any(step.get(key) and step[key] in identifiers for key in ("target_id", "selector", "ref", "target")):
            matches.append(control)
    if matches:
        return matches[0] if len(matches) == 1 else {}
    # Native AX and ordinary Probe targets need no Jev-only node record to
    # classify their observed capability. Never refresh stale identities here.
    resolve = getattr(page, "resolve_target", None)
    generation = getattr(page, "generation_id", None)
    if not callable(resolve) or not isinstance(generation, str):
        return {}
    raw = str(step.get("ref") or step.get("target") or "")
    try:
        target = resolve(
            generation_id=generation,
            target_id=step.get("target_id", ""),
            ref=raw if re.fullmatch(r"(?:f\d+)?e\d+", raw) else "",
            selector=step.get("selector") or (raw if raw and not re.fullmatch(r"(?:f\d+)?e\d+", raw) else ""),
        )
    except ValueError:
        return {}
    return {**target.compact_index(), "decision_state": target.decision_state} if target is not None else {}


def _impact(op: str, control: dict[str, Any]) -> str:
    if op in _READ_OPS or op.startswith("wait"):
        return "read"
    details = control.get("decision_state") or {}
    if op in {"hover", "navigate", "navigate_back", "scroll", "select_tab"}:
        return "local_ui"  # Reveals UI; never certifies a business effect.
    if (op == "click" and details.get("node_guard") and not details.get("effect")
            and control.get("role") == "button" and details.get("input_type") != "submit"
            and re.fullmatch(r"close|dismiss|关闭|关闭弹窗|收起", observed_label(control), re.I)):
        return "local_ui"
    # Only observed UI capabilities qualify; unrecognized submit/save/scripts
    # remain unknown. User-supplied safety declarations cannot lower this class.
    if control and (
        op in {"fill", "type"}
        and (details.get("tag") in {"input", "textarea"} or control.get("role") in {"textbox", "searchbox", "combobox"})
        and not details.get("sensitive")
        or op in {"click", "press", "press_key"}
        and (details.get("search_like") or control.get("kind") == "search")
        or op == "click"
        and (control.get("href") or details.get("href"))
        or op in {"click", "select_option", "set_checked"}
        and str(control.get("kind", "")).startswith(("sort", "filter", "rating_filter", "calendar_date"))
    ):
        return "local_ui"
    return "unknown"


def prepare(session: Any, inputs: Any, runtime: Any = None, *, effect_adapter: Any = None) -> dict[str, Any] | None:
    state = task_state(session)
    tool, args = str(getattr(inputs, "tool_name", "")), arguments(inputs)
    if not state or not is_write(tool, args):
        return None
    call_id = _id(inputs)
    if not call_id:
        return None
    journal = state.get("execution_journal", [])
    if any(entry["call_id"] == call_id for entry in journal):
        raise ValueError("browser_call_already_prepared: never replay a call id")
    steps = []
    raw_steps = args.get("steps") if tool.endswith("browser_batch_interact") else None
    native_op = tool.rsplit("browser_", 1)[-1]
    for index, raw in enumerate(raw_steps if isinstance(raw_steps, list) else [{**args, "op": native_op}]):
        control = _control(runtime, raw)
        op = str(raw.get("op") or "")
        step = {
            "index": index,
            "op": op,
            "impact": _impact(op, control),
            "control": control,
            "target": node_identity(control),
            "execution_state": "prepared",
            "executed": False,
            "optional": bool(raw.get("optional")),
            "target_name": str(control.get("name") or control.get("text") or "")[:160],
        }
        if op in {"fill", "type", "select_option"}:
            step["expected_value"] = raw.get("value", raw.get("text"))
        query = (control.get("decision_state") or {}).get("search_query") or {}
        if step["impact"] == "local_ui" and query and (
            op == "click" or op in {"press", "press_key"} and raw.get("key") == "Enter"
        ):
            expected = query.get("value")
            for preceding in steps:
                if preceding["op"] in {"fill", "type"} and same_node(query, preceding.get("control") or {}):
                    expected = preceding.get("expected_value")
            if expected:
                step["expected_query"] = str(expected)
        if op == "wait_for_text":
            step["expected_feedback"] = str(raw.get("text") or "")[:200]
        steps.append(step)
    phase = state.get("active_phase_contract") or {}
    entry = {
        "call_id": call_id,
        "tool": tool,
        "model_source": "jev" if call_id.startswith("jev_") else "llm",
        "phase_version": phase.get("version", 0),
        "source": str(getattr(runtime._ensure_page_state(), "url", "") or "") if runtime is not None else "",
        "condition_ids": list(phase.get("condition_ids", [])),
        "execution_state": "prepared",
        "prepared_at": time.time(),
        "steps": steps,
        "continue_on_error": bool(args.get("continue_on_error")),
        "dispatch_observation_sequence": int(state.get("phase_observation_sequence", 0)),
    }
    if effect_adapter is not None:
        effect_adapter(entry, state)
    entry["impact"] = (
        "business"
        if any(s["impact"] == "business" for s in steps)
        else ("unknown" if any(s["impact"] == "unknown" for s in steps) else "local_ui")
    )
    if unresolved_writes(state) and entry["impact"] not in {"read", "local_ui"}:
        raise ValueError(
            "browser_write_requires_reconciliation: verify outstanding business effects with fixed reads; "
            "snapshot/probes/navigation and known local UI remain available. Do not repeat unknown writes."
        )
    pending_ids = {e["call_id"] for e in unresolved_writes(state)}
    pending = [e for e in journal if e["call_id"] in pending_ids]
    if len(pending) >= 63:
        raise ValueError("browser_pending_effect_budget_exhausted")
    settled = [e for e in journal if e["call_id"] not in pending_ids]
    state["execution_journal"] = [*pending, *settled[-(63 - len(pending)) :], entry]
    save(session, state)
    return entry


@contextmanager
def execution_scope(session: Any, inputs: Any):
    tracker = {"dispatched": False, "tool_name": str(getattr(inputs, "tool_name", ""))}
    token = _ACTIVE.set((session, _id(inputs), tracker))
    try:
        yield tracker
    finally:
        _ACTIVE.reset(token)


def active_policy_call(tool_name: str = "") -> bool:
    current = _ACTIVE.get()
    return bool(current and current[1].startswith("jev_")
                and (not tool_name or current[2]["tool_name"] == tool_name))


def mark_dispatched() -> None:
    current = _ACTIVE.get()
    if current is None:
        return
    session, call_id, tracker = current
    tracker["dispatched"] = True
    state = task_state(session)
    entry = next((e for e in state.get("execution_journal", []) if e["call_id"] == call_id), None)
    if entry is not None and entry["execution_state"] == "prepared":
        entry.update(execution_state="dispatched", dispatched_at=time.time())
        entry["dispatch_observation_sequence"] = int(state.get("phase_observation_sequence", 0))
        save(session, state)
        _log(state, entry)


def _log(state: dict[str, Any], entry: dict[str, Any]) -> None:
    browser_agent_log_info(
        "[BROWSER_EXECUTION] %s",
        json.dumps(
            {
                **{
                    key: entry.get(key)
                    for key in ("call_id", "tool", "model_source", "phase_version", "execution_state", "impact")
                },
                "task_id": state.get("task_id"),
                "steps": [
                    {k: s.get(k) for k in ("index", "op", "execution_state", "impact")} for s in entry.get("steps", [])
                ],
            }
        ),
    )


def lean_guards() -> bool:
    """F_15 trial switch: OPENJIUWEN_BROWSER_GUARDS=lean. Anything else keeps today's strict guards."""
    return os.getenv("OPENJIUWEN_BROWSER_GUARDS", "").strip().lower() == "lean"


def _never_performed(error: Any) -> bool:
    """Playwright timed out while still waiting (e.g. for a visible, enabled, stable element).

    Its call log says "performing <action>" only once the action is actually sent to the page; an
    error without a call log proves nothing and stays uncertain.
    """
    text = str(error or "")
    return "Timeout" in text and "waiting for" in text and "performing" not in text


def _native_preflight_failure(tool: str, result: dict[str, Any]) -> bool:
    if not tool.endswith(("browser_click", "browser_type", "browser_select_option", "browser_press_key")):
        return False
    # Match the native executor's exact error envelope, not arbitrary page text.
    error = result.get("error") or result.get("result") or ""
    return isinstance(error, str) and bool(
        re.fullmatch(
            r"(?:### Error\s+)?(?:Error: )?Ref [\w.-]+ not found in the current page snapshot\. "
            r"Try capturing new snapshot\.?\s*",
            error,
        )
    )


def _aggregate(entry: dict[str, Any]) -> None:
    steps = entry["steps"]
    states = {s["execution_state"] for s in steps}
    if "dispatched_unknown" in states:
        entry["execution_state"] = "dispatched_unknown"
    elif states <= {"rejected_before_dispatch", "not_started"}:
        entry["execution_state"] = "rejected_before_dispatch"
    elif states <= {"acknowledged", "verified"}:
        entry["execution_state"] = "verified" if states == {"verified"} else "acknowledged"
    else:
        entry["execution_state"] = "partial"
    entry["executed"] = None if "dispatched_unknown" in states else any(s.get("executed") is True for s in steps)
    unknown_effects = [
        s for s in steps if s["execution_state"] == "dispatched_unknown" and s["impact"] not in {"read", "local_ui"}
    ]
    if not unknown_effects and entry["execution_state"] == "dispatched_unknown":
        entry["impact"] = "local_ui"


def record_result(session: Any, inputs: Any, outcome: dict[str, Any], result: Any) -> dict[str, Any]:
    state = task_state(session)
    result = result if isinstance(result, dict) else {}
    success = bool(outcome.get("success")) and not outcome.get("denied")
    rejected = (
        outcome.get("denied")
        or result.get("executed") is False
        or _native_preflight_failure(str(getattr(inputs, "tool_name", "")), result)
        or lean_guards() and not success
        and str(getattr(inputs, "tool_name", "")).endswith(("browser_click", "browser_type", "browser_select_option"))
        and _never_performed(result.get("error") or result.get("result"))
    )
    executed = False if rejected else True if success else result.get("executed")
    status = "rejected_before_dispatch" if rejected else "acknowledged" if success else "dispatched_unknown"
    entry = next((e for e in state.get("execution_journal", []) if e["call_id"] == _id(inputs)), None)
    if entry is None:
        return {"executed": executed, "execution_state": status}
    if outcome.get("denied") and entry["execution_state"] != "prepared":
        # A duplicate/replayed call's rejection cannot rewrite its earlier effect.
        return {"executed": False, "execution_state": "rejected_before_dispatch"}
    indexed = {s.get("index", i): s for i, s in enumerate(result.get("steps") or []) if isinstance(s, dict)}
    stopped = False
    for step in entry["steps"]:
        received = indexed.get(step["index"])
        if rejected:
            step.update(execution_state="rejected_before_dispatch", executed=False)
        elif received is not None:
            ok = received.get("ok") is True
            ran = True if ok else received.get("executed")
            if not ok and ran is not False and lean_guards() and _never_performed(received.get("error")):
                ran = False  # Timed out before the action was sent: not a write, nothing to reconcile.
            step.update(
                execution_state="acknowledged"
                if ok
                else "rejected_before_dispatch"
                if ran is False
                else "dispatched_unknown",
                executed=ran,
                error=str(received.get("error") or "")[:500],
            )
            stopped = not ok and not entry["continue_on_error"] and not step["optional"]
            if ok and step.get("expected_feedback"):
                step["observed_feedback"] = step["expected_feedback"]
        elif stopped:
            step.update(execution_state="not_started", executed=False)
        elif indexed:
            step.update(execution_state="dispatched_unknown", executed=None)
        else:
            step.update(execution_state=status, executed=executed)
        if step.get("executed") is True and step.get("expected_feedback"):
            step["observed_feedback"] = step["expected_feedback"]
            step["evidence_ref"] = {"call_id": entry["call_id"], "source": entry.get("source"),
                                    "reader": "wait_for_text"}
    _aggregate(entry)
    if not rejected:
        entry.setdefault("dispatched_at", time.time())
    if success:
        entry["acknowledged_at"] = time.time()
    save(session, state)
    _log(state, entry)
    return {
        "executed": entry["executed"],
        "execution_state": entry["execution_state"],
        "execution": project_entry(entry),
    }


def _search_result_observed(entry: dict[str, Any], step: dict[str, Any], observation: dict[str, Any]) -> bool:
    expected = step.get("expected_query")
    if not expected or step.get("impact") != "local_ui":
        return False
    try:
        before, after = urlsplit(str(entry.get("source") or "")), urlsplit(str(observation.get("url") or ""))
        if not before.hostname or before.hostname.removeprefix("www.") != (after.hostname or "").removeprefix("www."):
            return False
        params = parse_qs(after.query)
    except ValueError:
        return False
    return any(expected in params.get(key, []) for key in ("q", "wd", "query", "keyword", "keywords", "search_query"))


def reconcile_observation(state: dict[str, Any], observation: dict[str, Any]) -> None:
    """Confirm exact local field effects from fresh observations, never business success."""
    if not observation.get("capture_id") or observation.get("capture_id") == state.get("phase_invalidated_capture"):
        return
    for entry in state.get("execution_journal", []):
        if state.get("phase_observation_sequence", 0) <= entry.get("dispatch_observation_sequence", float("inf")):
            continue
        changed = False
        for step in entry.get("steps", []):
            if step.get("impact") != "local_ui" or step.get("execution_state") not in {
                "acknowledged",
                "dispatched_unknown",
            }:
                continue
            matching = [c for c in observation.get("controls", []) if same_node(step.get("target", {}), c)]
            value_observed = (
                len(matching) == 1
                and step.get("expected_value") is not None
                and (matching[0].get("decision_state") or {}).get("current_value") == step["expected_value"]
            )
            query_observed = _search_result_observed(entry, step, observation)
            if value_observed or query_observed:
                step.update(
                    execution_state="verified",
                    executed=True,
                    evidence_ref={"capture_id": observation["capture_id"], "url": observation.get("url"),
                                  "kind": "search_query_observed" if query_observed else "field_value_observed"},
                )
                changed = True
        if changed:
            _aggregate(entry)
            _log(state, entry)


def project_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            k: copy.deepcopy(entry[k])
            for k in (
                "call_id",
                "tool",
                "model_source",
                "phase_version",
                "execution_state",
                "executed",
                "impact",
                "requires_verification",
                "evidence_ref",
                "source",
            )
            if k in entry
        },
        "steps": [
            {
                k: copy.deepcopy(s[k])
                for k in (
                    "index",
                    "op",
                    "target_name",
                    "impact",
                    "execution_state",
                    "executed",
                    "effect_domain",
                    "effect_verified",
                    "error",
                    "evidence_ref",
                    "observed_feedback",
                )
                if k in s
            }
            for s in entry.get("steps", [])
        ],
    }


def project(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "recent": [project_entry(e) for e in state.get("execution_journal", [])[-6:]],
        "business_effects": [project_entry(e) for e in state.get("execution_journal", [])
                             if e.get("cart_mutation") or e.get("requires_verification")][-12:],
        "unresolved": [project_entry(e) for e in unresolved_writes(state)],
        "recovery": "read_and_reconcile_before_business_retry"
        if unresolved_writes(state)
        else "continue_unfinished_steps",
    }


def verify_effects(state: dict[str, Any], entry: dict[str, Any], evidence: dict[str, Any], indices: list[int]) -> None:
    """Record a domain adapter's verified effect; retain original dispatch facts."""
    entry.update(
        execution_state="verified", verified_at=time.time(), evidence_ref=evidence, requires_verification=False
    )
    for step in entry.get("steps", []):
        if step["index"] in indices:
            step.update(effect_verified=True, evidence_ref=evidence)
    _log(state, entry)
