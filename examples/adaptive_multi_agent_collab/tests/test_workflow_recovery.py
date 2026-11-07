"""Recorded provider evidence, one scheduled slot, and first-usable selection."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.workflows.recovery import (
    WorkflowRecoveryConfig, WorkflowRecoveryStopped, recovery_decision,
    run_workflow_slot, validate_recovery_records,
)
from examples.adaptive_multi_agent_collab.schemas import QueryExample, Trajectory, UsageRecord


def trajectory(*, complete=True, cost=1.0, kind=None, billing=1.0, answer="yes", halted=None):
    result = Trajectory(QueryExample("q", "Synthetic yes/no", ("yes", "no"), "yes"), (), ())
    result.completed = complete
    result.status = "completed" if complete else "initialization-failed"
    result.final_answer = answer if complete else None
    result.operational_halt = halted
    result.sessions = [SimpleNamespace(normalized_cost=cost, routing=SimpleNamespace(decisions=()))] if complete else []
    result.usage = UsageRecord(input_tokens=10, output_tokens=2, monetary_cost=cost,
                               paid_monetary_cost=billing, model_calls=1)
    result.call_records = ({"logical_call": {"call_type": "role" if complete else "initial"},
                            "usage": {"raw_metadata": {"failure_kind": kind}},
                            "physical_dispatch_ids": [0], "seed": 42},)
    return result


@pytest.mark.parametrize("operation", ["weighting", "prompt", "selection", "test", "routing"])
def test_wrong_answers_and_missing_billing_are_usable(operation):
    value = trajectory(answer="no", billing=None)
    decision = recovery_decision(value, operation)
    assert decision.usable and not decision.eligible


@pytest.mark.parametrize("kind", ["provider-unavailable", "transport-unavailable", "ambiguous-timeout", "accounting-incomplete"])
def test_only_recorded_eligible_missing_information_can_replace(kind):
    value = trajectory(cost=None, kind=kind)
    assert recovery_decision(value, "selection").eligible
    assert recovery_decision(value, "weighting").usable
    assert recovery_decision(value, "prompt").usable


@pytest.mark.parametrize("kind", [None, "request-rejected", "provider-refusal", "output-schema", "interrupted-generation",
                                  "credential-rejected", "counting-bound", "serving-binding", "receipt-corruption"])
def test_unknown_cost_does_not_turn_excluded_failure_into_replacement(kind):
    assert not recovery_decision(trajectory(cost=None, kind=kind), "selection").eligible


def test_scientific_dependency_excludes_initial_billing_and_future_only_alpha_can_remain_usable():
    value = trajectory(cost=None, kind="accounting-incomplete")
    # No selected routing decisions => no missing positive-weight target inputs.
    assert recovery_decision(value, "routing").usable
    value.call_records[0]["logical_call"]["call_type"] = "initial"
    assert not recovery_decision(value, "selection").eligible


def test_schema_failure_with_missing_usage_is_not_a_provider_replacement():
    value = trajectory(cost=None, kind="accounting-incomplete")
    value.call_records[0].update(valid_output=False, validation_error="output keys invalid")
    assert not recovery_decision(value, "selection").eligible


@pytest.mark.parametrize("operation,call_type", [("weighting", "role"), ("selection", "initial")])
def test_classified_hard_failure_precedes_usable_weighting_supervision(operation, call_type):
    value = trajectory(kind="serving-binding")
    value.call_records[0]["logical_call"]["call_type"] = call_type
    decision = recovery_decision(value, operation)
    assert not decision.usable and not decision.eligible and decision.reason == "operational-stop"


def test_repaired_schema_does_not_veto_later_eligible_missing_tie_cost():
    value = trajectory(cost=None, kind="accounting-incomplete")
    schema = {"logical_call": {"call_type": "role", "conversation_id": "c", "repair_attempt": 0},
              "repair_attempt": 0, "valid_output": False, "validation_error": "invalid fields",
              "usage": {"monetary_cost": 0.1, "raw_metadata": {}}}
    repaired = {**schema, "logical_call": {**schema["logical_call"], "repair_attempt": 1},
                "repair_attempt": 1, "valid_output": True, "validation_error": None}
    value.call_records[0]["logical_call"]["call_type"] = "tie"
    value.call_records = (schema, repaired, *value.call_records)
    assert recovery_decision(value, "selection").eligible


def test_one_replacement_first_usable_and_original_reporting_on_second_failure():
    async def run(second_cost):
        states, calls, commits = {}, [], []
        values = [trajectory(cost=None, kind="provider-unavailable", answer="no"),
                  trajectory(cost=second_cost, kind="accounting-incomplete", answer="yes")]
        async def execute(index, identity):
            calls.append(identity)
            assert commits[-1]["slot"]["executions"][-1]["status"] == "started"
            return values[index]
        result, record = await run_workflow_slot(
            states, slot_id="slot", condition="frozen", operation="selection", execute=execute,
            allocate=lambda index: f"execution:{index}", persist=lambda: commits.append(copy.deepcopy(states)),
            assert_allowed=lambda: None,
        )
        assert calls == ["execution:0", "execution:1"]
        assert result is values[1 if second_cost is not None else 0]
        assert record["selected_index"] == (1 if second_cost is not None else 0)
        validate_recovery_records(states)
        restored = copy.deepcopy(states)
        with pytest.raises(ValueError, match="already recorded"):
            await run_workflow_slot(restored, slot_id="slot", condition="frozen", operation="selection",
                                    execute=execute, allocate=lambda index: "new", persist=lambda: None,
                                    assert_allowed=lambda: None)
        assert len(calls) == 2
    asyncio.run(run(2.0))
    asyncio.run(run(None))


def test_interrupted_intent_never_replays_and_hard_second_failure_cannot_hide_behind_original():
    async def run():
        states = {}
        async def crash(index, identity):
            raise RuntimeError("injected crash after durable intent")
        with pytest.raises(RuntimeError, match="injected crash"):
            await run_workflow_slot(states, slot_id="slot", condition="frozen", operation="selection",
                                    execute=crash, allocate=lambda index: "first", persist=lambda: None,
                                    assert_allowed=lambda: None)
        assert states["slot"]["executions"][0]["status"] == "started"
        with pytest.raises(ValueError, match="already recorded"):
            await run_workflow_slot(copy.deepcopy(states), slot_id="slot", condition="frozen", operation="selection",
                                    execute=crash, allocate=lambda index: "new", persist=lambda: None,
                                    assert_allowed=lambda: None)
        async def fail(index, identity):
            return trajectory(cost=None, kind="provider-unavailable") if not index else trajectory(halted={"reason": "hard stop"})
        with pytest.raises(WorkflowRecoveryStopped):
            await run_workflow_slot({}, slot_id="other", condition="frozen", operation="selection", execute=fail,
                                    allocate=lambda index: f"other:{index}", persist=lambda: None, assert_allowed=lambda: None)
    asyncio.run(run())


def _hooks(*, failure="cost", every=False, selected=False):
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment
    trainer, hooks, *_ = _build_offline_experiment()
    if selected:
        asyncio.run(trainer.run_pilot(hooks))
        asyncio.run(hooks.calibrate_termination())
        trainer.state.calibration_complete, trainer.state.phase = True, "complete"
    trainer.config = replace(trainer.config, workflow_recovery=WorkflowRecoveryConfig(enabled=True))
    trainer.declared_config = trainer.config
    trainer.hooks = hooks
    original = hooks.runtime_factory
    calls, counts = [], {}
    def factory(*args):
        runtime = original(*args)
        run = runtime.run
        async def execute(example, **kwargs):
            calls.append((example.example_id, kwargs))
            count = counts.get(example.example_id, 0)
            counts[example.example_id] = count + 1
            result = await run(example, **kwargs)
            if (count == 0 or every) and failure != "none":
                result.call_records += ({"logical_call": {"call_type": "role"}, "usage": {
                    "raw_metadata": {"failure_kind": "provider-unavailable"}}, "physical_dispatch_ids": []},)
                if failure == "initial":
                    result.completed, result.status, result.final_answer = False, "initialization-failed", None
                    result.sessions = []
                else:
                    result.sessions = [replace(item, normalized_cost=None) for item in result.sessions]
            return result
        runtime.run = execute
        return runtime
    hooks.runtime_factory = factory
    return trainer, hooks, calls


def test_actual_numerical_hook_uses_one_cursor_distinct_training_ids_and_first_usable():
    trainer, hooks, calls = _hooks(failure="initial")
    first = asyncio.run(trainer._trajectory("weighting"))
    assert len(calls) == 2 and hooks.sample_counters["weighting"] == 1
    assert trainer.state.workflow_attempt_counter == 2
    assert [kwargs["generation_context"].attempt_id for _, kwargs in calls] == [0, 1]
    assert [kwargs["routing_identity"].update_id for _, kwargs in calls] == [0, 1]
    assert first.training_execution_id == 1 and first.completed
    assert first.workflow_recovery["selected_index"] == 1


def test_actual_weighting_hook_keeps_cost_free_uninformative_trajectory_without_replacement():
    trainer, hooks, calls = _hooks(failure="cost")
    result = asyncio.run(trainer._trajectory("weighting"))
    assert len(calls) == 1 and result.completed and not result.weighting_eligible
    assert trainer.state.workflow_attempt_counter == 1 and hooks.sample_counters["weighting"] == 1


def test_actual_validation_keeps_paired_seeds_and_query_cache_prevents_reexecution(tmp_path):
    trainer, hooks, calls = _hooks()
    trainer.checkpoint_path = tmp_path / "checkpoint.pt"
    record = asyncio.run(hooks.validate("routing"))
    assert record.score is not None and len(calls) == 4
    for index in (0, 2):
        assert calls[index][0] == calls[index + 1][0]
        assert calls[index][1]["generation_context"] == calls[index + 1][1]["generation_context"]
    assert len(hooks.recovery_slots) == 2
    saved = hooks.state_dict()
    hooks.load_state_dict(saved)
    assert asyncio.run(hooks.validate("routing")) == record
    assert len(calls) == 4
    resumed_trainer, resumed, resumed_calls = _hooks()
    resumed_trainer.resume_from_checkpoint(trainer.checkpoint_path, hooks=resumed)
    assert asyncio.run(resumed.validate("routing")) == record
    assert resumed_calls == []


def test_actual_validation_second_failure_retains_original_and_full_denominator():
    _, hooks, calls = _hooks(failure="initial", every=True)
    record = asyncio.run(hooks.validate("routing"))
    assert len(calls) == 4 and len(record.examples) == 2
    assert record.score is None and record.accuracy == 0
    assert all(item.stopping_session == 0 and item.prediction is None for item in record.examples)
    assert all(item.workflow_recovery["selected_index"] == 0 for item in record.examples)


def test_actual_prompt_evidence_uses_same_iteration_and_one_query_cursor_per_slot():
    trainer, hooks, calls = _hooks(failure="initial")
    hooks.prompt_train_batch_size = 1
    asyncio.run(hooks.prompt_evidence("tie"))
    assert len(calls) == 2 and hooks.sample_counters["prompt"] == 1
    assert trainer.state.workflow_attempt_counter == 2
    assert len(hooks.prompt_evidence_records) == 1
    assert hooks.prompt_evidence_records[0]["selected_execution_id"] == 1


def test_shared_prefix_allowance_belongs_to_query_not_candidate():
    shared1, shared2 = trajectory(cost=None, kind="accounting-incomplete"), trajectory(cost=1)
    values = [SimpleNamespace(shared=shared1, prefixes=(shared1, shared1)),
              SimpleNamespace(shared=shared2, prefixes=(shared2, shared2))]
    calls = []
    async def execute(index, identity):
        calls.append(identity)
        return values[index]
    result, record = asyncio.run(run_workflow_slot(
        {}, slot_id="shared", condition="fixed", operation="shared-prefix", execute=execute,
        allocate=lambda index: f"shared:{index}", persist=lambda: None, assert_allowed=lambda: None,
    ))
    assert len(calls) == 2 and result is values[1]
    assert len(record["executions"]) == 2 and len(record["executions"][1]["outcome"]["candidate_boundaries"]) == 2


def test_actual_calibration_replaces_shared_realization_once_for_all_candidates():
    trainer, hooks, calls = _hooks()
    hooks.safety_record = SimpleNamespace(approved=True)
    first = trainer.config.termination
    hooks.termination_plan = replace(hooks.termination_plan, candidates=(first, replace(first, min_sessions=2, max_sessions=2)))
    result = asyncio.run(hooks.calibrate_termination())
    assert len(calls) == 4  # Two system queries, one shared replacement per query.
    assert len(result.candidates) == 2
    assert len(hooks.recovery_slots) == 2
    assert all(len(item["executions"]) == 2 and item["selected_index"] == 1 for item in hooks.recovery_slots.values())
    assert all(item["workflow_recovery"]["selected_index"] == 1 for item in result.shared_executions)


def test_actual_final_test_replacement_preserves_denominator_selected_cost_and_checkpoint(tmp_path):
    trainer, hooks, calls = _hooks(failure="initial", every=True, selected=True)
    path = tmp_path / "selected.pt"
    trainer.save_checkpoint(path)
    before = path.read_bytes()
    record = asyncio.run(hooks.final_test(path))
    assert path.read_bytes() == before
    assert len(calls) == 4 and len(record.predictions) == 2
    assert record.to_dict()["accuracy_by_run_seed"] == {"19": 0.0, "20": 0.0}
    assert all(item.answer is None and item.normalized_cost is None for item in record.predictions)
    assert all(len(item.workflow_recovery["executions"]) == 2 for item in record.predictions)


def test_actual_training_unfinished_execution_does_not_advance_sampling_cursor():
    trainer, hooks, _ = _hooks(failure="none")
    def runtime():
        async def crash(*args, **kwargs):
            raise RuntimeError("unfinished execution")
        return SimpleNamespace(run=crash)
    hooks._runtime = runtime
    with pytest.raises(RuntimeError, match="unfinished execution"):
        asyncio.run(trainer._trajectory("weighting"))
    assert hooks.sample_counters["weighting"] == 0
    assert trainer.state.workflow_attempt_counter == 1
    assert next(iter(hooks.recovery_slots.values()))["status"] == "running"


def test_actual_final_test_hard_stop_retains_all_planned_queries(tmp_path):
    trainer, hooks, calls = _hooks(failure="none", selected=True)
    factory = hooks.runtime_factory
    def stopped_factory(*args):
        runtime = factory(*args)
        original_run = runtime.run
        async def run(example, **kwargs):
            result = await original_run(example, **kwargs)
            result.operational_halt = {"reason": "injected hard stop"}
            result.error, result.status = "injected hard stop", "accounting-failed"
            return result
        runtime.run = run
        return runtime
    hooks.runtime_factory = stopped_factory
    path = tmp_path / "selected.pt"
    trainer.save_checkpoint(path)
    record = asyncio.run(hooks.final_test(path))
    assert len(calls) == 1 and len(record.predictions) == 2
    assert record.predictions[0].status == "accounting-failed"
    assert record.predictions[1].status == "unattempted-accounting-failed"
    assert not record.to_dict()["stage_complete"]


def test_linked_unchanged_joint_validation_reuses_original_good_queries_only_once():
    from examples.adaptive_multi_agent_collab.learning.evaluation import EvaluationRecord, ExampleEvaluation
    from examples.adaptive_multi_agent_collab.learning.training import _module_fingerprint
    trainer, hooks, calls = _hooks(failure="none")
    plan = hooks.plans["routing"]
    bad_id, good_id = plan.ordered_example_ids
    old = EvaluationRecord(plan, (
        ExampleEvaluation(bad_id, "no", 1, None, False, 1, None),
        ExampleEvaluation(good_id, "yes", 1, 0.0, True, 3, 12),
    ))
    hooks.execution_metadata["supplement_routing_source"] = {
        "evaluation": old.to_dict(), "alpha_fingerprint": _module_fingerprint(trainer.alpha),
        "weighting_fingerprint": _module_fingerprint(trainer.weighting), "gamma": trainer.gamma.to_dict(),
        "source_checkpoint_sha256": "a" * 64, "replacement_query_id": bad_id,
        "failure_evidence": {"dispatch_id": 883, "usage": {"failure_kind": "provider-unavailable"}},
    }
    revised = asyncio.run(hooks.validate_supplement_routing(weighting_changed=False))
    assert len(calls) == 1 and calls[0][0] == bad_id
    assert revised.examples[1] == old.examples[1] and old.score is None
    assert revised.examples[0].workflow_recovery["selected_index"] == 1
    assert hooks.evaluation_records[-1]["recovery_source"]["reused_query_ids"] == [good_id]
    assert asyncio.run(hooks.validate_supplement_routing(weighting_changed=False)) == revised
    assert len(calls) == 1


def test_real_journal_recovery_intents_are_durable_without_full_checkpoint_rewrites(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal, validate_journal_resume
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
    trainer, hooks, _ = _hooks(failure="initial")
    trainer.checkpoint_path = tmp_path / "checkpoint.pt"
    wire_durable_journal(trainer, hooks, tmp_path)
    trainer._checkpoint()
    original_checkpoint = trainer.checkpoint_path.read_bytes()
    captures, starts = [], []
    capture = trainer.checkpoint_payload
    def counted_capture(**kwargs):
        captures.append(True)
        return capture(**kwargs)
    monkeypatch.setattr(trainer, "checkpoint_payload", counted_capture)
    factory = hooks.runtime_factory
    def checked_factory(*args):
        runtime = factory(*args)
        original_run = runtime.run
        async def run(example, **kwargs):
            journal = read_journal(tmp_path)
            entry = [row for row in journal["progress"] if row["kind"] == "workflow-recovery"][-1]
            slot = entry["payload"]["slot"]
            assert journal["boundary"] is None
            assert slot["executions"][-1]["status"] == "started"
            assert trainer._request_boundary_pending
            starts.append(slot["executions"][-1]["execution_id"])
            return await original_run(example, **kwargs)
        runtime.run = run
        return runtime
    hooks.runtime_factory = checked_factory
    async def run():
        try:
            result = await trainer._trajectory("weighting")
            assert result.completed and starts == ["train:0", "train:1"]
            assert captures == [] and trainer.checkpoint_path.read_bytes() == original_checkpoint
            records = [row for row in read_journal(tmp_path)["progress"] if row["kind"] == "workflow-recovery"]
            assert len(records) == 5  # Start/terminal for each execution, then selected pair.
            assert records[-1]["payload"]["slot"]["selected_index"] == 1
            with pytest.raises(ValueError, match="unfinished workflow"):
                validate_journal_resume(trainer.checkpoint_path, tmp_path)
            await trainer.checkpoint_async()
            assert captures == [True]
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
        finally:
            await trainer.durable_journal.aclose()
    asyncio.run(run())


def test_crash_after_workflow_intent_keeps_allowance_and_blocks_old_checkpoint(tmp_path):
    from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal, validate_journal_resume
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
    trainer, hooks, _ = _hooks(failure="none")
    trainer.checkpoint_path = tmp_path / "checkpoint.pt"
    wire_durable_journal(trainer, hooks, tmp_path)
    trainer._checkpoint()
    before = trainer.checkpoint_path.read_bytes()
    async def crashed(index, identity):
        raise RuntimeError("crash before generation")
    async def run():
        try:
            with pytest.raises(RuntimeError, match="crash before generation"):
                await hooks._recover_workflow(slot_id="crash", operation="selection", execute=crashed,
                                              allocate=lambda index: f"crash:{index}")
            assert trainer.checkpoint_path.read_bytes() == before
            journal = read_journal(tmp_path)
            assert journal["boundary"] is None
            assert journal["progress"][-1]["payload"]["slot"]["executions"] == [
                {"index": 0, "execution_id": "crash:0", "status": "started"}
            ]
            with pytest.raises(ValueError, match="unfinished workflow"):
                validate_journal_resume(trainer.checkpoint_path, tmp_path)
        finally:
            await trainer.durable_journal.aclose()
    asyncio.run(run())
