"""Freeze independent training-side captured-state probes without model I/O.

Historical responses are fixed input evidence, never a trajectory under the new
condition. Each probe stands alone: no later request depends on a live reply.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .experiment import PreparedExperiment, planning_hooks, prepare_experiment
from .gateway import OpenRouterBinding, preview_request_body
from .output_contract import response_format_for
from .prompts import compose_backend_input, protocol_instructions
from .randomness import GenerationContext, LogicalCallIdentity
from .repair import REPAIR_DIAGNOSTICS, REPAIR_DIAGNOSTIC_ID, repair_variants, render_repair
from .replay import render_recorded_request, repetition_replay
from .resources import BackendResourceProfile, ExpenditureLedger, LocalTokenCounter, ResourceController
from .schemas import AgentCallKind, AgentRequest


PROBE_PLAN_ID = "captured_training_state_six_probes_v1"
ROLE_PROBE_PLAN_ID = "role_contract_six_probes_v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _records(pilot: Mapping[str, Any], training_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    for observation_index, observation in enumerate(pilot["observations"]):
        query_id = observation["example_id"]
        if query_id not in training_ids:
            raise ValueError("captured-state evidence must contain registered active-training queries only")
        for record_index, record in enumerate(observation["call_records"]):
            result.append(
                {
                    **record,
                    "example_id": query_id,
                    "observation_index": observation_index,
                    "record_index": record_index,
                    "source_record_sha256": _digest(record),
                }
            )
    return result


def _select_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use artifact order, with a self-reflection pair before its replayed stop."""

    def first(agent: int, component: str, predicate: Any = None) -> dict[str, Any]:
        for record in records:
            identity = record["logical_call"]
            if identity["agent_id"] == agent and record["component"] == component and not identity["repair_attempt"]:
                if predicate is None or predicate(record):
                    return record
        raise ValueError(f"no unambiguous captured training input for agent {agent} {component}")

    triggers = [item for item in repetition_replay(records)["triggers"] if item["tag"].endswith(":1->1:S_self")]
    if not triggers:
        raise ValueError("self-reflection probe requires a recorded repetition case")
    trigger = triggers[0]

    def within_repetition(record: Mapping[str, Any]) -> bool:
        identity = record["logical_call"]
        return (
            record["example_id"] == trigger["example_id"]
            and identity["conversation_id"] == trigger["tag"]
            and 1 < identity["round_index"] < trigger["round"]
        )

    revision = first(2, "revision:S_D")
    return [
        first(0, "tie"),
        first(0, "role:S_D"),
        revision,
        first(1, "role:S_self", within_repetition),
        first(
            1,
            "revision:S_self",
            lambda row: within_repetition(row) and row["logical_call"]["round_index"] > 2,
        ),
        revision,
    ]


def _controller(prepared: PreparedExperiment, agent_id: int) -> ResourceController:
    backend_key = prepared.config.agent_pool[agent_id].backend_key
    profile = BackendResourceProfile.from_dict(prepared.backends[backend_key]["resources"])
    return ResourceController(
        profile,
        LocalTokenCounter(profile),
        ExpenditureLedger(prepared.config.resources),
        limits=prepared.config.resources,
    )


