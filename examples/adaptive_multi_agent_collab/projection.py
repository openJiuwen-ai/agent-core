"""Offline spending scenarios; ceilings are not completion guarantees."""

from __future__ import annotations

import math
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .config import Config


def spending_projection(
    specification: Mapping[str, Any],
    pilot_report: Mapping[str, Any] | None = None,
    *,
    compatibility_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bound scheduled workflow counts and price explicitly hypothetical usage.

    Scenarios assume paid rates equal frozen reference tariffs, no discounts or
    fees, and equal per-agent workload. They are neither live billing estimates
    nor changes to the scientific schedule. The pilot-derived forecast remains
    unavailable until complete paid per-query usage is supplied.
    """
    from .prompts import PROTOCOL_ID, MESSAGE_SERIALIZATION_ID, INITIAL_TEMPLATE_ID, ROLE_PROTOCOL_ID
    from .output_contract import ROLE_OUTPUT_PROTOCOL, ROLE_SCHEMA_ID, SCHEMA_COUNTING_ID
    from .repair import REPAIR_DIAGNOSTIC_ID

    config = Config.from_dict(specification.get("config", {}))
    train = config.training
    active = specification.get("active_data", {})
    _, component_queries, system_queries, _ = active["counts"]

    # Incoming, every interval, and a changed partial final checkpoint. Ceil
    # covers the last partial interval, not an extra duplicate final validation.
    def validations(phase: Any) -> int:
        return 0 if phase.max_updates == 0 else 1 + math.ceil(phase.max_updates / phase.validation_interval)

    bootstrap = train.weighting_bootstrap.max_attempts + train.routing_bootstrap.max_updates
    bootstrap_validation = component_queries * (
        validations(train.weighting_bootstrap) + validations(train.routing_bootstrap)
    )
    regular_training = train.weighting_phase.max_attempts + train.routing_phase.max_updates
    regular_validation = component_queries * (validations(train.weighting_phase) + validations(train.routing_phase))
    prompt_iterations = train.prompt_phase.max_iterations
    prompt_training = train.prompt_train_batch_size * prompt_iterations
    prompt_validation = component_queries * (1 + prompt_iterations) if prompt_iterations else 0
    cycles = train.max_outer_cycles
    workflow_counts = {
        "pilot": 8,
        "development": bootstrap
        + bootstrap_validation
        + system_queries
        + cycles * (regular_training + regular_validation + prompt_training + prompt_validation + system_queries),
        "test": 100 * 3,
    }
    backends = specification.get("backends", {})
    rates = []
    for agent in config.agent_pool:
        profile = backends.get(agent.backend_key, {}).get("resources", {})
        if profile.get("reference_input_price") is None or profile.get("reference_output_price") is None:
            raise ValueError("projection requires every collaboration backend's frozen reference tariff")
        rates.append((float(profile["reference_input_price"]), float(profile["reference_output_price"])))
    mean_input = sum(x[0] for x in rates) / len(rates)
    mean_output = sum(x[1] for x in rates) / len(rates)
    textgrad = specification.get("textgrad", {})
    tg_backend = backends.get(textgrad.get("backward_backend"), {}).get("resources", {})
    if not tg_backend:
        raise ValueError("projection requires the declared TextGrad backend resource profile")
    tg_input, tg_output = tg_backend["reference_input_price"], tg_backend["reference_output_price"]
    tg_calls = cycles * prompt_iterations * 5  # two evaluations, two backward calls, one proposal
    scenarios = []
    for label, sessions, edges, rounds, input_tokens, output_tokens, tie_calls, retry_fraction in (
        ("illustrative_short", 2, 4, 1, 800, 128, 0.2, 0.0),
        ("illustrative_long", 3, 6, 2, 1400, 200, 1.0, 0.1),
    ):
        per_query_calls = (config.n_agents + sessions * edges * rounds * 2 + tie_calls) * (1 + retry_fraction)
        per_query = per_query_calls * (input_tokens * mean_input + output_tokens * mean_output)
        tg_cost = tg_calls * (6000 * tg_input + 512 * tg_output) * (1 + retry_fraction)
        costs = {stage: count * per_query for stage, count in workflow_counts.items()}
        # Pilot always has three sessions; shorten neither its horizon nor schedule.
        pilot_calls = (config.n_agents + 3 * edges * rounds * 2 + tie_calls) * (1 + retry_fraction)
        costs["pilot"] = 8 * pilot_calls * (input_tokens * mean_input + output_tokens * mean_output)
        costs["development"] += tg_cost
        scenarios.append(
            {
                "name": label,
                "assumptions": {
                    "mean_sessions_outside_pilot": sessions,
                    "pilot_sessions": 3,
                    "mean_active_edges": edges,
                    "mean_rounds": rounds,
                    "calls_per_conversation_round": 2,
                    "mean_input_tokens": input_tokens,
                    "mean_output_tokens": output_tokens,
                    "mean_tie_calls_per_query": tie_calls,
                    "extra_attempt_fraction": retry_fraction,
                    "textgrad_input_tokens": 6000,
                    "textgrad_output_tokens": 512,
                },
                "projected_api_usd": costs,
                "textgrad_development_usd": tg_cost,
                "within_registered_ceiling": {
                    stage: cost <= getattr(config.resources, f"{stage}_budget") for stage, cost in costs.items()
                },
            }
        )
    max_rate_in, max_rate_out = max(x[0] for x in rates), max(x[1] for x in rates)
    physical_factor = (1 + config.safety.parse_retries) * (1 + config.resources.transport_retries)
    outgoing, incoming = config.capacity.resolve(config.n_agents)
    max_edges = min(sum(outgoing), sum(incoming))
    cap_calls_per_session = (2 * max_edges * config.safety.max_internal_rounds + config.n_agents) * physical_factor
    # Deliberately overbounds all session call types by the largest input cap.
    session_call_cost = (
        max(config.resources.conversation_input, config.resources.judge_input, config.resources.tie_input) * max_rate_in
        + config.resources.generation_output * max_rate_out
    )
    init_cost = (
        config.n_agents
        * physical_factor
        * (config.resources.initial_input * max_rate_in + config.resources.generation_output * max_rate_out)
    )
    query_bound = init_cost + 3 * cap_calls_per_session * session_call_cost
    tg_bound = (
        tg_calls
        * (1 + config.resources.transport_retries)
        * (config.resources.textgrad_input * tg_input + config.resources.textgrad_output * tg_output)
    )
    result: dict[str, Any] = {
        "protocol": "reference_tariff_scenarios_revised_workflow_v2",
        "workflow_input_protocol": {
            "fixed_protocol": PROTOCOL_ID,
            "role_protocol": ROLE_PROTOCOL_ID,
            "role_output_protocol": ROLE_OUTPUT_PROTOCOL,
            "role_schema": ROLE_SCHEMA_ID,
            "schema_counting": SCHEMA_COUNTING_ID,
            "message_serialization": MESSAGE_SERIALIZATION_ID,
            "initial_template": INITIAL_TEMPLATE_ID,
            "repair_diagnostic": REPAIR_DIAGNOSTIC_ID,
            "prompt_collections": [agent.prompt_collection_version for agent in config.agent_pool],
        },
        "currency": "USD",
        "billing_assumption": "paid equals frozen reference, no discounts or fees",
        "workflow_attempt_upper_counts": workflow_counts,
        "workflow_count_breakdown": {
            "bootstrap_training": bootstrap,
            "bootstrap_validation": bootstrap_validation,
            "shared_prefix_calibration": system_queries,
            "checkpoint_zero_new_workflows": 0,
            "per_cycle_training": regular_training,
            "per_cycle_numerical_validation": regular_validation,
            "per_cycle_prompt_training": prompt_training,
            "per_cycle_prompt_validation": prompt_validation,
            "per_cycle_system_validation": system_queries,
            "cycles": cycles,
        },
        "textgrad_max_logical_calls": tg_calls,
        "illustrative_scenarios": scenarios,
        "loose_reference_priced_call_cap_bound_usd": {
            stage: count * query_bound + (tg_bound if stage == "development" else 0)
            for stage, count in workflow_counts.items()
        },
        "registered_api_ceilings_usd": {
            stage: getattr(config.resources, f"{stage}_budget") for stage in ("compatibility", *workflow_counts)
        },
        "compatibility_checks": {
            "budget_usd": config.resources.compatibility_budget,
            "physical_generation_attempts_max": config.resources.compatibility_max_attempts,
            "separate_from_pilot": True,
        },
        "pilot_paid_projection": pilot_paid_projection(specification, compatibility_report=compatibility_report),
        "completion_guaranteed": False,
        "account_funding_fees_taxes_fx": "record separately from inference; excluded from these scenarios",
        "pilot_forecast": {"available": False, "reason": "no authorized measured pilot supplied"},
    }
    if pilot_report is not None:
        # No invented mean from partial, zero-filled or unmeasured billing.
        usages = pilot_report.get("paid_query_costs_usd")
        limitations = pilot_report.get("paid_query_cost_limitations", {})
        if (
            isinstance(usages, list)
            and len(usages) == 8
            and limitations.get("failed_or_censored_query_ids") == []
            and limitations.get("unresolved_paid_query_ids") == []
            and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0 for x in usages
            )
        ):
            mean = sum(usages) / len(usages)
            result["pilot_forecast"] = {
                "available": True,
                "mean_pilot_paid_query_usd": mean,
                "development_workflows_at_pilot_mean_usd": workflow_counts["development"] * mean,
                "limitations": "eight initialized training queries; learned routing/prompt lengths may differ; add TextGrad/compatibility and billing headroom",
                "requires_development_review": True,
            }
        else:
            result["pilot_forecast"] = {
                "available": False,
                "reason": "need all eight measured/derived paid query costs; unresolved/censored data cannot be zero-filled",
            }
    return result


def pilot_paid_projection(
    specification: Mapping[str, Any], *, compatibility_report: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Price the unchanged eight-query/three-session pilot by actual tariff profile.

    Ordinary workload assumptions and a pessimistic repair/retry scenario are
    disclosed separately. Neither is an authorization or a guarantee of fitting
    the ceiling; compatibility spend is excluded from the independent pilot.
    """
    config = Config.from_dict(specification["config"])
    attestations = specification.get("attestations", {})
    summary = attestations.get("compatibility", {})
    # Execution artifacts use this exact renderer. A differently formatted or
    # changed report cannot inherit the raw-file attestation digest silently.
    report_digest = (
        None
        if compatibility_report is None
        else hashlib.sha256((json.dumps(compatibility_report, indent=2, allow_nan=False) + "\n").encode()).hexdigest()
    )
    report_matches = bool(
        compatibility_report is not None
        and compatibility_report.get("status") == "complete"
        and compatibility_report.get("assessment", {}).get("passed") is True
        and summary.get("state") == "validated"
        and summary.get("report_sha256") == report_digest
        and summary.get("plan_fingerprint") == compatibility_report.get("plan", {}).get("fingerprint")
    )
    records = [] if compatibility_report is None else compatibility_report.get("requests", [])
    observed_modes: dict[int, set[str]] = {}
    for request in records:
        agent = request.get("probe", {}).get("agent_id")
        if not isinstance(agent, int):
            continue
        modes = {item.get("observed_billing_mode") for item in request.get("assessment_receipts", ())}
        if len(modes) == 1 and next(iter(modes)) in {"credits", "byok"}:
            observed_modes.setdefault(agent, set()).update(modes)
    scenarios = []
    for name, per_agent_calls, inputs, output_tokens, repair_fraction, retry_fraction in (
        (
            "ordinary_workload_assumption",
            {"initial": 1, "conversation": 6, "judge": 0.75, "tie": 0.3},
            {"initial": 800, "conversation": 1200, "judge": 2400, "tie": 4800},
            128,
            0.05,
            0.02,
        ),
        (
            "pessimistic_workload_with_full_repairs_and_retries",
            {"initial": 1, "conversation": 24, "judge": 3, "tie": 3},
            {
                "initial": config.resources.initial_input,
                "conversation": config.resources.conversation_input,
                "judge": config.resources.judge_input,
                "tie": config.resources.tie_input,
            },
            config.resources.generation_output,
            config.safety.parse_retries,
            config.resources.transport_retries,
        ),
    ):
        rows = []
        maximum_reservation = 0.0
        for agent in config.agent_pool:
            backend = specification["backends"][agent.backend_key]
            profile = backend["resources"]
            in_rate, out_rate = profile.get("input_price"), profile.get("output_price")
            if any(value is None or not math.isfinite(value) or value < 0 for value in (in_rate, out_rate)):
                raise ValueError("paid pilot projection requires current per-backend paid tariffs")
            multiplier, fee = profile.get("billing_fee_multiplier", 1.0), profile.get("request_fee", 0.0)
            factor = (1 + repair_fraction) * (1 + retry_fraction)
            parts = {
                kind: {
                    "physical_attempts": 8 * calls * factor,
                    "paid_tariff_usd": 8
                    * calls
                    * factor
                    * ((inputs[kind] * in_rate + output_tokens * out_rate) * multiplier + fee),
                }
                for kind, calls in per_agent_calls.items()
            }
            mode = profile.get("billing_mode", "unknown")
            if agent.agent_id in observed_modes and len(observed_modes[agent.agent_id]) == 1:
                mode = next(iter(observed_modes[agent.agent_id]))
            billing = attestations.get("backends", {}).get(agent.backend_key, {}).get("billing", {})
            actual_assessment = (
                (compatibility_report or {})
                .get("assessment", {})
                .get("backends", {})
                .get(str(agent.agent_id), {})
                .get("billing", {})
            )
            documentation = backend.get("billing_verification", {})
            documented_tariff = bool(
                documentation.get("catalog_fetched_at_utc")
                and documentation.get("catalog_sha256")
                and documentation.get("evidence")
                and profile.get("tariff_revision") == documentation["catalog_fetched_at_utc"]
            )
            billing_verified = bool(
                report_matches
                and mode == "credits"
                and documented_tariff
                and billing.get("state") == "validated"
                and billing.get("report_sha256") == report_digest
                and billing.get("details") == actual_assessment
                and actual_assessment.get("status") == "validated"
                and billing.get("observed_billing_modes") == ["credits"]
            )
            # Mirror ResourceController's physical reservation formula, then
            # allow every concurrent request to hit the most expensive backend.
            input_cap = max(
                config.resources.initial_input,
                config.resources.conversation_input,
                config.resources.judge_input,
                config.resources.tie_input,
            )
            output_cap = (
                profile.get("reasoning_allowance")
                if profile.get("reasoning_in_output_budget")
                else config.resources.generation_output
            )
            request_reservation = (
                (
                    input_cap
                    * max(in_rate, profile.get("cached_input_price") or 0, profile.get("cache_write_price") or 0)
                    + output_cap * max(out_rate, profile.get("reasoning_price") or 0)
                )
                * multiplier
                + fee
            ) * profile.get("reservation_safety_factor", 1.0)
            maximum_reservation = max(maximum_reservation, request_reservation)
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "backend_key": agent.backend_key,
                    "model": agent.model_name,
                    "billing_mode": mode,
                    "billing_verified": billing_verified,
                    "documented_tariff": documented_tariff,
                    "compatibility_report_sha256": report_digest if billing_verified else None,
                    "tariff_revision": profile.get("tariff_revision"),
                    "input_price_per_token": in_rate,
                    "output_price_per_token": out_rate,
                    "billing_fee_multiplier": multiplier,
                    "request_fee": fee,
                    "maximum_physical_request_reservation_usd": request_reservation,
                    "components": parts,
                    "total_paid_tariff_usd": sum(item["paid_tariff_usd"] for item in parts.values()),
                }
            )
        total = sum(row["total_paid_tariff_usd"] for row in rows)
        reservation_headroom = maximum_reservation * config.safety.max_concurrent_model_calls
        # Workload uncertainty is separate from outstanding reservation capacity.
        headroom = 0.25 * total + reservation_headroom
        scenarios.append(
            {
                "name": name,
                "per_agent_logical_calls_per_query": per_agent_calls,
                "mean_input_tokens_per_generation": inputs,
                "mean_output_tokens_per_generation": output_tokens,
                "schema_repair_fraction": repair_fraction,
                "transport_retry_fraction": retry_fraction,
                "by_backend": rows,
                "total_paid_tariff_usd": total,
                "proposed_headroom_usd": headroom,
                "total_with_headroom_usd": total + headroom,
                "headroom_rule": "25_percent_scenario_cost_plus_maximum_physical_reservation_times_concurrency",
                "maximum_concurrent_physical_reservations": config.safety.max_concurrent_model_calls,
                "outstanding_reservation_headroom_usd": reservation_headroom,
                "fits_pilot_ceiling_under_stated_tariff_assumptions": total + headroom
                <= (config.resources.pilot_budget or 0),
                "billing_verified": all(row["billing_verified"] for row in rows),
            }
        )
    return {
        "protocol": "eight_query_three_session_revised_workflow_scenarios_v2",
        "queries": 8,
        "sessions_per_query": 3,
        "initial_answer_generations_without_retries": 8 * config.n_agents,
        "compatibility_spend_included": False,
        "pilot_ceiling_usd": config.resources.pilot_budget,
        "reference_pricing_used": False,
        "scenarios": scenarios,
        "completion_guaranteed": False,
        "requires_separate_pilot_authorization": True,
        "limitations": [
            "workload assumptions are not measurements or convergence predictions",
            "BYOK upstream provider charges cannot be inferred from OpenRouter account charges alone",
            "unverified billing mode/tariffs do not establish that the USD5 pilot is affordable",
            "account-funding fees, taxes and currency conversion are recorded separately",
        ],
    }
