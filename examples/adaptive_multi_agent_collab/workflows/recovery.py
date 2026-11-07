"""One durable whole-workflow replacement, separate from request recovery.

This coordinator never retries an exception or an unfinished execution.  Only
recorded terminal scientific unavailability with explicit eligible provider
evidence may use the second execution.  The caller owns scheduled slots,
sampling cursors, updates and the unchanged execution condition.
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from ..infrastructure.failure_policy import HARD_FAILURES, PROVIDER_FAILURES, FailureKind

WORKFLOW_RECOVERY_PROTOCOL = "whole_workflow_replacement_v1"


@dataclass(frozen=True, slots=True)
class WorkflowRecoveryConfig:
    enabled: bool = False
    max_replacements: int = 1
    version: str = WORKFLOW_RECOVERY_PROTOCOL

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or isinstance(self.max_replacements, bool):
            raise ValueError("workflow recovery requires explicit boolean activation")
        if self.max_replacements != 1 or self.version != WORKFLOW_RECOVERY_PROTOCOL:
            raise ValueError("each scheduled slot permits exactly one replacement allowance")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    usable: bool
    eligible: bool
    reason: str


class WorkflowRecoveryStopped(RuntimeError):
    """A hard stop in either execution must not be hidden by original selection."""

    operational_stop = True

    def __init__(self, trajectory: Any, selected: Any = None) -> None:
        super().__init__(trajectory.error or trajectory.status)
        self.trajectory = trajectory
        self.selected = trajectory if selected is None else selected
        self.budget_exhausted = trajectory.status == "budget-exhausted"


def _metadata(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _metadata(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _metadata(child)


def required_available(trajectory: Any, operation: str) -> bool:
    """Availability is not correctness, informativeness or billing completeness."""
    if operation == "shared-prefix":
        return bool(trajectory.prefixes) and all(required_available(item, "selection") for item in trajectory.prefixes)
    if not trajectory.completed or not trajectory.sessions or trajectory.final_answer is None:
        return False
    if operation == "weighting":
        # Uninformative complete trajectories are usable and still skip Adam.
        return bool(trajectory.complete_labelled)
    if operation == "routing":
        return bool(trajectory.routing_eligible)
    if operation == "prompt":
        return bool(trajectory.complete_labelled)
    if operation in {"selection", "test"}:
        return all(item.normalized_cost is not None for item in trajectory.sessions)
    raise ValueError("unknown workflow recovery operation")


def recovery_decision(trajectory: Any, operation: str, *, stopped: bool = False) -> RecoveryDecision:
    shared = trajectory.shared if operation == "shared-prefix" else trajectory
    if stopped or shared.operational_halt or shared.status in {
        "budget-exhausted", "accounting-failed", "binding-failed", "counting-failed",
        "campaign-halted", "policy-stopped", "interrupted",
    }:
        return RecoveryDecision(False, False, "operational-stop")
    # Classifications are emitted by the resource boundary, not inferred from
    # prose, answer accuracy, or elapsed time. Initialization cost does not
    # create a missing collaboration-cost dependency in a complete workflow.
    records = tuple(shared.call_records)
    hard_kinds = {item.value for item in HARD_FAILURES}
    if any(item.get("failure_kind") in hard_kinds for record in records
           for item in _metadata(record.get("usage", {}).get("raw_metadata", {}))):
        return RecoveryDecision(False, False, "operational-stop")
    if shared.completed and operation in {"selection", "test", "routing", "shared-prefix"}:
        records = tuple(item for item in records if item.get("logical_call", {}).get("call_type") != "initial")
    kinds = {
        item["failure_kind"] for record in records
        for item in _metadata(record.get("usage", {}).get("raw_metadata", {}))
        if item.get("failure_kind") is not None
    }
    if required_available(trajectory, operation):
        return RecoveryDecision(True, False, "required-information-available")
    excluded = {
        FailureKind.REQUEST.value, FailureKind.REFUSAL.value,
        FailureKind.SCHEMA.value, FailureKind.INTERRUPTED.value,
    }
    if kinds & excluded:
        return RecoveryDecision(False, False, "excluded-terminal-failure")
    terminal_calls: dict[str, Mapping[str, Any]] = {}
    for record in records:
        logical = {key: value for key, value in record.get("logical_call", {}).items() if key != "repair_attempt"}
        key = json.dumps(logical, sort_keys=True)
        previous = terminal_calls.get(key)
        if previous is None or record.get("repair_attempt", 0) >= previous.get("repair_attempt", 0):
            terminal_calls[key] = record
    if any(
        record.get("valid_output") is False and record.get("validation_error")
        and (not shared.completed or record.get("usage", {}).get("monetary_cost") is None)
        and not any(item.get("failure_kind") in {kind.value for kind in PROVIDER_FAILURES}
                    for item in _metadata(record.get("usage", {}).get("raw_metadata", {})))
        for record in terminal_calls.values()
    ):
        return RecoveryDecision(False, False, "schema-validation-failure")
    eligible = kinds & ({item.value for item in PROVIDER_FAILURES} | {FailureKind.MISSING.value})
    return RecoveryDecision(False, bool(eligible), "eligible-unavailable-information" if eligible else "unavailable-without-eligible-provider-evidence")


def execution_summary(result: Any, operation: str) -> dict[str, Any]:
    trajectory = result.shared if operation == "shared-prefix" else result
    summary = {
        "example_id": trajectory.example.example_id,
        "completed": trajectory.completed,
        "status": trajectory.status,
        "error": trajectory.error,
        "prediction": trajectory.final_answer,
        "session_count": len(trajectory.sessions),
        "normalized_costs": [item.normalized_cost for item in trajectory.sessions],
        # Full immutable usage/frame evidence remains in call receipts/journal;
        # the slot record references those physical identities without copying
        # their repeated effective inputs into every training checkpoint.
        "usage": {key: value for key, value in asdict(trajectory.usage).items() if key != "raw_metadata"},
        "missing_usage": list(trajectory.usage.missing_coordinates),
        "physical_dispatch_ids": sorted({
            identity for record in trajectory.call_records
            for identity in record.get("physical_dispatch_ids", ())
        }),
        "generation_seeds": [record["seed"] for record in trajectory.call_records if record.get("seed") is not None],
    }
    if operation == "shared-prefix":
        summary["candidate_boundaries"] = [
            {"sessions": len(item.sessions), "prediction": item.final_answer,
             "normalized_costs": [session.normalized_cost for session in item.sessions]}
            for item in result.prefixes
        ]
    return summary


def validate_recovery_records(records: Mapping[str, Any]) -> None:
    if not isinstance(records, Mapping):
        raise ValueError("invalid workflow recovery records")
    identities = []
    for key, slot in records.items():
        if (slot.get("protocol") != WORKFLOW_RECOVERY_PROTOCOL or slot.get("slot_id") != key
                or not slot.get("condition") or slot.get("operation") not in
                {"weighting", "routing", "prompt", "selection", "test", "shared-prefix"}):
            raise ValueError("workflow recovery condition or slot identity changed")
        executions = slot.get("executions")
        if not isinstance(executions, list) or not 1 <= len(executions) <= 2:
            raise ValueError("workflow replacement allowance exceeded")
        for index, execution in enumerate(executions):
            if execution.get("index") != index or execution.get("status") not in {"started", "terminal"}:
                raise ValueError("invalid workflow recovery execution state")
            identity = execution.get("execution_id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("missing workflow execution identity")
            identities.append(identity)
            if execution["status"] == "terminal" and not isinstance(execution.get("decision"), Mapping):
                raise ValueError("terminal workflow requires its availability decision")
            if execution["status"] == "terminal":
                decision = execution["decision"]
                if (not isinstance(decision.get("usable"), bool) or not isinstance(decision.get("eligible"), bool)
                        or not isinstance(decision.get("reason"), str)
                        or decision["usable"] and decision["eligible"]):
                    raise ValueError("invalid recorded workflow availability decision")
            if index and (executions[0]["status"] != "terminal" or not executions[0]["decision"].get("eligible")):
                raise ValueError("replacement is not supported by the original terminal result")
        if slot.get("status") == "complete":
            selected = slot.get("selected_index")
            if (any(item["status"] != "terminal" for item in executions) or isinstance(selected, bool)
                    or selected not in range(len(executions))):
                raise ValueError("completed recovery slot has unfinished executions")
            usable = [index for index, item in enumerate(executions) if item["decision"]["usable"]]
            if selected != (usable[0] if usable else 0):
                raise ValueError("recovery must retain the first usable execution or original failure")
        elif slot.get("status") != "running":
            raise ValueError("invalid workflow recovery slot status")
    if len(set(identities)) != len(identities):
        raise ValueError("workflow execution identities cannot be reused")


async def run_workflow_slot(
    records: dict[str, dict[str, Any]], *, slot_id: str, condition: str,
    operation: str, execute: Callable[[int, str], Awaitable[Any]],
    allocate: Callable[[int], str], persist: Callable[[], Any],
    assert_allowed: Callable[[], Any],
    original: tuple[Any, str] | None = None,
    original_observation: tuple[RecoveryDecision, Mapping[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Resolve one slot. A persisted started execution can never auto-replay.

    ``original`` is only for an explicitly authorized linked re-evaluation of a
    recorded unavailable outcome; it consumes the original allowance up front.
    """
    if slot_id in records:
        validate_recovery_records(records)
        raise ValueError("workflow slot already recorded; use its published selected result, never replay")
    slot: dict[str, Any] = {"protocol": WORKFLOW_RECOVERY_PROTOCOL, "slot_id": slot_id,
            "condition": condition, "operation": operation, "status": "running", "executions": []}
    records[slot_id] = slot
    # The checkpoint owner may wrap/copy mutable containers to track changes.
    # Mutate its owned record, never the unowned construction dictionary.
    slot = records[slot_id]
    results = []

    async def commit() -> None:
        result = persist()
        if inspect.isawaitable(result):
            await result

    for index in range(2):
        assert_allowed()
        identity = original[1] if index == 0 and original is not None else allocate(index)
        entry = {"index": index, "execution_id": identity, "status": "started"}
        slot["executions"].append(entry)
        entry = slot["executions"][-1]
        await commit()  # Durable consumed allowance precedes any physical request.
        result = original[0] if index == 0 and original is not None else await execute(index, identity)
        results.append(result)
        if index == 0 and original is not None and original_observation is not None:
            decision, summary = original_observation
        else:
            decision, summary = recovery_decision(result, operation), execution_summary(result, operation)
        entry.update(status="terminal", decision=asdict(decision), outcome=copy.deepcopy(summary))
        await commit()
        if decision.usable or not decision.eligible or index == 1:
            break
    first_usable = next((index for index, item in enumerate(slot["executions"]) if item["decision"]["usable"]), None)
    slot.update(status="complete", selected_index=0 if first_usable is None else first_usable,
                selection_reason="first-usable" if first_usable is not None else "original-retained-unavailable")
    await commit()
    stopped = next((item for item, entry in zip(results, slot["executions"])
                    if entry["decision"]["reason"] == "operational-stop"), None)
    if stopped is not None:
        failed = stopped.shared if operation == "shared-prefix" else stopped
        failed.workflow_recovery = copy.deepcopy(slot)
        selected = results[slot["selected_index"]]
        if operation != "shared-prefix" and hasattr(selected, "workflow_recovery"):
            try:
                selected.workflow_recovery = copy.deepcopy(slot)
            except (AttributeError, TypeError):
                pass  # A linked frozen historical evaluation is immutable.
        raise WorkflowRecoveryStopped(failed, selected)
    return results[slot["selected_index"]], copy.deepcopy(slot)