def _freeze_record(
    prepared: PreparedExperiment, record: Mapping[str, Any], order: int, source: Mapping[str, str]
) -> dict[str, Any]:
    base, fields = render_recorded_request(dict(record))
    source_identity = record["logical_call"]
    agent_id, kind = source_identity["agent_id"], source_identity["call_type"]
    agent = prepared.config.agent_pool[agent_id]
    backend = prepared.backends[agent.backend_key]
    probe_id = f"probe-{order}"
    deliberate_repair = order == 6
    diagnostic = REPAIR_DIAGNOSTICS["truncation"] if deliberate_repair else None
    body = render_repair(base, diagnostic) if diagnostic else base
    repair_attempt = int(deliberate_repair)
    generation = GenerationContext(
        prepared.config.seed, "compatibility", f"captured-state:{probe_id}:{record['example_id']}"
    )
    # message=None deliberately keeps execution identity separate from the
    # original workflow tag; the historical tag remains immutable input data.
    logical = LogicalCallIdentity(
        agent_id, source_identity["session_id"], kind, kind, source_identity["round_index"], repair_attempt
    )
    context = {
        **fields,
        "example_id": record["example_id"],
        "session": logical.session_id,
        "round": logical.round_index,
        "repair_attempt": repair_attempt,
        "repair_base_prompt": base,
        "repair_enabled": prepared.config.safety.parse_retries > 0,
        "generation_seed": generation.seed_for(logical),
        "logical_call": generation.descriptor(logical),
    }
    instructions = "\n".join(value for value in (agent.system_configuration, agent.role_configuration) if value)
    protocol = protocol_instructions(record["component"], context)
    context.update(agent_instructions=instructions, protocol_instructions=protocol)
    effective = compose_backend_input(instructions, protocol, body)
    controller = _controller(prepared, agent_id)
    schema = response_format_for(kind, backend.get("role_output"))
    bodies = repair_variants(base, kind=kind)
    variants = [compose_backend_input(instructions, protocol, value) for value in bodies]
    maximum, output_cap = controller.preflight_envelope(variants, kind, schema=schema)
    input_count, _ = controller.preflight(effective, kind, schema=schema)
    reservation = controller.reservation_maximum(input_count, output_cap, kind)
    envelope = [
        {
            "diagnostic_key": (
                None
                if index == 0
                else next(
                    key
                    for key, diagnostic in REPAIR_DIAGNOSTICS.items()
                    if bodies[index] == render_repair(base, diagnostic)
                )
            ),
            "effective_input": value,
            "input_tokens": controller.counter.count_input(value, schema=schema),
            "sha256": _digest(value),
        }
        for index, value in enumerate(variants)
    ]
    source_evidence = {
        "artifact": dict(source),
        "query_id": record["example_id"],
        "partition": "active-training",
        "observation_index": record["observation_index"],
        "call_record_index": record["record_index"],
        "source_record_sha256": record["source_record_sha256"],
        "logical_call": dict(source_identity),
        "source_generation_seed": record.get("seed"),
        "selection_rule": "artifact-order-first-matching-base-request; self-reflection uses first recorded repetition case and a prior role/revision pair",
    }
    return {
        "probe_id": probe_id,
        "order": order,
        "agent_id": agent_id,
        "backend_key": agent.backend_key,
        "model_name": backend["model_name"],
        "call_kind": kind,
        "component": record["component"],
        "prompt": body,
        "base_prompt": base,
        "context": context,
        "source_evidence": source_evidence,
        "repair_attempt": repair_attempt,
        "deliberate_repair_shape": deliberate_repair,
        "diagnostic": diagnostic,
        "diagnostic_id": REPAIR_DIAGNOSTIC_ID,
        "generation_context": asdict(generation),
        "logical_call": asdict(logical),
        "generation_seed": generation.seed_for(logical),
        "wire_seed": generation.seed_for(logical) % (2**31) if backend["supports_seed"] else None,
        "supports_seed": backend["supports_seed"],
        "decoding": copy.deepcopy(backend["decoding"]),
        "effective_input": effective,
        "effective_input_sha256": _digest(effective),
        "response_format": schema,
        "complete_request_sha256": hashlib.sha256(
            json.dumps({"messages": effective, "response_format": schema}, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "admission": {
            "input_tokens": input_count,
            "input_cap": prepared.config.resources.caps(kind)[0],
            "maximum_repair_envelope_tokens": maximum,
            "output_cap": output_cap,
            "counter": asdict(controller.profile),
            "variants": envelope,
            "maximum_physical_reservation_usd": reservation,
        },
    }


def build_probe_plan(specification_path: str | Path, pilot_record_path: str | Path) -> dict[str, Any]:
    """Build exact requests and real offline admission bounds; never dispatch."""
    manifest_path, pilot_path = Path(specification_path).resolve(), Path(pilot_record_path).resolve()
    prepared = prepare_experiment(manifest_path)
    if prepared.active_data_plan is None:
        raise ValueError("captured probes require the registered active data plan")
    pilot = json.loads(pilot_path.read_text())
    records = _records(pilot, set(prepared.active_data_plan.train_ids))
    source = _file(pilot_path)
    rows = [_freeze_record(prepared, record, order, source) for order, record in enumerate(_select_records(records), 1)]
    baseline_path = pilot_path.parent / "ledger_before_pilot.json"
    baseline = json.loads(baseline_path.read_text())
    compatibility_entries = [item for item in baseline["entries"] if item["stage"] == "compatibility"]
    original_compatibility = {
        "artifact": _file(baseline_path),
        "physical_attempts": len(compatibility_entries),
        "known_paid_usd": sum(item["charged"] for item in compatibility_entries if item["charged"] is not None),
        "unresolved_reserved_usd": sum(item["reserved"] for item in compatibility_entries if item["charged"] is None),
        "entries_sha256": _digest(compatibility_entries),
        "meaning": "Immutable original compatibility receipts, separate from the current canonical ledger and its accounting reconciliation.",
    }
    maximum = sum(row["admission"]["maximum_physical_reservation_usd"] for row in rows)
    # Any permitted retry can replace a later probe in the same physical cap.
    arbitrary_six_bound = 6 * max(row["admission"]["maximum_physical_reservation_usd"] for row in rows)
    if arbitrary_six_bound > 0.25:
        raise ValueError("the six-attempt reservation bound exceeds the separately authorized USD0.25 ceiling")
    plan = {
        "protocol": PROBE_PLAN_ID,
        "manifest": _file(manifest_path),
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
        "source_artifact": source,
        "original_compatibility_baseline": original_compatibility,
        "requests": rows,
        "execution": {
            "stage": "compatibility",
            "additional_physical_attempt_ceiling": 6,
            "additional_spending_ceiling_usd": 0.25,
            "cumulative_physical_attempt_ceiling": prepared.config.resources.compatibility_max_attempts,
            "cumulative_spending_ceiling_usd": prepared.config.resources.compatibility_budget,
            "planned_one_attempt_reservations_usd": maximum,
            "any_six_attempt_reservations_usd": arbitrary_six_bound,
            "automatic_repairs_and_retries_consume_same_six_slots": True,
            "pilot_budget_consumed": False,
            "requires_separate_execution_authorization": True,
        },
        "limitations": [
            "Captured incoming messages are independent fixed fixtures, not responses to earlier live probes.",
            "A live complete role reply never becomes continue to enable a revision probe.",
            "The sixth diagnostic is deliberate repair-shaped evidence, not an observed truncation.",
            "A six-attempt cap can leave later planned probes unattempted after repairs or retries.",
            "No learned parameter or accepted prompt is changed; no workflow or accuracy result is produced.",
        ],
    }
    return {**plan, "fingerprint": _digest(plan)}


def request_from_probe(probe: Mapping[str, Any]) -> AgentRequest:
    return AgentRequest(
        AgentCallKind(probe["call_kind"]),
        probe["agent_id"],
        probe["prompt"],
        probe["component"],
        context=copy.deepcopy(probe["context"]),
        max_repair_generations=0,
    )


def validate_probe_plan(plan: Mapping[str, Any], prepared: PreparedExperiment) -> None:
    """Rebuild from original source and require byte-equivalent scientific data."""
    expected_bindings = {
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
    }
    if any(plan.get(key) != value for key, value in expected_bindings.items()):
        raise ValueError("captured probe condition/profile binding changed")
    if plan.get("protocol") == ROLE_PROBE_PLAN_ID:
        current = build_role_probe_plan(
            plan["manifest"]["path"],
            plan["prior_plan"]["path"],
            plan["source_artifact"]["path"],
            plan["synthetic_fixtures"]["path"],
            plan["consumed_compatibility_baseline"]["artifact"]["path"],
        )
    else:
        current = build_probe_plan(plan["manifest"]["path"], plan["source_artifact"]["path"])
    if _digest(plan) != _digest(current):
        raise ValueError("captured probe source, request, admission envelope or fingerprint changed")


def _refreeze_role_probe(prepared: PreparedExperiment, template: Mapping[str, Any], order: int) -> dict[str, Any]:
    """Keep captured task text/seeds intact; replace only current protocol/schema/diagnostics."""
    row = copy.deepcopy(dict(template))
    row.update(probe_id=f"probe-{order}", order=order)
    kind, base = row["call_kind"], row["base_prompt"]
    row["diagnostic_id"] = REPAIR_DIAGNOSTIC_ID
    row["diagnostic"] = REPAIR_DIAGNOSTICS["truncation"] if row["deliberate_repair_shape"] else None
    row["prompt"] = render_repair(base, row["diagnostic"]) if row["diagnostic"] else base
    agent = prepared.config.agent_pool[row["agent_id"]]
    backend = prepared.backends[agent.backend_key]
    context = row["context"]
    instructions = "\n".join(value for value in (agent.system_configuration, agent.role_configuration) if value)
    protocol = protocol_instructions(row["component"], context)
    context.update(agent_instructions=instructions, protocol_instructions=protocol, repair_base_prompt=base)
    schema = response_format_for(kind, backend.get("role_output"))
    effective = compose_backend_input(instructions, protocol, row["prompt"])
    bodies = repair_variants(base, kind=kind)
    variants = [compose_backend_input(instructions, protocol, body) for body in bodies]
    controller = _controller(prepared, row["agent_id"])
    maximum, output_cap = controller.preflight_envelope(variants, kind, schema=schema)
    count, _ = controller.preflight(effective, kind, schema=schema)
    row.update(
        effective_input=effective,
        effective_input_sha256=_digest(effective),
        response_format=schema,
        role_output_profile=copy.deepcopy(backend.get("role_output")),
        complete_request_sha256=hashlib.sha256(
            json.dumps({"messages": effective, "response_format": schema}, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        admission={
            "input_tokens": count,
            "input_cap": prepared.config.resources.caps(kind)[0],
            "maximum_repair_envelope_tokens": maximum,
            "output_cap": output_cap,
            "counter": asdict(controller.profile),
            "variants": [
                {
                    "diagnostic_key": (
                        None
                        if index == 0
                        else next(
                            key
                            for key, diagnostic in REPAIR_DIAGNOSTICS.items()
                            if bodies[index] == render_repair(base, diagnostic)
                        )
                    ),
                    "effective_input": value,
                    "response_format": schema,
                    "input_tokens": controller.counter.count_input(value, schema=schema),
                    "sha256": _digest(value),
                }
                for index, value in enumerate(variants)
            ],
            "maximum_physical_reservation_usd": controller.reservation_maximum(count, output_cap, kind),
        },
    )
    settings: dict[str, Any] = {"max_tokens": output_cap}
    if row["supports_seed"]:
        settings["seed"] = row["generation_seed"]
    if kind == "role":
        settings["workflow_call_kind"] = "role"
    if schema is not None:
        settings["response_format"] = schema
    row["wire_body"] = preview_request_body(
        OpenRouterBinding.from_dict(backend["gateway"]),
        backend["decoding"]["temperature"],
        effective,
        settings,
        role_output=backend.get("role_output"),
    )
    row["wire_body_sha256"] = _digest(row["wire_body"])
    return row


def build_role_probe_plan(
    specification_path: str | Path,
    prior_plan_path: str | Path,
    prior_report_path: str | Path,
    fixtures_path: str | Path,
    ledger_snapshot_path: str | Path,
) -> dict[str, Any]:
    """Freeze the approved six independent role-contract probes, without generation."""
    from .prompts import seed_prompt_collection
    from .schemas import ConversationTag, MessageFormat, Scheme, StructuredMessage

    paths = [
        Path(path).resolve()
        for path in (
            specification_path,
            prior_plan_path,
            prior_report_path,
            fixtures_path,
            ledger_snapshot_path,
        )
    ]
    prepared = prepare_experiment(paths[0])
    old, report, fixtures, baseline = (json.loads(path.read_text()) for path in paths[1:])
    if old.get("fingerprint") != _digest({key: value for key, value in old.items() if key != "fingerprint"}):
        raise ValueError("historical frozen plan fingerprint changed")
    if report.get("plan_fingerprint") != old["fingerprint"] or report.get("pending_probe") is not None:
        raise ValueError("role recheck requires the terminal historical plan/report boundary")
    prior_failure = next(item for item in report["probes"] if item["probe_id"] == "probe-4")
    if prior_failure.get("valid_output") is not False or [
        item["dispatch_id"] for item in prior_failure["physical_entries"]
    ] != [406, 407]:
        raise ValueError("the role fixture must come from the recorded 406/407 schema failure")
    if report.get("unattempted_probe_ids") != ["probe-6"]:
        raise ValueError("the truncation-diagnostic fixture was not the recorded unattempted probe")
    if prepared.active_data_plan is None or not all(
        row["source_evidence"]["query_id"] in prepared.active_data_plan.train_ids for row in old["requests"]
    ):
        raise ValueError("historical probes must retain active-training provenance")
    if fixtures.get("protocol") != "synthetic_role_input_fixtures_v1" or len(fixtures.get("fixtures", [])) != 2:
        raise ValueError("exactly two separately labelled synthetic role fixtures are required")
    templates = [copy.deepcopy(old["requests"][3])]
    templates[0]["source_evidence"].update(
        prior_plan=_file(paths[1]),
        prior_report=_file(paths[2]),
        prior_probe_id="probe-4",
        preserved_base_prompt_sha256=_digest(old["requests"][3]["base_prompt"]),
    )
    for index, fixture in enumerate(fixtures["fixtures"], 2):
        if set(fixture) != {
            "fixture_id",
            "query",
            "incoming_answer",
            "incoming_explanation",
            "previous_feedback",
        }:
            raise ValueError("synthetic runtime fixtures cannot contain gold labels or expected statuses")
        if not all(isinstance(value, str) and value for value in fixture.values()):
            raise ValueError("synthetic fixtures require complete explicit string fields")
        template = copy.deepcopy(old["requests"][3])
        tag = ConversationTag(1, 1, 1, Scheme.SELF)
        incoming = StructuredMessage(
            MessageFormat.REVISION_RESPONSE,
            1,
            1,
            1,
            2,
            tag,
            query=fixture["query"],
            answer=fixture["incoming_answer"],
            explanation=fixture["incoming_explanation"],
            feedback=fixture["previous_feedback"],
        )
        template["base_prompt"] = seed_prompt_collection().render("role:S_self", message_text=incoming.text)
        generation = GenerationContext(prepared.config.seed, "compatibility", fixture["fixture_id"])
        logical = LogicalCallIdentity(1, 1, "role", "role", 3, 0)
        template.update(
            generation_context=asdict(generation),
            logical_call=asdict(logical),
            generation_seed=generation.seed_for(logical),
            wire_seed=generation.seed_for(logical) % (2**31),
            runtime_example={
                "example_id": fixture["fixture_id"],
                "query": fixture["query"],
                "candidates": ["yes", "no"],
            },
            source_evidence={
                "partition": "synthetic",
                "query_id": fixture["fixture_id"],
                "artifact": _file(paths[3]),
                "fixture_index": index - 2,
                "fixture_sha256": _digest(fixture),
                "provenance": "Explicit synthetic arithmetic fixture; independent of all historical responses.",
            },
        )
        template["context"].update(
            example_id=fixture["fixture_id"],
            incoming_answer=fixture["incoming_answer"],
            session=1,
            round=3,
            repair_attempt=0,
            generation_seed=generation.seed_for(logical),
            logical_call=generation.descriptor(logical),
        )
        templates.append(template)
    templates.append(copy.deepcopy(old["requests"][1]))
    pilot_path = Path(old["source_artifact"]["path"])
    records = _records(json.loads(pilot_path.read_text()), set(prepared.active_data_plan.train_ids))
    claude = next(
        row
        for row in records
        if row["logical_call"]["agent_id"] == 2
        and row["logical_call"]["call_type"] == "role"
        and not row["logical_call"]["repair_attempt"]
    )
    templates.append(_freeze_record(prepared, claude, 5, _file(pilot_path)))
    templates.append(copy.deepcopy(old["requests"][5]))
    rows = [_refreeze_role_probe(prepared, template, index) for index, template in enumerate(templates, 1)]
    consumed = [entry for entry in baseline["entries"] if entry["stage"] == "compatibility"]
    if len(consumed) != 24 or any(entry["charged"] is None for entry in consumed):
        raise ValueError("the new recheck must preserve the 24 accounted historical compatibility attempts")
    if (
        prepared.config.resources.compatibility_max_attempts,
        prepared.config.resources.compatibility_budget,
    ) != (48, 3):
        raise ValueError("the role recheck requires the explicitly authorized cumulative 48/USD3 limits")
    plan = {
        "protocol": ROLE_PROBE_PLAN_ID,
        "manifest": _file(paths[0]),
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
        "prior_plan": _file(paths[1]),
        "source_artifact": _file(paths[2]),
        "synthetic_fixtures": _file(paths[3]),
        "original_compatibility_baseline": old["original_compatibility_baseline"],
        "consumed_compatibility_baseline": {
            "artifact": _file(paths[4]),
            "physical_attempts": 24,
            "known_paid_usd": sum(entry["charged"] for entry in consumed),
            "entries_sha256": _digest(consumed),
        },
        "additional_physical_attempts": 24,
        "additional_budget_usd": 0.5,
        "cumulative_compatibility_attempts": 48,
        "cumulative_compatibility_budget_usd": 3.0,
        "requests": rows,
        "execution": {
            "stage": "compatibility",
            "additional_physical_attempt_ceiling": 24,
            "additional_spending_ceiling_usd": 0.5,
            "cumulative_physical_attempt_ceiling": 48,
            "cumulative_spending_ceiling_usd": 3.0,
            "planned_one_attempt_reservations_usd": sum(
                row["admission"]["maximum_physical_reservation_usd"] for row in rows
            ),
            "pilot_budget_consumed": False,
            "repairs_and_transport_retries_consume_same_allowance": True,
        },
        "limitations": [
            "Six independent captured or synthetic states, not continuous conversations.",
            "Semantic expectations remain outside runtime inputs and are assessed manually.",
            "The last request is repair-shaped synthetic evidence, not an observed truncation.",
            "Independent schema-exhausted probes remain failed; fatal accounting/binding/counting events stop dispatch.",
        ],
    }
    return {**plan, "fingerprint": _digest(plan)}


def pilot_forecast(specification_path: str | Path, pilot_record_path: str | Path) -> dict[str, Any]:
    """Keep current hypothetical paid scenarios separate from censored history."""
    from .projection import pilot_paid_projection, spending_projection

    manifest_path, pilot_path = Path(specification_path).resolve(), Path(pilot_record_path).resolve()
    specification = json.loads(manifest_path.read_text())
    pilot = json.loads(pilot_path.read_text())
    training_ids = set(specification["active_data"]["plan"]["train_ids"])
    _records(pilot, training_ids)
    complete = [
        item
        for item in pilot["observations"]
        if item["successful"]
        and item["sessions_completed"] == 3
        and item["usage"].get("paid_monetary_cost") is not None
    ]
    known_paid = sum(item["usage"]["paid_monetary_cost"] for item in complete)
    scenario = spending_projection(specification)
    return {
        "protocol": "captured_pilot_context_forecast_v1",
        "manifest": _file(manifest_path),
        "source_artifact": _file(pilot_path),
        "current_paid_tariff_scenarios": pilot_paid_projection(specification),
        "separate_reference_tariff_scenarios": scenario["illustrative_scenarios"],
        "reference_cap_bound_usd": scenario["loose_reference_priced_call_cap_bound_usd"]["pilot"],
        "historical_context_only": {
            "fully_completed_three_session_queries": len(complete),
            "completed_query_ids": [item["example_id"] for item in complete],
            "known_paid_cost_of_completed_queries_usd": known_paid,
            "mean_completed_query_cost_usd": known_paid / len(complete) if complete else None,
            "eight_times_complete_case_mean_usd": 8 * known_paid / len(complete) if complete else None,
            "completed_sessions_all_observations": sum(item["sessions_completed"] for item in pilot["observations"]),
            "excluded_failed_or_unattempted_query_ids": [
                item["example_id"] for item in pilot["observations"] if item not in complete
            ],
            "selection_bias": "Complete-case cost excludes censored failed queries; it is not an unbiased forecast or a prediction of revised behavior.",
        },
        "completion_guaranteed": False,
        "pilot_authorized": False,
        "limitations": [
            "Revised compact inputs and repeated-feedback stops have no observed full-workflow cost distribution.",
            "Historical missing charges remain unresolved until independently reconciled; they are not zero-filled here.",
            "Original pilot expenditure is preserved; a future pilot must fit the remaining independent pilot ceiling, including unresolved holds.",
            "Scientific reference costs, inference account charges, funding fees and reservation uncertainty remain distinct.",
        ],
    }
