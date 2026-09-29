# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional current intent and bounded conditions, updated by the observation lifecycle.

This module contains no model calls and never mutates the browser. Selectors and
node identities remain local; only the bounded public projection reaches Jev.
"""

from __future__ import annotations

import copy
import json
import re
import time
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

from .browser_logging import browser_agent_log_info
from .cart_verification import CART_READER as CART_READER  # compatibility export
from .cart_verification import inspect_cart, read_cart
from .evidence import observe_acceptance

PHASE_KEY = "__browser_phase_budget_state__"
OPERATIONS = {
    "click",
    "fill",
    "press",
    "select_option",
    "set_checked",
    "navigate",
    "navigate_back",
    "scroll",
    "hover",
    "select_tab",
    "read_text",
    "find",
    "snapshot",
    "tabs",
    "wait",
}
CONDITIONS = {
    "control_value",
    "control_selected",
    "url_query",
    "url",
    "evidence",
    "cart_delta",
}


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
_CONDITION_FIELDS = {
    "control_value": ({"target_id": _TEXT, "value": _TEXT}, ["target_id", "value"]),
    "control_selected": ({"target_id": _TEXT}, ["target_id"]),
    "url_query": ({"key": _TEXT, "value": {"type": "string", "maxLength": 2000}}, ["key", "value"]),
    "url": ({"value": _TEXT}, ["value"]),
    "evidence": ({k: _TEXT for k in ("field", "variant", "query", "source", "entity")}, ["field", "variant"]),
    "cart_delta": ({
        **{k: _TEXT for k in ("items_selector", "sku_attribute", "quantity_selector", "count_selector")},
        "deltas": {"type": "object", "minProperties": 1, "maxProperties": 20,
                   "additionalProperties": {"type": "integer", "minimum": -99, "maximum": 99}},
        "preserve_existing": {"type": "boolean"},
    }, ["items_selector", "sku_attribute", "quantity_selector", "count_selector", "deltas"]),
}
CONDITION_SCHEMAS = {
    kind: _object_schema({"kind": {"const": kind}, **properties}, ["kind", *required])
    for kind, (properties, required) in _CONDITION_FIELDS.items()
}
for _kind in ("evidence", "cart_delta"):
    CONDITION_SCHEMAS[_kind]["properties"]["requirement_id"] = {
        "type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_:-]{0,79}$"
    }
CONDITION_SCHEMAS["evidence"]["anyOf"] = [{"required": ["query"]}, {"required": ["source"]}]
PHASE_SCHEMA = _object_schema({
    "op": {"type": "string", "enum": ["set", "verify"]},
    "phase_version": {"type": "integer", "minimum": 0},
    "inspect_cart": {"type": "boolean"},
    "objective": _TEXT,
    "allowed_operations": {"type": "array", "minItems": 1, "maxItems": len(OPERATIONS),
                           "items": {"enum": sorted(OPERATIONS)}, "uniqueItems": True},
    "target_ids": {"type": "array", "maxItems": 30, "items": _TEXT, "uniqueItems": True},
    "bound_values": {"type": "array", "maxItems": 20,
                     "items": _object_schema({"target_id": _TEXT, "value": _TEXT}, ["target_id", "value"])},
    "conditions": {"type": "array", "maxItems": 12, "items": {"oneOf": list(CONDITION_SCHEMAS.values())}},
}, ["op"])
PHASE_SCHEMA["oneOf"] = [
    {"properties": {"op": {"const": "set"}}, "required": ["objective"],
     "not": {"anyOf": [{"required": ["phase_version"]}, {"required": ["inspect_cart"]}]}},
    {"properties": {"op": {"const": "verify"}},
     "not": {"anyOf": [{"required": [k]} for k in ("objective", "allowed_operations", "target_ids",
                                                    "bound_values", "conditions")]}},
]


class PhaseInputError(ValueError):
    def __init__(self, code: str, **details: Any):
        self.details = {"code": code, **details}
        super().__init__(f"{code}: {json.dumps(details, ensure_ascii=False)}")


def validate_request(args: dict[str, Any]) -> None:
    """Provider schema plus focused field errors, shared by rail and direct tools."""
    conditions = args.get("conditions", [])
    for index, condition in enumerate(conditions if isinstance(conditions, list) else []):
        kind = condition.get("kind") if isinstance(condition, dict) else None
        if kind not in CONDITION_SCHEMAS:
            raise PhaseInputError("invalid_phase_condition", path=f"conditions[{index}].kind",
                                  allowed=sorted(CONDITIONS), correction="Use kind (not type), e.g. kind=url_query.")
        error = next(Draft202012Validator(CONDITION_SCHEMAS[kind]).iter_errors(condition), None)
        if error:
            path = ".".join([f"conditions[{index}]", *map(str, error.absolute_path)])
            raise PhaseInputError("invalid_phase_condition", path=path, detail=error.message,
                                  required=CONDITION_SCHEMAS[kind]["required"])
    error = next(Draft202012Validator(PHASE_SCHEMA).iter_errors(args), None)
    if error:
        raise PhaseInputError("invalid_phase_request", path=".".join(map(str, error.absolute_path)) or "$",
                              detail=error.message, correction='set: {"op":"set","objective":"..."}; '
                              'verify: {"op":"verify"} (optional phase_version or inspect_cart:true).')


def binding_targets(controls: list[dict[str, Any]], *, limit: int = 30) -> dict[str, Any]:
    """Only publish identities accepted by _control, without local node guards."""
    counts: dict[str, int] = {}
    for control in controls:
        key = control.get("target_id")
        counts[key] = counts.get(key, 0) + 1
    valid = [c for c in controls if c.get("target_id") and counts[c["target_id"]] == 1
             and node_identity(c)["document"] and node_identity(c)["node"] is not None]
    return {
        "targets": [{"target_id": c["target_id"], "name": str(c.get("name") or c.get("text") or "")[:120],
                     "role": c.get("role"), "field": (c.get("decision_state") or {}).get("tag")}
                    for c in valid[:limit]],
        "omitted": max(0, len(valid) - limit), "unbound": len(controls) - len(valid),
        "refresh": "browser_probe_interactives or browser_phase verify; use these target_ids, not AX refs",
    }


def task_state(session: Any) -> dict[str, Any]:
    value = session.get_state(PHASE_KEY) if session is not None else None
    return value if isinstance(value, dict) else {}


def save(session: Any, state: dict[str, Any]) -> None:
    if session is not None and callable(getattr(session, "update_state", None)):
        session.update_state({PHASE_KEY: state})


def node_identity(control: dict[str, Any]) -> dict[str, Any]:
    guard = (control.get("decision_state") or {}).get("node_guard") or {}
    return {key: guard.get(key) for key in ("document", "node", "signature")}


def same_node(binding: dict[str, Any], control: dict[str, Any]) -> bool:
    identity = node_identity(control)
    return (
        bool(identity["document"] and identity["node"] is not None)
        and all(binding.get(key) == identity[key] for key in ("document", "node"))
        and ("signature" not in binding or binding["signature"] == identity["signature"])
    )


def unresolved_writes(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in state.get("execution_journal", [])
        if (
            entry.get("execution_state") in {"prepared", "dispatched", "dispatched_unknown"}
            and entry.get("impact", "unknown") not in {"read", "local_ui"}
        )
        or entry.get("requires_verification")
        and entry.get("execution_state") != "verified"
    ]


def missing_conditions(state: dict[str, Any]) -> list[str]:
    return [
        item["id"] for item in state.get("phase_requirements", []) if item.get("status") not in {"satisfied", "expired"}
    ]


def project_phase(state: dict[str, Any]) -> dict[str, Any]:
    contract = state.get("active_phase_contract") or {}
    return {
        "version": contract.get("version", 0),
        "objective": contract.get("objective", ""),
        "allowed_operations": contract.get("allowed_operations", []),
        "bound_values": [
            {"field": b.get("name"), "value": b["value"], "source": b.get("source")}
            for b in contract.get("bindings", [])
        ],
        "conditions": [
            {
                **{key: item.get(key) for key in ("id", "kind", "status", "evidence_ref")},
                "expected": {
                    key: value
                    for key, value in item["spec"].items()
                    if key in {"key", "value", "field", "variant", "query", "deltas"}
                },
                **(
                    {
                        "baseline": item.get("baseline", {}).get("items"),
                        "observed": item.get("observed_items"),
                    }
                    if item["kind"] == "cart_delta"
                    else {}
                ),
            }
            for item in state.get("phase_requirements", [])
        ],
        "missing_conditions": missing_conditions(state),
        "unknown_writes": [
            {key: item.get(key) for key in ("call_id", "tool", "phase_version", "execution_state")}
            for item in unresolved_writes(state)
        ],
        "status": contract.get("status", "unplanned"),
    }


def _control(controls: list[dict[str, Any]], target_id: Any) -> dict[str, Any]:
    matches = [item for item in controls if item.get("target_id") == target_id]
    identity = node_identity(matches[0]) if len(matches) == 1 else {}
    if len(matches) != 1 or not identity.get("document") or identity.get("node") is None:
        reason = ("not_in_current_observation" if not matches
                  else "non_unique" if len(matches) > 1 else "missing_identity")
        raise PhaseInputError("phase_target_requires_fresh_unique_observation", target_id=target_id,
                              reason=reason, bindings=binding_targets(controls),
                              correction="Refresh and rebind; omit optional binding for an objective-only update.")
    return matches[0]


def _text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid_phase_text")
    return value.strip()


def set_phase(state: dict[str, Any], args: dict[str, Any], controls: list[dict[str, Any]]) -> None:
    """Validate all input before updating state; cannot accept model-reported proof."""
    validate_request({"op": "set", **args})
    objective = _text(args.get("objective"), 2000)
    operations = args.get("allowed_operations", sorted(OPERATIONS))
    if not isinstance(operations, list) or not operations or set(operations) - OPERATIONS:
        raise ValueError("invalid_phase_operations")
    version = int((state.get("active_phase_contract") or {}).get("version", 0)) + 1
    targets = args.get("target_ids", [])
    if not isinstance(targets, list) or len(targets) > 30:
        raise ValueError("invalid_phase_targets")
    target_bindings = [node_identity(_control(controls, target)) for target in targets]
    bindings = []
    values = args.get("bound_values", [])
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("invalid_phase_bindings")
    for value in values:
        if not isinstance(value, dict) or set(value) != {"target_id", "value"}:
            raise ValueError("invalid_phase_binding")
        control = _control(controls, value["target_id"])
        details = control.get("decision_state") or {}
        if (
            details.get("sensitive")
            or details.get("readonly")
            or details.get("tag") not in {"input", "textarea", "select"}
        ):
            raise ValueError("phase_binding_not_a_writable_field")
        bindings.append(
            {
                **node_identity(control),
                "name": control.get("name", ""),
                "value": _text(value["value"], 200),
                "source": "llm_phase",
            }
        )
    specs = args.get("conditions", [])
    if not isinstance(specs, list) or len(specs) > 12:
        raise ValueError("phase_requires_bounded_conditions")
    # Node/URL conditions belong to the previous local intent, not permanent
    # user requirements. Durable evidence and business effects survive replans.
    requirements = copy.deepcopy(
        [item for item in state.get("phase_requirements", []) if item["kind"] in {"evidence", "cart_delta"}]
    )
    new_conditions = []
    for index, source in enumerate(specs):
        if not isinstance(source, dict) or source.get("kind") not in CONDITIONS:
            raise ValueError("invalid_phase_condition")
        kind = source["kind"]
        allowed = {
            "control_value": {"kind", "target_id", "value"},
            "control_selected": {"kind", "target_id"},
            "url_query": {"kind", "key", "value"},
            "url": {"kind", "value"},
            "evidence": {"kind", "field", "variant", "query", "source", "entity", "requirement_id"},
            "cart_delta": {
                "kind",
                "items_selector",
                "sku_attribute",
                "quantity_selector",
                "count_selector",
                "deltas",
                "preserve_existing",
                "requirement_id",
            },
        }[kind]
        if set(source) - allowed:
            raise ValueError("condition_contains_untrusted_proof_or_unknown_field")
        spec = copy.deepcopy(source)
        if kind.startswith("control_"):
            observed = _control(controls, spec.pop("target_id", None))
            spec["target"] = node_identity(observed)
            if kind == "control_value":
                spec["value"] = _text(spec.get("value"), 200)
        elif kind in {"url", "url_query"}:
            if not isinstance(spec.get("value"), str) or len(spec["value"]) > 2000:
                raise ValueError("invalid_phase_url_condition")
            if kind == "url_query":
                _text(spec.get("key"), 100)
        elif kind == "evidence":
            for key in ("field", "variant"):
                spec[key] = _text(spec.get(key), 300)
            if not spec.get("query") and not spec.get("source"):
                raise ValueError("evidence_requires_query_or_source")
            for key in ("query", "source"):
                if key in spec:
                    spec[key] = _text(spec[key], 2000 if key == "source" else 300)
        else:
            for key in ("items_selector", "quantity_selector", "count_selector"):
                spec[key] = _text(spec.get(key), 500)
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_:-]{0,79}", str(spec.get("sku_attribute", ""))):
                raise ValueError("cart_requires_stable_sku_attribute")
            deltas = spec.get("deltas")
            if (
                not isinstance(deltas, dict)
                or not 1 <= len(deltas) <= 20
                or any(
                    not isinstance(sku, str) or not sku or len(sku) > 200 or type(delta) is not int or abs(delta) > 99
                    for sku, delta in deltas.items()
                )
            ):
                raise ValueError("invalid_cart_deltas")
            if type(spec.get("preserve_existing", True)) is not bool:
                raise ValueError("invalid_cart_preservation_scope")
            spec.setdefault("preserve_existing", True)
            if not spec["preserve_existing"] and re.search(
                r"保留|原有|原来|preserv|keep.{0,20}(?:existing|original)", str(state.get("goal", "")), re.I
            ):
                raise ValueError("cart_requires_preserve_existing")
        # An identical condition retains its original evidence/baseline.
        existing = next((item for item in requirements if item["spec"] == spec), None)
        logical_id = spec.get("requirement_id")
        rebound = next((item for item in requirements if item["spec"].get("requirement_id") == logical_id), None) \
            if logical_id else None
        if existing is None and rebound is not None:
            mutable = {"items_selector", "sku_attribute", "quantity_selector", "count_selector"}
            if kind != "cart_delta" or any(
                rebound["spec"].get(k) != spec.get(k) for k in set(rebound["spec"]) | set(spec) if k not in mutable
            ):
                raise ValueError("requirement_rebinding_cannot_change_expected_business_result")
            # Rebinding a reader cannot reset the original baseline or settle uncertainty.
            rebound.setdefault("binding_history", []).append(copy.deepcopy(rebound["spec"]))
            del rebound["binding_history"][:-4]
            rebound.update(spec=spec, status="unknown", reason="cart_reader_rebound_requires_read")
            existing = rebound
        if existing is None:
            if len(requirements) >= 64:
                raise ValueError("phase_requirement_budget_exhausted")
            existing = {
                "id": f"requirement:{logical_id}" if logical_id else f"phase-{version}-{index}",
                "kind": kind,
                "spec": spec,
                "status": "unknown",
                "phase_version": version,
            }
            requirements.append(existing)
        new_conditions.append(existing["id"])
    current = state.get("active_phase_contract") or {}
    previous_specs = [
        c["spec"] for c in state.get("phase_requirements", []) if c["id"] in current.get("condition_ids", [])
    ]
    next_specs = [c["spec"] for c in requirements if c["id"] in new_conditions]
    if (
        current.get("objective") == objective
        and current.get("allowed_operations") == list(dict.fromkeys(operations))
        and current.get("targets") == target_bindings
        and current.get("bindings") == bindings
        and previous_specs == next_specs
    ):
        return  # Repeating metadata is not a new intent or a policy re-entry.
    if current:
        history = state.setdefault("phase_history", [])
        history.append({"version": current.get("version"), "objective": current.get("objective"),
                        "conditions": copy.deepcopy(project_phase(state)["conditions"]),
                        "replaced_by": version})
        del history[:-6]
    state["phase_requirements"] = requirements
    slots = state.setdefault("required_evidence_slots", [])
    fields = state.setdefault("required_fields", [])
    for requirement in requirements:
        if requirement["kind"] != "evidence":
            continue
        spec = requirement["spec"]
        entity = spec.get("entity") or (slots[0].get("entity") if slots else "task")
        requested = {"entity": entity, "variant": spec["variant"], "field": spec["field"]}
        if requested not in slots:
            slots.append(requested)
        if spec["field"] not in fields:
            fields.append(spec["field"])
    state["active_phase_contract"] = {
        "version": version,
        "objective": objective,
        "allowed_operations": list(dict.fromkeys(operations)),
        "targets": target_bindings,
        "bindings": bindings,
        "condition_ids": new_conditions,
        "status": "in_progress",
        "started_at": time.time(),
    }


def observe_conditions(state: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    """Evaluate current-page facts and durable extraction evidence separately."""
    phase = state.get("active_phase_contract") or {}
    controls = observation.get("controls") or []
    capture = observation.get("capture_id")
    if capture and capture == state.get("phase_invalidated_capture"):
        capture = None
    elif capture:
        state.pop("phase_invalidated_capture", None)
    if capture and capture != state.get("phase_last_capture"):
        state["phase_observation_sequence"] = int(state.get("phase_observation_sequence", 0)) + 1
        state["phase_last_capture"] = capture
    url = observation.get("url") or ""
    current_ids = set(phase.get("condition_ids", []))
    for item in state.get("phase_requirements", []):
        # Completed historical phases retain evidence from that phase's page.
        if item["id"] not in current_ids and item.get("status") == "satisfied":
            continue
        spec, kind = item["spec"], item["kind"]
        verdict = None
        if kind.startswith("control_"):
            matches = [c for c in controls if same_node(spec["target"], c)]
            documents = {node_identity(c)["document"] for c in controls if node_identity(c)["document"]}
            if capture and documents and spec["target"].get("document") not in documents:
                # A new document retires the binding, not the user requirement.
                # Keep previously observed proof; expiry itself is not success.
                if item.get("status") != "satisfied":
                    item["status"] = "expired"
                continue
            if len(matches) == 1:
                details = matches[0].get("decision_state") or {}
                verdict = (
                    details.get("current_value") == spec["value"]
                    if kind == "control_value"
                    else matches[0].get("selected") is True or details.get("checked") is True
                )
        elif kind == "url" and url:
            verdict = url == spec["value"]
        elif kind == "url_query" and url:
            verdict = parse_qs(urlsplit(url).query, keep_blank_values=True).get(spec["key"], [""]) == [spec["value"]]
        elif kind == "evidence":
            # These slots are written by runtime extraction, not tool arguments.
            slots = state.get("evidence_slots") or []
            matching = [
                slot
                for slot in slots
                if all(str(slot.get(key, "")) == str(spec[key]) for key in ("field", "variant") if key in spec)
                and slot.get("query_id") == str(state.get("query_id") or state.get("task_id") or "")
                and (not spec.get("source") or spec["source"] == slot.get("source"))
                and (
                    not spec.get("query")
                    or spec["query"]
                    in [
                        value
                        for key, values in parse_qs(urlsplit(str(slot.get("source", ""))).query).items()
                        if key in {"q", "wd", "query", "keyword", "search_query"}
                        for value in values
                    ]
                )
                and (not spec.get("entity") or slot.get("entity") == spec["entity"])
                and slot.get("source")
                and slot.get("value") not in (None, "", [], {})
                and slot.get("status") not in {"missing", "unknown"}
                and slot.get("observation_status") != "not_observed"
            ]
            verdict = bool(matching)
            if matching:
                item["evidence_ref"] = {
                    "source": matching[-1]["source"],
                    "variant": spec["variant"],
                    "query": spec.get("query"),
                }
        elif kind == "cart_delta":
            continue  # Needs the bounded reader, not arbitrary body text.
        item["status"] = "unknown" if verdict is None or not capture else "satisfied" if verdict else "unsatisfied"
        if capture:
            if item.get("observed_capture") != capture:
                item["observed_at"] = time.time()
                item["observed_sequence"] = state["phase_observation_sequence"]
                item["observed_capture"] = capture
            if kind != "evidence":
                item["evidence_ref"] = {"capture_id": capture, "url": url}
    active = [item for item in state.get("phase_requirements", []) if item["id"] in current_ids]
    phase["status"] = "verified" if active and all(c["status"] == "satisfied" for c in active) else "in_progress"
    return project_phase(state)


def constrain_actions(menu: Any, state: dict[str, Any], controls: list[dict[str, Any]]) -> None:
    """Narrow the menu in place; identifiers are never renumbered after filtering."""
    phase = state.get("active_phase_contract") or {}
    if not phase:
        return
    current = {c.get("target_id"): c for c in controls}
    selected_targets = [
        (item["spec"]["target"], item["kind"])
        for item in state.get("phase_requirements", [])
        if item["id"] in phase["condition_ids"]
        and item["status"] == "satisfied"
        and item["kind"] in {"control_value", "control_selected"}
    ]
    sort_applied = any(
        item["id"] in phase["condition_ids"]
        and item["status"] == "satisfied"
        and item["kind"] == "url_query"
        and item["spec"]["key"] in {"order", "sort", "sortBy"}
        for item in state.get("phase_requirements", [])
    )
    for key, step in list(menu.steps.items()):
        control = current.get(step.get("target_id"))
        # Satisfied optional conditions do not finish the intent. For example,
        # a confirmed fill still needs its subsequent search submission.
        # Local intent constrains mutations; fixed observations can establish
        # whether it is satisfied, like the existing VERIFY operation.
        allowed = step["op"] in phase["allowed_operations"] or step["op"] in {
            "probe_cards", "probe_interactives", "read_text", "find", "snapshot", "tabs", "wait"
        }
        if control:
            if phase["targets"]:
                allowed = allowed and any(same_node(binding, control) for binding in phase["targets"])
            if any(
                same_node(target, control) and (kind == "control_selected" or step["op"] in {"fill", "select_option"})
                for target, kind in selected_targets
            ):
                allowed = False
            if sort_applied and (
                str(control.get("kind", "")).startswith("sort")
                or re.search(
                    r"排序|综合|最新|销量|sort|newest|relevance|price",
                    str(control.get("name", "")),
                    re.I,
                )
            ):
                allowed = False
        if not allowed:
            menu.steps.pop(key)
            menu.criteria.pop(key, None)
            menu.excluded["phase_contract"] = menu.excluded.get("phase_contract", 0) + 1


class BrowserPhaseTool(Tool):
    accepts_tool_callback_context = True

    def __init__(self, runtime: Any):
        super().__init__(
            ToolCard(
                name="browser_phase",
                description=(
                    "Optional local intent update or explicit fresh verification. Ordinary browser actions do not "
                    "require this tool. Set needs only objective; allowed_operations, observed target_ids, "
                    "bound_values [{target_id,value}] and conditions are optional. For complex work prepare one "
                    "current executable fragment with resolved parameters and known completion conditions; "
                    "update only when intent/bindings change, not before each action. Example: "
                    '{"op":"set","objective":"Search the selected query and read its results"}. '
                    "Use target_ids from browser_state.phase_bindings, not native AX refs. "
                    'Example condition: {"kind":"url_query","key":"order","value":"pubdate"}. '
                    'Example verify: {"op":"verify"}. To discover cart reader selectors without arbitrary scripts: '
                    '{"op":"verify","inspect_cart":true}; hints do not establish a baseline. '
                    "Each condition requires kind: control_value {kind,target_id,value}, "
                    "control_selected {kind,target_id}, url_query {kind,key,value}, url {kind,value}, "
                    "evidence {kind,field,variant,query OR source,entity?}, cart_delta {kind,items_selector,"
                    "sku_attribute,quantity_selector,count_selector,deltas:{sku:integer},preserve_existing:true}. "
                    "Cart baseline needs stable SKU+variant and unique numeric distinct-line count BEFORE a "
                    "cart mutation. Local search fills are allowed before baseline. preserve_existing:false "
                    "checks only target SKUs when the user does not require whole-cart preservation. "
                    "Normal observations auto-verify available facts. Verify requests fresh evidence and may "
                    "omit phase_version. Set replaces temporary bindings, retaining user evidence requirements, "
                    "cart baselines and unresolved effects. A cart_delta may use requirement_id to rebind its reader "
                    "without changing deltas/preservation or resetting baseline. Never submit success/proof."
                ),
                input_params=copy.deepcopy(PHASE_SCHEMA),
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        started = time.perf_counter()
        reading = False
        cart_hints = None
        session = kwargs.get("session")
        state = task_state(session)
        try:
            validate_request(inputs)
            if not state.get("goal"):
                raise ValueError("phase_requires_active_task")
            deadline = float(state.get("deadline_at") or time.time() + 15)
            remaining = min(15, deadline - time.time(), float(state.get("invocation_remaining_s", 15)))
            if remaining <= 0:
                raise ValueError("browser_task_deadline")
            page = self._runtime._ensure_page_state()
            observation = page.export_decision_observation()
            op = inputs.get("op")
            callback = getattr(kwargs.get("_tool_callback_context"), "inputs", None)
            call_id = str(getattr(getattr(callback, "tool_call", None), "id", ""))
            if op == "set":
                if call_id.startswith("jev_"):
                    raise ValueError("jev_cannot_plan_or_certify")
                set_phase(state, inputs, observation.get("controls", []))
                # Persist the accepted contract before an external read can fail.
                save(session, state)
            elif (
                op != "verify"
                or set(inputs) - {"op", "phase_version", "inspect_cart"}
                or (
                    "phase_version" in inputs
                    and inputs["phase_version"] != (state.get("active_phase_contract") or {}).get("version")
                )
            ):
                raise ValueError("stale_or_invalid_phase_request")
            if call_id.startswith("jev_"):
                policy = getattr(self._runtime, "decision_policy", None)
                if policy is None:
                    raise ValueError("browser_policy_unavailable_at_execution")
                await policy.validate_tool_call(callback, session, actual_arguments=inputs)
            import asyncio

            async with asyncio.timeout(min(remaining, deadline - time.time())):
                if inputs.get("inspect_cart"):
                    reading = True
                    cart_hints = await inspect_cart(self._runtime)
                if op == "verify":
                    reading = True
                    captured = await self._runtime.capture_reconciliation_browser_state(
                        action_group_id=call_id or "phase-verification", include_decision=True
                    )
                    if not captured.get("ok") or not (captured.get("decision_observation") or {}).get("capture_id"):
                        raise ValueError("phase_verification_observation_unavailable")
                    observation = captured["decision_observation"]
                for condition in state.get("phase_requirements", []):
                    if op == "set" and condition.get("baseline") and not condition.get("reason"):
                        continue
                    if condition["kind"] == "cart_delta" and (
                        condition["status"] != "satisfied"
                        or condition["id"] in (state.get("active_phase_contract") or {}).get("condition_ids", [])
                    ):
                        reading = True
                        await read_cart(self._runtime, condition, state, baseline=op == "set")
            from .execution_journal import reconcile_observation

            observe_conditions(state, observation)
            observe_acceptance(state, observation)
            reconcile_observation(state, observation)
            report = project_phase(state)
            save(session, state)
            browser_agent_log_info(
                "[BROWSER_PHASE] %s",
                json.dumps(
                    {
                        "task_id": state.get("task_id"),
                        "phase_version": report["version"],
                        "operation": op,
                        "status": report["status"],
                        "missing_count": len(report["missing_conditions"]),
                        "unknown_writes": len(report["unknown_writes"]),
                    }
                ),
            )
            return ToolOutput(
                success=True,
                data={
                    "ok": True,
                    "executed": True,
                    "state_changed": False,
                    "phase": report,
                    "bindings": binding_targets(observation.get("controls", [])),
                    **({"cart_reader_hints": cart_hints} if cart_hints is not None else {}),
                    "observation_updated": reading,
                },
            )
        except Exception as exc:
            if reading:
                state["phase_invalidated_capture"] = (page.decision_snapshot or {}).get("capture_id")
                observe_conditions(state, {})
            save(session, state)
            return ToolOutput(
                success=False,
                error=str(exc),
                data={
                    "ok": False,
                    "executed": None if reading else False,
                    "execution_state": "dispatched_unknown" if reading else "rejected_before_dispatch",
                    "state_changed": False,
                    "error": str(exc),
                    "validation": exc.details if isinstance(exc, PhaseInputError) else {},
                    "phase": project_phase(state),
                },
            )
        finally:
            browser_agent_log_info(
                "[BROWSER_TIMING] %s",
                json.dumps(
                    {
                        "component": "phase_verification",
                        "task_id": state.get("task_id"),
                        "phase_version": (state.get("active_phase_contract") or {}).get("version", 0),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                ),
            )

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)


async def observe_runtime(runtime: Any, session: Any, captured: dict[str, Any]) -> None:
    """Shared lifecycle hook, independent of whether a decision model is installed."""
    from .cart_verification import observe_cart
    from .execution_journal import reconcile_observation

    state = task_state(session)
    if not state.get("goal"):
        return
    observation = captured.get("decision_observation") or {}
    if not captured.get("ok") or not observation.get("capture_id"):
        return  # No new proof; preserve prior evidence rather than inventing it.
    observe_conditions(state, observation)
    observe_acceptance(state, observation)
    reconcile_observation(state, observation)
    await observe_cart(runtime, state, observation)
    observe_conditions(state, observation)
    save(session, state)
