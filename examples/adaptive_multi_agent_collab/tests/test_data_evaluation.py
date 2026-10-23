"""Offline evidence for query isolation, one-trajectory evaluation, and calibration."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.calibration import (
    PilotObservation,
    SafetyCalibrationPlan,
    SafetyCalibrationRecord,
    TerminationCalibrationPlan,
    TerminationCalibrationRecord,
    calibrate_safety,
    calibrate_termination,
)
from examples.adaptive_multi_agent_collab.config import (
    Config,
    LogicalAgentConfig,
    NetworkConfig,
    PhaseConfig,
    PromptPhaseConfig,
    SafetyConfig,
    SelectionConfig,
    TerminationConfig,
    TrainingConfig,
)
from examples.adaptive_multi_agent_collab.datasets.data import (
    DatasetPartitions,
    ActiveDataPlan,
    DatasetProvenance,
    load_strategyqa,
    partition_queries,
)
from examples.adaptive_multi_agent_collab.learning.evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    ExampleEvaluation,
    Experiment,
    FinalTestPlan,
    evaluate_runtime,
    scoring_configuration,
)
from examples.adaptive_multi_agent_collab.infrastructure.randomness import (
    GenerationContext,
    LogicalCallIdentity,
    derive_seed,
)
from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime
from examples.adaptive_multi_agent_collab.schemas import (
    QueryExample,
    RoutingMode,
    Trajectory,
)


def _example(identifier: str, answer: str = "yes") -> QueryExample:
    return QueryExample(identifier, f"Question {identifier}?", ("yes", "no"), answer)


def _dataset() -> DatasetPartitions:
    return DatasetPartitions(
        (_example("train-a"), _example("train-b", "no")),
        (_example("component-a"), _example("component-b", "no")),
        (_example("system-a"), _example("system-b", "no")),
        (_example("test-a"),),
        DatasetProvenance("synthetic", "fixture-v1", ()),
        5,
    )


def _plan(domain: str = "promptval") -> EvaluationPlan:
    partition = "sys-val" if domain == "sysval" else "comp-val"
    prefix = "system" if domain == "sysval" else "component"
    return EvaluationPlan(
        (f"{prefix}-a", f"{prefix}-b"),
        domain,
        19,
        partition,
        {"temperature": 0.0},
        scoring_configuration(Config(selection=SelectionConfig(1.0))),
    )


def _active(dataset, *, pilot_ids=("train-a",)):
    return ActiveDataPlan(
        tuple(item.example_id for item in dataset.train),
        tuple(item.example_id for item in dataset.component_validation),
        tuple(item.example_id for item in dataset.system_validation),
        pilot_ids,
        19,
        dataset.fingerprint,
        "offline_fixture",
    )


def _record(plan, *, cost=0.5, correct=True, stopping=1):
    return EvaluationRecord(
        plan,
        tuple(
            ExampleEvaluation(
                identifier,
                "yes" if correct else "no",
                stopping,
                cost,
                correct,
                2,
                20,
                complete_return=-99.0,
            )
            for identifier in plan.ordered_example_ids
        ),
    )


def test_strategyqa_split_reserves_union_before_stratification_and_excludes_duplicate_questions():
    examples = [_example(str(index), "yes" if index % 2 else "no") for index in range(42)]
    examples.append(replace(examples[0], example_id="duplicate", query="  QUESTION   0? "))
    kwargs: dict[str, Any] = dict(
        evaluation_id_sets=(("0",), ("1",)),
        split_seed=81,
        provenance=DatasetProvenance("StrategyQA", "local-fixture", ()),
    )
    dataset = partition_queries(examples, **kwargs)
    replay = partition_queries(list(reversed(examples)), **kwargs)
    assert tuple(len(group) for group in dataset.groups) == (32, 4, 4, 2)
    assert tuple(item.example_id for item in dataset.test) == ("0", "1")
    assert dataset.excluded_duplicate_ids == ("duplicate",)
    assert dataset.fingerprint == replay.fingerprint
    for group in dataset.groups[:3]:
        assert sum(example.gold_answer == "yes" for example in group) == len(group) // 2
    assert dataset.manifest()["provenance"]["revision"] == "local-fixture"


def test_query_identity_and_duplicate_text_cannot_cross_partitions_or_be_relabelled():
    dataset = _dataset()
    with pytest.raises(ValueError, match="duplicate"):
        replace(dataset, test=(replace(dataset.train[0], example_id="alias"),))
    with pytest.raises(ValueError, match="belongs to"):
        dataset.select("train", ("test-a",))
    with pytest.raises(ValueError, match="original query"):
        dataset.require_origin(replace(dataset.train[0], query="Another input"), "train")
    with pytest.raises(ValueError, match="verified"):
        partition_queries(
            dataset.train,
            evaluation_id_sets=(("unverified",),),
            split_seed=0,
            provenance=dataset.provenance,
        )
    with pytest.raises(ValueError, match="artifacts"):
        DatasetProvenance("StrategyQA", "release", (), evaluation_protocol="reconcile_main")


def test_strategyqa_local_ingestion_records_exact_sources_without_exposing_metadata_as_query(
    tmp_path,
):
    source = tmp_path / "data.json"
    source.write_text(
        json.dumps(
            [
                {
                    "qid": "x",
                    "question": "Does it work?",
                    "answer": True,
                    "facts": ["hidden supervision"],
                },
                {"qid": "y", "question": "Is it false?", "answer": "NO"},
            ]
        )
    )
    examples, provenance = load_strategyqa((source,), revision="recorded-release")
    assert [item.gold_answer for item in examples] == ["yes", "no"]
    assert examples[0].metadata == {"original_query_id": "x"}
    assert provenance.sources[0].sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text('[{"qid":"x","question":"Q","answer":1}]')
    with pytest.raises(ValueError, match="boolean"):
        load_strategyqa((source,), revision="release")


def test_validation_logical_call_seeds_are_domain_separated_order_independent_and_include_initial_repairs():
    initial = LogicalCallIdentity(0, 0, "", "initial")
    role = LogicalCallIdentity(1, 2, "2:0:1:S_V", "role", 3, 0)
    repaired = replace(role, repair_attempt=1)
    contexts = [GenerationContext(91, domain, "query") for domain in ("promptval", "alphaval", "sysval")]
    assert len({context.seed_for(initial) for context in contexts}) == 3
    assert contexts[0].seed_for(role) != contexts[0].seed_for(repaired)
    assert contexts[0].seed_for(role) == GenerationContext(91, "promptval", "query").seed_for(role)
    assert contexts[0].seed_for(initial) != GenerationContext(91, "promptval", "another-query").seed_for(initial)
    assert derive_seed(91, "route", "query", 4, 2) != derive_seed(91, "sysval", "query", 4, 2)
    assert _plan().trajectories_per_example == 1
    with pytest.raises(ValueError, match="designated"):
        replace(_plan(), partition="test")


def test_evaluation_records_reject_missing_reordered_or_duplicate_examples_and_preserve_costs():
    plan = _plan()
    results = (
        ExampleEvaluation("component-a", "yes", 1, 0.3, True, 3, 42),
        ExampleEvaluation("component-b", "no", 1, 0.2, False, 2, 21),
    )
    record = EvaluationRecord(plan, results)
    assert float(record) == pytest.approx(0.25)
    assert record.per_example_scores == pytest.approx((0.7, -0.2))
    replay = EvaluationRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert replay.examples[0].normalized_cost == 0.3
    for incomplete in (results[:1], tuple(reversed(results)), (results[0], results[0])):
        with pytest.raises(ValueError, match="complete outcome"):
            EvaluationRecord(plan, incomplete)


def test_actual_evaluation_driver_runs_one_complete_workflow_per_query_and_restores_modes(
    monkeypatch,
):
    import examples.adaptive_multi_agent_collab.learning.evaluation as evaluation_module

    dataset = _dataset()
    calls = []

    class ProbeRuntime:
        routing_policy = torch.nn.Linear(1, 1)
        weighting_model = torch.nn.Linear(1, 1)
        agents = {0: SimpleNamespace(supports_seed=True)}

        async def run(self, example, **kwargs):
            assert not torch.is_grad_enabled()
            assert not self.routing_policy.training and not self.weighting_model.training
            calls.append((example, kwargs))
            await asyncio.sleep(0)
            return SimpleNamespace(example=example)

    runtime = ProbeRuntime()
    runtime.weighting_model.eval()
    monkeypatch.setattr(
        evaluation_module,
        "_trajectory_evaluation",
        lambda trajectory, **kwargs: ExampleEvaluation(
            trajectory.example.example_id,
            "yes",
            1,
            0.5,
            True,
            2,
            20,
            seed_replay_supported=kwargs["seed_replay_supported"],
        ),
    )
    record = asyncio.run(evaluate_runtime(cast(Runtime, runtime), dataset, _plan()))
    assert [example.example_id for example, _ in calls] == list(_plan().ordered_example_ids)
    assert all(
        kwargs["routing_mode"] is RoutingMode.EVALUATION and kwargs["partition"] == "comp-val" for _, kwargs in calls
    )
    assert all(kwargs["generation_context"].domain == "promptval" for _, kwargs in calls)
    assert runtime.routing_policy.training and not runtime.weighting_model.training
    assert record.score == 0.5 and all(item.seed_replay_supported for item in record.examples)


def test_safety_pilot_preserves_declared_profile_and_requires_review_of_limit_hits():
    plan = SafetyCalibrationPlan(("train-a", "train-b"), seed=7)
    observations = (
        PilotObservation("train-a", (1, 3), (0.1, 0.3), (0.5, 1.0), True),
        PilotObservation(
            "train-b",
            (30,),
            (120.0,),
            (600.0,),
            True,
            safeguard_count=1,
            resource_limit_hits=("session-timeout",),
        ),
    )
    profile = SafetyConfig()
    record = calibrate_safety(plan, observations, profile)
    assert record.configuration is profile and record.requires_review and not record.approved
    assert record.observed_safeguard_rate == 0.5
    assert not any(record.coverage.values())
    assert SafetyCalibrationRecord.from_dict(json.loads(json.dumps(record.to_dict()))) == record
    with pytest.raises(ValueError, match="only the training"):
        replace(plan, partition="sys-val")
    sparse = calibrate_safety(
        plan,
        tuple(replace(item, resource_limit_hits=(), safeguard_count=0) for item in observations),
        profile,
    )
    assert not sparse.approved and not sparse.requires_review and sparse.configuration == profile


def test_termination_shared_calibration_is_complete_and_tie_broken_by_cost_then_configuration():
    earlier = TerminationConfig(max_sessions=2)
    later = TerminationConfig(max_sessions=3)
    plan = TerminationCalibrationPlan(_plan("sysval"), (later, earlier), later, 2, (1, 1), (1, 1), ("0", "S_self"))
    calls = []

    async def evaluate(shared_plan):
        calls.append(shared_plan)
        return (
            _record(shared_plan.validation, cost=0.2),
            _record(shared_plan.validation, cost=0.2),
        ), ()

    record = asyncio.run(calibrate_termination(plan, evaluate))
    assert calls == [plan] and record.selected_configuration == earlier
    assert TerminationCalibrationRecord.from_dict(json.loads(json.dumps(record.to_dict()))) == record
    with pytest.raises(ValueError, match="system-validation"):
        replace(plan, validation=_plan())
    with pytest.raises(ValueError, match="recalibration"):
        record.configuration_for_test(
            agent_count=3,
            outgoing_capacity=(1, 1, 1),
            incoming_capacity=(1, 1, 1),
            routing_labels=("0",),
        )

    async def incomplete(shared_plan):
        return (_record(shared_plan.validation),), ()

    with pytest.raises(ValueError, match="exactly once"):
        asyncio.run(calibrate_termination(plan, incomplete))


def test_experiment_sampler_checkpoint_validates_partitions_and_resumes_without_global_rng():
    dataset = _dataset()
    plans = {
        name: _plan(domain)
        for name, domain in (
            ("weighting", "wval"),
            ("routing", "alphaval"),
            ("prompt", "promptval"),
            ("system", "sysval"),
        )
    }
    experiment = Experiment(
        dataset,
        lambda *_: None,
        plans=plans,
        active_data_plan=_active(dataset),
        authorization_required=False,
    )
    experiment.bind(SimpleNamespace(config=Config(selection=SelectionConfig(1.0))))
    experiment.sample_counters["routing"] = 3
    state = experiment.state_dict()
    expected = experiment._training_example("routing")
    restored = Experiment(
        dataset,
        lambda *_: None,
        plans=plans,
        active_data_plan=_active(dataset),
        authorization_required=False,
    )
    restored.bind(SimpleNamespace(config=Config(selection=SelectionConfig(1.0))))
    restored.load_state_dict(state)
    assert restored._training_example("routing") == expected
    altered = json.loads(json.dumps(state))
    altered["condition"]["dataset_fingerprint"] = "different"
    with pytest.raises(ValueError, match="incompatible experiment"):
        restored.load_state_dict(altered)
    assert restored.sample_counters["routing"] == 3
    foreign = json.loads(json.dumps(state))
    record = EvaluationRecord(
        _plan(),
        (
            ExampleEvaluation("component-a", "yes", 1, 0.0, True, 1, 1),
            ExampleEvaluation("component-b", "yes", 1, 0.0, True, 1, 1),
        ),
    ).to_dict()
    record["plan"]["run_seed"] += 1
    foreign["evaluation_records"] = [record]
    with pytest.raises(ValueError, match="changed the declared experiment plan"):
        restored.load_state_dict(foreign)
    assert restored.sample_counters["routing"] == 3


def test_local_experiment_preparation_freezes_source_ids_plans_and_allows_only_outer_ceiling_extension(
    tmp_path,
):
    from examples.adaptive_multi_agent_collab.operations.experiment import prepare_experiment

    data_path = tmp_path / "strategyqa.json"
    data_path.write_text(
        json.dumps(
            [
                {
                    "qid": str(index),
                    "question": f"Question {index}?",
                    "answer": bool(index % 2),
                }
                for index in range(42)
            ]
        )
    )
    ids_path = tmp_path / "holdout.json"
    ids_path.write_text('["0", "1"]')
    specification = {
        "config": Config(selection=SelectionConfig(1.0)).to_dict(),
        "dataset": {
            "source_files": [data_path.name],
            "revision": "fixture",
            "evaluation_id_files": [ids_path.name],
            "split_seed": 7,
        },
        "pilot": {},
        "active_data": {"profile": "offline_fixture", "counts": [4, 2, 2, 1]},
        "termination_calibration": {},
        "backends": {},
        "textgrad": {},
    }
    source = tmp_path / "experiment.json"
    source.write_text(json.dumps(specification))
    prepared = prepare_experiment(source)
    assert prepared.dataset.test[0].example_id == "0"
    assert prepared.termination_plan.validation.domain == "sysval"
    assert prepared.plans["prompt"].partition == "comp-val"
    assert all(plan.trajectories_per_example == 1 for plan in prepared.plans.values())
    assert "#sha256=" in prepared.dataset.provenance.evaluation_artifacts[0]
    specification["config"]["training"]["max_outer_cycles"] += 1
    source.write_text(json.dumps(specification))
    extended = prepare_experiment(source)
    assert prepared.specification_sha256 == extended.specification_sha256
    specification["config"]["safety"]["parse_retries"] += 1
    source.write_text(json.dumps(specification))
    assert prepare_experiment(source).specification_sha256 != prepared.specification_sha256


def _build_offline_experiment(*, full_schedule=False, pilot_count=None):
    from examples.adaptive_multi_agent_collab.models.encoders import (
        FixedBaseEncoders,
        offline_encoder_configuration,
    )
    from examples.adaptive_multi_agent_collab.operations.experiment import (
        PreparedExperiment,
        build_experiment,
    )
    from examples.adaptive_multi_agent_collab.workflows.prompts import DeterministicFakeProposer
    from examples.adaptive_multi_agent_collab.schemas import SCHEME_ORDER

    training = TrainingConfig()
    if full_schedule:
        training = TrainingConfig(
            bootstrap_weighting_updates=1,
            bootstrap_weighting_attempts=2,
            bootstrap_routing_updates=1,
            max_outer_cycles=1,
            weighting_phase=PhaseConfig(max_updates=1, max_attempts=2),
            routing_phase=PhaseConfig(max_updates=1, max_attempts=2),
            prompt_phase=PromptPhaseConfig(max_iterations=1, patience=1),
            prompt_train_batch_size=1,
        )
    config = Config(
        n_agents=2,
        selection=SelectionConfig(1.0),
        agent_pool=tuple(LogicalAgentConfig(index, "shared", "local", "fixture") for index in range(2)),
        encoders=replace(offline_encoder_configuration(), numerical_precision="float64"),
        network=NetworkConfig(
            representation_dim=16,
            feedforward_dim=24,
            answer_byte_dim=8,
            transformer_layers=1,
            transformer_heads=2,
        ),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
        training=training,
    )
    dataset = _dataset()
    if pilot_count is not None:
        dataset = replace(
            dataset,
            train=tuple(_example(f"pilot-{index}") for index in range(pilot_count)),
        )
    plans = {
        name: _plan(domain)
        for name, domain in (
            ("weighting", "wval"),
            ("routing", "alphaval"),
            ("prompt", "promptval"),
            ("system", "sysval"),
        )
    }
    pilot = SafetyCalibrationPlan(("train-a", "train-b") if full_schedule else ("train-a",), seed=19)
    if pilot_count is not None:
        pilot = SafetyCalibrationPlan(tuple(item.example_id for item in dataset.train), seed=19)
    outgoing, incoming = config.capacity.resolve(config.n_agents)
    termination = TerminationCalibrationPlan(
        plans["system"],
        (config.termination,),
        config.termination,
        config.n_agents,
        outgoing,
        incoming,
        tuple(scheme.value for scheme in SCHEME_ORDER),
    )
    prepared = PreparedExperiment(
        config,
        dataset,
        plans,
        pilot,
        termination,
        FinalTestPlan(("test-a",), (19, 20)),
        {
            "shared": {
                "model_name": "local",
                "revision": "fixture",
                "supports_seed": True,
            }
        },
        {},
        "a" * 64,
        _active(dataset, pilot_ids=pilot.ordered_example_ids),
    )

    class LocalModel:
        def __init__(self):
            self.calls = []

        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            role = "component role:" in str(kwargs.get("messages", ()))
            return SimpleNamespace(
                content=(
                    '{"status":"complete","feedback":"complete reasoning"}'
                    if role
                    else '{"answer":"yes","explanation":"local reasoning"}'
                ),
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "total_cost": 0.0,
                },
            )

    models = []

    def model_factory(backend):
        assert backend["model_name"] == "local"
        model = LocalModel()
        models.append(model)
        return model

    encoders = FixedBaseEncoders(config.n_agents, configuration=config.encoders)
    proposer = DeterministicFakeProposer()
    trainer, experiment = build_experiment(
        prepared,
        model_factory=model_factory,
        fixed_encoders=encoders,
        proposer=proposer,
    )
    return trainer, experiment, models, encoders, proposer


def test_actual_offline_build_shares_backend_and_fixed_cache_and_evaluates_with_logical_seeds():
    trainer, experiment, models, encoders, proposer = _build_offline_experiment()
    assert experiment.expenditure_ledger.on_change == trainer._request_checkpoint
    assert len(models) == 1 and not models[0].calls and proposer.calls == 0
    runtime = experiment._runtime()
    assert runtime.agents[0] is runtime.agents[1]
    assert runtime.encoders is encoders and experiment._runtime().encoders is encoders
    assert next(trainer.alpha.parameters()).dtype == next(trainer.weighting.parameters()).dtype == torch.float64
    first = asyncio.run(experiment.validate("routing"))
    second = asyncio.run(experiment.validate("routing"))
    assert first.to_dict() == second.to_dict()
    assert len(first.examples) == 2 and all(item.model_calls == 2 for item in first.examples)
    assert all(len(item.generation_seeds) == 2 and item.seed_replay_supported for item in first.examples)
    assert len(models[0].calls) == 4  # Exact completed-record reuse makes no new generations.
    assert proposer.calls == 0


def test_final_test_runs_predeclared_full_partition_after_gold_removal_and_rejects_unfrozen_models(
    tmp_path,
):
    trainer, experiment, models, _, _ = _build_offline_experiment()
    checkpoint = tmp_path / "selected.pt"
    with pytest.raises(ValueError, match="completed joint checkpoint"):
        asyncio.run(experiment.final_test(checkpoint))
    experiment.safety_record = calibrate_safety(
        experiment.pilot_plan,
        (PilotObservation("train-a", (2,), (0.5,), (1.0,), True),),
        trainer.config.safety,
    )
    trainer.apply_configuration(replace(trainer.config, safety=experiment.safety_record.configuration))
    experiment.pilot_observations = list(experiment.safety_record.observations)
    assert experiment._runtime().agents[0].timeout == experiment.safety_record.configuration.call_timeout_seconds
    trainer.state.pilot_complete = True
    asyncio.run(experiment.calibrate_termination())
    trainer.state.calibration_complete = True
    trainer.state.phase = "complete"
    trainer.hooks = experiment
    trainer.save_checkpoint(checkpoint)
    seen = []
    factory = experiment.runtime_factory

    def inspect_factory(config, prompts):
        runtime = factory(config, prompts)
        run = runtime.run

        async def inspect_input(example, **kwargs):
            assert example.gold_answer is None and set(example.metadata) == {"original_query_id"}
            assert kwargs["partition"] == "test" and kwargs["generation_context"].domain == "test"
            seen.append(example.example_id)
            return await run(example, **kwargs)

        runtime.run = inspect_input
        return runtime

    experiment.runtime_factory = inspect_factory
    record = asyncio.run(experiment.final_test(checkpoint))
    assert seen == ["test-a", "test-a"]
    assert record.to_dict()["accuracy_by_run_seed"] == {"19": 1.0, "20": 1.0}
    assert record.checkpoint_sha256 == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert all(item.correct and item.generation_seeds for item in record.predictions)
    assert record.predictions[0].generation_seeds != record.predictions[1].generation_seeds
    assert all("seed" in call for call in models[0].calls)
    with torch.no_grad():
        next(trainer.alpha.parameters()).add_(1)
    with pytest.raises(ValueError, match="parameters differ"):
        asyncio.run(experiment.final_test(checkpoint))


def test_concrete_experiment_executes_bounded_offline_schedule_and_restores_dataset_calibrations(
    tmp_path,
):
    trainer, experiment, models, _, _ = _build_offline_experiment(full_schedule=True)
    checkpoint = tmp_path / "complete.pt"
    assert asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint)).phase == "pilot_complete"
    state = asyncio.run(trainer.fit(experiment, checkpoint_path=checkpoint))
    assert state.phase == "complete" and state.outer_cycle == 1
    assert state.pilot_complete and state.calibration_complete and all(state.bootstrap_complete.values())
    assert trainer.best_system is not None
    assert experiment.safety_record.plan.partition == "train"
    assert experiment.termination_record.plan.validation.partition == "sys-val"
    assert all(example.example_id.startswith("train") for example in experiment.safety_record.observations)
    assert models[0].calls
    assert all(isinstance(call.get("seed"), int) for call in models[0].calls)
    restored_trainer, restored, fresh_models, _, _ = _build_offline_experiment(full_schedule=True)
    restored_trainer.resume_from_checkpoint(checkpoint, hooks=restored)
    assert not fresh_models[0].calls
    assert restored.state_dict() == experiment.state_dict()
    assert restored_trainer.config == trainer.config
    assert asyncio.run(restored_trainer.fit(restored)).phase == "complete"
    assert not fresh_models[0].calls


@pytest.mark.parametrize(
    "group,field,error",
    (
        ("safety", "call_timeout_seconds", "active safeguards disagree"),
        ("termination", "max_sessions", "active termination disagrees"),
    ),
)
def test_checkpoint_rejects_changed_active_calibration_before_restoring_state(tmp_path, group, field, error):
    trainer, experiment, _, _, _ = _build_offline_experiment(full_schedule=True)
    checkpoint = tmp_path / "complete.pt"
    asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint))
    asyncio.run(trainer.fit(experiment, checkpoint_path=checkpoint))
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    payload["config"][group][field] += 1
    changed = tmp_path / "changed.pt"
    torch.save(payload, changed)
    target, hooks, models, _, _ = _build_offline_experiment(full_schedule=True)
    before = {
        "alpha": copy.deepcopy(target.alpha.state_dict()),
        "weighting": copy.deepcopy(target.weighting.state_dict()),
        "hooks": hooks.state_dict(),
        "config": target.config,
        "rng": torch.random.get_rng_state().clone(),
    }
    with pytest.raises(ValueError, match=error):
        target.resume_from_checkpoint(changed, hooks=hooks)
    assert target.config == before["config"] and target.state.phase == "unstarted"
    assert hooks.state_dict() == before["hooks"] and not models[0].calls
    assert torch.equal(torch.random.get_rng_state(), before["rng"])
    for name in ("alpha", "weighting"):
        assert all(torch.equal(value, before[name][key]) for key, value in getattr(target, name).state_dict().items())


@pytest.mark.parametrize("component", ("routing", "weighting", "prompt"))
def test_training_generation_seeds_replay_saved_sampler_and_refresh_for_next_attempt(
    component,
):
    from examples.adaptive_multi_agent_collab.models.policies import RoutingRolloutIdentity

    trainer, experiment, models, _, _ = _build_offline_experiment()
    state = experiment.state_dict()
    identity = RoutingRolloutIdentity(trainer.config.seed, "replaced-by-original-query", 9)

    async def sample(hooks):
        if component == "prompt":
            await hooks.prompt_evidence(next(iter(trainer.gamma.components)))
        else:
            await hooks.trajectory(
                component,
                routing_identity=replace(identity, update_id=hooks.trainer.allocate_workflow_attempt()),
            )

    asyncio.run(sample(experiment))
    initial_seeds = tuple(call["seed"] for call in models[0].calls)
    assert initial_seeds
    _, restored, restored_models, _, _ = _build_offline_experiment()
    restored.load_state_dict(state)
    asyncio.run(sample(restored))
    replayed_seeds = tuple(call["seed"] for call in restored_models[0].calls)
    assert replayed_seeds == initial_seeds
    completed_count = len(restored_models[0].calls)
    asyncio.run(sample(restored))
    next_seeds = tuple(call["seed"] for call in restored_models[0].calls[completed_count:])
    assert next_seeds and set(initial_seeds).isdisjoint(next_seeds)
    assert restored.state_dict()["condition"]["training_generation_policy"]


def test_shared_selection_uses_final_correctness_cost_and_never_training_return():
    plan = _plan("alphaval")
    outcomes = (
        ExampleEvaluation("component-a", "yes", 2, 0.2, True, 4, 20, complete_return=-100),
        ExampleEvaluation("component-b", "no", 3, 0.4, False, 6, 30, complete_return=100),
    )
    record = EvaluationRecord(plan, outcomes)
    assert record.score == pytest.approx(0.2)
    assert record.accuracy == 0.5 and record.mean_normalized_cost == pytest.approx(0.3)
    changed_diagnostic = replace(record, examples=tuple(replace(item, complete_return=999) for item in outcomes))
    assert changed_diagnostic.score == record.score
    changed_plan = replace(plan, scoring_configuration={**plan.scoring_configuration, "lambda_select": 2.0})
    assert EvaluationRecord(changed_plan, outcomes).score == pytest.approx(-0.1)
    assert changed_plan.fingerprint != plan.fingerprint


def test_weighting_validation_means_within_query_then_over_eligible_queries_and_unavailable_roundtrip():
    plan = _plan("wval")
    outcomes = (
        ExampleEvaluation(
            "component-a",
            "yes",
            1,
            0,
            True,
            1,
            1,
            informative_sessions=1,
            weighting_loss_sum=0.2,
        ),
        ExampleEvaluation(
            "component-b",
            "yes",
            3,
            0,
            True,
            1,
            1,
            informative_sessions=3,
            weighting_loss_sum=2.4,
        ),
    )
    record = EvaluationRecord(plan, outcomes, "negative_weighting_nll")
    assert record.score == pytest.approx(-0.5) and record.eligible_query_fraction == 1
    unavailable = replace(
        record,
        examples=tuple(replace(item, informative_sessions=0, weighting_loss_sum=0) for item in outcomes),
    )
    assert unavailable.score is None and unavailable.eligible_query_fraction == 0
    assert EvaluationRecord.from_dict(json.loads(json.dumps(unavailable.to_dict(), allow_nan=False))).score is None
    with pytest.raises(ValueError, match="no eligible query"):
        float(unavailable)


def test_training_seed_is_direct_component_query_global_attempt_and_logical_call():
    logical = LogicalCallIdentity(1, 2, "stream", "role", 3, 1)
    context = GenerationContext(7, "train_generation", "query", "weighting", 11)
    expected = derive_seed(7, "train_generation", "weighting", "query", 11, 1, 2, "stream", "role", 3, 1)
    assert context.seed_for(logical) == expected
    assert replace(context, component="routing").seed_for(logical) != expected
    assert replace(context, attempt_id=12).seed_for(logical) != expected
    with pytest.raises(ValueError, match="requires a component"):
        GenerationContext(7, "train_generation", "query")


def _enable_stage_authorization(trainer, experiment):
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger

    resources = replace(
        trainer.config.resources,
        compatibility_budget=3.0,
        pilot_budget=5.0,
        development_budget=80.0,
        test_budget=20.0,
        currency="USD",
    )
    trainer.config = trainer.declared_config = replace(trainer.config, resources=resources)
    experiment.authorization_required = True
    experiment.execution_session_guard = lambda: True  # Explicit offline session fixture.
    experiment.execution_metadata.update(
        authorization_manifest_fingerprint="a" * 64,
        resource_profile_fingerprint="b" * 64,
    )
    experiment.expenditure_ledger = ExpenditureLedger(resources)
    experiment.expenditure_ledger.on_change = trainer._request_checkpoint


def _stage_artifact(experiment, stage, **bindings):
    from examples.adaptive_multi_agent_collab.operations.preflight import (
        stage_authorization_template,
    )

    return {
        **stage_authorization_template(**experiment._authorization_bindings(stage, **bindings)),
        "decision": "approve_stage",
        "reviewer": "fixture-reviewer",
        "rationale": "Approve the exact bounded fixture scope.",
    }


@pytest.mark.parametrize("guard", (None, lambda: False))
def test_production_stage_approval_cannot_dispatch_without_active_execution_session(guard):
    trainer, experiment, models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(trainer, experiment)
    experiment.execution_session_guard = guard
    plan = experiment.compatibility_plan()
    experiment.authorize_stage(_stage_artifact(experiment, "pilot"))
    experiment.authorize_stage(
        _stage_artifact(experiment, "compatibility", compatibility_plan_fingerprint=plan["fingerprint"])
    )
    for stage in ("pilot", "development", "test", "compatibility"):
        with pytest.raises(ValueError, match="active locked campaign session"):
            experiment.require_stage_authorization(stage)
    with pytest.raises(ValueError, match="active locked campaign session"):
        asyncio.run(trainer.run_pilot(experiment))
    assert not models[0].calls and not experiment.pilot_observations
    assert "execution_session_guard" not in experiment.state_dict()
    active = True
    experiment.execution_session_guard = lambda: active
    experiment.require_stage_authorization("pilot")
    active = False
    with pytest.raises(ValueError, match="active locked campaign session"):
        experiment.require_stage_authorization("pilot")


def test_pilot_review_reuses_saved_observations_and_requires_exact_condition(tmp_path):
    trainer, experiment, models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(trainer, experiment)
    experiment.authorize_stage(_stage_artifact(experiment, "pilot"))
    trainer.state.phase = "pilot"
    trainer.hooks = experiment
    observation = PilotObservation("train-a", (4,), (1.0,), (2.0,), True, 1, ("round-cap",))
    experiment.pilot_observations = [observation]
    experiment.safety_record = calibrate_safety(experiment.pilot_plan, (observation,), trainer.config.safety)
    checkpoint = tmp_path / "review.pt"
    state = asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint))
    assert state.phase == "pilot_complete" and state.pilot_complete and not models[0].calls
    assert not experiment.safety_record.approved
    with pytest.raises(ValueError, match="development stage authorization"):
        asyncio.run(trainer.fit(experiment))
    resumed, restored, fresh_models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(resumed, restored)
    resumed.resume_from_checkpoint(checkpoint, hooks=restored)
    approval = _stage_artifact(restored, "development")
    with pytest.raises(ValueError, match="differs from the exact"):
        restored.authorize_stage({**approval, "pilot_fingerprint": "0" * 64})
    restored.authorize_stage(approval)
    accepted = restored.safety_record
    assert accepted.approved and accepted.acceptance_method == "reviewed_declared_profile"
    assert asyncio.run(resumed.run_pilot(restored)).phase == "pilot_complete"
    assert not fresh_models[0].calls
    assert SafetyCalibrationRecord.from_dict(accepted.to_dict()) == accepted


def test_dedicated_eight_query_pilot_freezes_components_and_stops_after_24_sessions(
    tmp_path,
):
    trainer, experiment, models, _, proposer = _build_offline_experiment(pilot_count=8)
    _enable_stage_authorization(trainer, experiment)
    with pytest.raises(ValueError, match="pilot-only"):
        asyncio.run(trainer.fit(experiment))
    with pytest.raises(ValueError, match="pilot stage authorization"):
        asyncio.run(trainer.run_pilot(experiment))
    assert not models[0].calls
    experiment.authorize_stage(_stage_artifact(experiment, "pilot"))
    before = tuple(copy.deepcopy(module.state_dict()) for module in (trainer.alpha, trainer.weighting))
    prompts = trainer.gamma.to_dict()
    seen = []
    factory = experiment.runtime_factory

    def tracked_factory(*args):
        runtime = factory(*args)
        original = runtime.run

        async def run(example, **kwargs):
            assert kwargs["routing_mode"] == RoutingMode.TRAIN and kwargs["fixed_horizon"] == 3
            assert kwargs["partition"] == "train" and kwargs["generation_context"].domain == "pilot"
            assert all(
                not p.requires_grad for module in (trainer.alpha, trainer.weighting) for p in module.parameters()
            )
            trajectory = await original(example, **kwargs)
            seen.append((example.example_id, len(trajectory.sessions)))
            return trajectory

        runtime.run = run
        return runtime

    experiment.runtime_factory = tracked_factory
    checkpoint = tmp_path / "pilot.pt"
    state = asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint))
    assert seen == [(f"pilot-{index}", 3) for index in range(8)]
    assert state.phase == "pilot_complete" and state.pilot_complete
    assert not state.calibration_complete and not any(state.bootstrap_complete.values())
    assert not any(state.updates.values()) and not any(state.attempts.values())
    assert not trainer.alpha_optimizer.state and not trainer.weighting_optimizer.state
    assert experiment.termination_record is None and not experiment.evaluation_records and proposer.calls == 0
    assert trainer.gamma.to_dict() == prompts
    for module, saved in zip((trainer.alpha, trainer.weighting), before, strict=True):
        assert all(torch.equal(value, saved[key]) for key, value in module.state_dict().items())
    report = experiment.safety_record.to_dict()
    assert report["paid_query_costs_usd"] == [0.0] * 8 and not report["approved"]
    assert not report["paid_query_cost_limitations"]["failed_or_censored_query_ids"]
    calls = len(models[0].calls)
    with pytest.raises(ValueError, match="development stage authorization"):
        asyncio.run(trainer.fit(experiment))
    assert len(models[0].calls) == calls
    resumed, restored, fresh_models, _, _ = _build_offline_experiment(pilot_count=8)
    _enable_stage_authorization(resumed, restored)
    resumed.resume_from_checkpoint(checkpoint, hooks=restored)
    assert asyncio.run(resumed.run_pilot(restored)).phase == "pilot_complete"
    assert not fresh_models[0].calls


def test_pilot_resumes_only_remaining_safe_query_boundaries(tmp_path):
    trainer, experiment, _, _, _ = _build_offline_experiment(pilot_count=3)
    checkpoint = tmp_path / "pilot-prefix.pt"
    factory = experiment.runtime_factory
    started = []

    def interrupted_factory(*args):
        runtime = factory(*args)
        original = runtime.run

        async def run(example, **kwargs):
            started.append(example.example_id)
            if len(started) == 2:
                raise KeyboardInterrupt("interrupted before a request")
            return await original(example, **kwargs)

        runtime.run = run
        return runtime

    experiment.runtime_factory = interrupted_factory
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint))
    assert [item.example_id for item in experiment.pilot_observations] == ["pilot-0"]
    resumed, restored, _, _, _ = _build_offline_experiment(pilot_count=3)
    resumed.resume_from_checkpoint(checkpoint, hooks=restored)
    resumed_factory = restored.runtime_factory
    remaining = []

    def traced_factory(*args):
        runtime = resumed_factory(*args)
        original = runtime.run

        async def run(example, **kwargs):
            remaining.append(example.example_id)
            return await original(example, **kwargs)

        runtime.run = run
        return runtime

    restored.runtime_factory = traced_factory
    asyncio.run(resumed.run_pilot(restored, checkpoint_path=checkpoint))
    assert remaining == ["pilot-1", "pilot-2"]
    assert len(restored.safety_record.observations) == 3


def test_compatibility_plan_is_bounded_separately_authorized_and_discards_proposal(
    tmp_path,
):
    from examples.adaptive_multi_agent_collab.workflows.prompts import PromptProposal

    trainer, experiment, models, _, proposer = _build_offline_experiment()
    _enable_stage_authorization(trainer, experiment)
    trainer.hooks, trainer.checkpoint_path = experiment, tmp_path / "compatibility.pt"
    plan = experiment.compatibility_plan()
    assert plan["logical_calls_max"] == 13 and plan["physical_calls_max"] == 24
    assert plan["budget_stage"] == "compatibility" and plan["budget_usd"] == 3
    assert not models[0].calls and proposer.calls == 0
    experiment.authorize_stage(_stage_artifact(experiment, "pilot"))
    with pytest.raises(ValueError, match="compatibility stage authorization"):
        asyncio.run(experiment.run_compatibility(plan))
    experiment.authorize_stage(
        _stage_artifact(
            experiment,
            "compatibility",
            compatibility_plan_fingerprint=plan["fingerprint"],
        )
    )
    before = trainer.gamma.to_dict()
    seen = []

    async def propose(spec, evidence, *, iteration_id):
        seen.append((spec.key, evidence[0].split, evidence[0].example_id, iteration_id))
        return PromptProposal(spec.template + "\nUncommitted compatibility edit.")

    proposer.propose_compatibility = propose
    result = asyncio.run(experiment.run_compatibility(plan))
    assert result["status"] == "complete" and len(result["requests"]) == 10
    assert len(models[0].calls) == 10 and seen[0][1:3] == (
        "synthetic",
        "compatibility-synthetic-arithmetic",
    )
    assert trainer.gamma.to_dict() == before and result["committed_prompt_updates"] == 0
    assert not any(trainer.state.updates.values()) and trainer.state.phase == "unstarted"
    assert not trainer.alpha_optimizer.state and not trainer.weighting_optimizer.state
    assert result["parameter_receipts"]["unchanged"]
    assert not result["assessment"]["passed"]  # Offline fixtures provide no native verification receipts.
    assert asyncio.run(experiment.run_compatibility(plan)) == result and len(models[0].calls) == 10


def test_final_report_uses_run_sample_deviation_and_separates_reference_from_paid_cost():
    from examples.adaptive_multi_agent_collab.learning.evaluation import (
        FinalTestRecord,
        TestPrediction,
    )

    plan = FinalTestPlan(("q0", "q1"), (42, 43, 44))
    items = tuple(
        TestPrediction(
            run_seed=seed,
            example_id=f"q{index}",
            answer="yes",
            correct=correct,
            normalized_cost=2.0,
            model_calls=5,
            tokens=30,
            generation_seeds=(seed,),
            seed_replay_supported=True,
            reference_collaboration_cost_usd=0.4,
            paid_inference_cost_usd=0.25,
            input_tokens=20,
            output_tokens=10,
        )
        for seed, answers in (
            (42, (False, False)),
            (43, (True, False)),
            (44, (True, True)),
        )
        for index, correct in enumerate(answers)
    )
    report = FinalTestRecord(plan, "a" * 64, "b" * 64, items).to_dict()
    assert report["accuracy_by_run_seed"] == {"42": 0.0, "43": 0.5, "44": 1.0}
    assert report["accuracy_mean"] == 0.5 and report["accuracy_standard_deviation"] == 0.5
    assert report["independent_question_count"] == 2 and report["prediction_count"] == 6
    assert all(run["reference_collaboration_cost_usd"] == 0.8 for run in report["runs"])
    assert all(run["paid_inference_cost_usd_including_initialization"] == 0.5 for run in report["runs"])


@pytest.mark.parametrize("failure", (None, "undercount", "missing_reasoning", "missing_billing", "missing_identity"))
def test_compatibility_records_native_receipts_and_stops_after_counter_violation(tmp_path, failure):
    import time
    from examples.adaptive_multi_agent_collab.workflows.prompts import PromptProposal
    from examples.adaptive_multi_agent_collab.providers.resources import BackendResourceProfile, ResourceController

    trainer, experiment, models, _, proposer = _build_offline_experiment()
    _enable_stage_authorization(trainer, experiment)
    trainer.hooks, trainer.checkpoint_path = experiment, tmp_path / "native-compatibility.pt"

    class Counter:
        def count_input(self, messages, schema=None):
            return sum(len(item["content"].split()) + 2 for item in messages) + 1

        def count_output(self, value):
            return len(value.split())

    counter = Counter()
    profile = BackendResourceProfile(
        "fixture",
        "fixed",
        "transformers",
        20000,
        4096,
        1e-6,
        2e-6,
        "USD",
        "fixture",
        reference_input_price=1e-6,
        reference_output_price=2e-6,
        reference_pricing_date="fixture",
    )
    controller = ResourceController(profile, counter, experiment.expenditure_ledger, limits=trainer.config.resources)
    model = models[0]
    original = model.invoke

    async def native_invoke(**kwargs):
        output = await original(**kwargs)
        count = counter.count_input(kwargs["messages"])
        output.usage_metadata = {
            "input_tokens": count + int(failure == "undercount"),
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "total_cost": count * 1e-6 + 20 * 2e-6,
            "is_byok": False,
            "served_model": "fixture",
            "served_provider": "fixture",
            "response_identity_verified": True,
            "usage_sources": {"input_tokens": "generation_native", "output_tokens": "generation_native"},
        }
        missing_key = {
            "missing_reasoning": "reasoning_tokens",
            "missing_billing": "is_byok",
            "missing_identity": "response_identity_verified",
        }.get(failure)
        if missing_key:
            output.usage_metadata.pop(missing_key)
        output.finish_reason = "stop"
        return output

    model.invoke = native_invoke
    for adapter in experiment._runtime().agents.values():
        adapter.resources = controller

    async def propose(spec, _evidence, *, iteration_id):
        for kind in ("textgrad_evaluation", "textgrad_backward", "textgrad_optimizer"):
            await controller.invoke(
                model,
                messages=[{"role": "user", "content": "synthetic engine fixture"}],
                kind=kind,
                deadline=time.monotonic() + 10,
                settings={},
            )
        return PromptProposal(spec.template + "\nDiscarded fixture proposal.")

    proposer.propose_compatibility = propose
    plan = experiment.compatibility_plan()
    experiment.authorize_stage(
        _stage_artifact(experiment, "compatibility", compatibility_plan_fingerprint=plan["fingerprint"])
    )
    result = asyncio.run(experiment.run_compatibility(plan))
    assert result["parameter_receipts"]["unchanged"]
    assert experiment.expenditure_ledger.stage_summary("pilot")["attempts"] == 0
    if failure:
        assert result["status"] == "failed" and len(model.calls) == 1
        assert experiment.expenditure_ledger.halted is not None
        assert not result["assessment"]["passed"] and result["attempts_used"] == 1
    else:
        assert result["status"] == "complete" and result["assessment"]["passed"]
        assert len(model.calls) == result["attempts_used"] == 13
        assert all(item["protocol_validated"] for item in result["requests"])
        assert len(result["textgrad_receipts"]) == 3
        assert experiment.expenditure_ledger.stage_summary("compatibility")["unresolved"] == 0


def test_development_and_test_require_separate_approval_bound_to_selected_file(tmp_path):
    trainer, experiment, models, _, _ = _build_offline_experiment(full_schedule=True)
    _enable_stage_authorization(trainer, experiment)
    checkpoint = tmp_path / "selected.pt"
    experiment.authorize_stage(_stage_artifact(experiment, "pilot"))
    asyncio.run(trainer.run_pilot(experiment, checkpoint_path=checkpoint))
    experiment.authorize_stage(_stage_artifact(experiment, "development"))
    asyncio.run(trainer.fit(experiment, checkpoint_path=checkpoint))
    assert trainer.state.phase == "complete"
    calls = len(models[0].calls)
    with pytest.raises(ValueError, match="test stage authorization"):
        asyncio.run(experiment.final_test(checkpoint))
    experiment.authorize_stage(_stage_artifact(experiment, "test", checkpoint_sha256="c" * 64))
    with pytest.raises(ValueError, match="differs from the exact"):
        asyncio.run(experiment.final_test(checkpoint))
    assert len(models[0].calls) == calls
    experiment.authorize_stage(
        _stage_artifact(experiment, "test", checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
    )
    report = asyncio.run(experiment.final_test(checkpoint)).to_dict()
    assert report["stage_complete"] and report["independent_question_count"] == 1
    assert len(models[0].calls) > calls and trainer.state.phase == "complete"


def test_pilot_reports_nested_call_types_missing_usage_and_resource_errors():
    from examples.adaptive_multi_agent_collab.learning.calibration import (
        collect_pilot_observation,
    )

    trajectory = SimpleNamespace(
        example=SimpleNamespace(example_id="pilot"),
        sessions=(),
        call_durations=(1.0,),
        completed=True,
        error=None,
        call_records=(
            {
                "logical_call": {"call_type": "initial"},
                "error": "output-truncated",
                "usage": {"input_tokens": 2, "output_tokens": None, "monetary_cost": 0},
            },
        ),
    )
    observation = collect_pilot_observation(cast(Trajectory, trajectory))
    assert observation.call_types == ("initial",) and observation.usage_missing
    assert observation.resource_limit_hits == ("output-truncated",)


def test_pilot_budget_exhaustion_stops_before_next_query_and_keeps_partial_observation(
    tmp_path,
):
    trainer, experiment, _, _, _ = _build_offline_experiment(full_schedule=True)
    seen = []

    async def run(example, **kwargs):
        seen.append(example.example_id)
        return SimpleNamespace(
            example=example,
            sessions=(),
            call_durations=(),
            call_records=(),
            completed=False,
            error="pilot budget exhausted",
            status="budget-exhausted",
        )

    experiment.runtime_factory = lambda *_: SimpleNamespace(run=run)
    path = tmp_path / "partial-pilot.pt"
    state = asyncio.run(trainer.run_pilot(experiment, checkpoint_path=path))
    assert state.phase == "failed" and seen == ["train-a"]
    assert not experiment.safety_record.execution_complete and len(experiment.pilot_observations) == 1
    saved = torch.load(path, weights_only=False)
    assert saved["hook_state"]["pilot_observations"][0]["example_id"] == "train-a"
    assert saved["trainer_state"]["execution_failures"][-1]["budget_exhausted"]


@pytest.mark.parametrize(
    "status,expected_calls,stage_complete",
    [
        ("initialization-failed", 2, True),
        ("budget-exhausted", 1, False),
        ("accounting-failed", 1, False),
    ],
)
def test_final_test_missing_predictions_remain_in_denominator_and_budget_stops_calls(
    tmp_path, status, expected_calls, stage_complete
):
    from examples.adaptive_multi_agent_collab.schemas import UsageRecord

    trainer, experiment, _, _, _ = _build_offline_experiment()
    asyncio.run(experiment.pilot_safety())
    trainer.state.pilot_complete = True
    asyncio.run(experiment.calibrate_termination())
    trainer.state.calibration_complete, trainer.state.phase = True, "complete"
    trainer.hooks = experiment
    checkpoint = tmp_path / "selected.pt"
    trainer.save_checkpoint(checkpoint)
    seen = []
    factory = experiment.runtime_factory

    def failing_factory(*args):
        runtime = factory(*args)

        async def run(example, **kwargs):
            seen.append(example.example_id)
            return SimpleNamespace(
                example=example,
                final_answer=None,
                sessions=(),
                usage=UsageRecord(),
                call_records=(),
                completed=False,
                status=status,
            )

        runtime.run = run
        return runtime

    experiment.runtime_factory = failing_factory
    record = asyncio.run(experiment.final_test(checkpoint))
    assert len(seen) == expected_calls and len(record.predictions) == 2
    assert record.to_dict()["accuracy_by_run_seed"] == {"19": 0.0, "20": 0.0}
    assert record.to_dict()["stage_complete"] is stage_complete
    assert all(item.answer is None and item.correct is False for item in record.predictions)


def test_shared_calibration_prefixes_reuse_selected_checkpoint_zero_without_regeneration():
    trainer, experiment, models, _, _ = _build_offline_experiment()
    bootstrap = trainer.config.termination
    later = replace(bootstrap, min_sessions=3, max_sessions=3)
    experiment.termination_plan = replace(experiment.termination_plan, candidates=(bootstrap, later))
    asyncio.run(experiment.pilot_safety())
    before = len(models[0].calls)
    calibration = asyncio.run(experiment.calibrate_termination())
    after = len(models[0].calls)
    # Greedy neutral routing chooses no collaboration, leaving one shared initialization per query.
    assert after - before == trainer.config.n_agents * len(experiment.plans["system"].ordered_example_ids)
    assert all(item.stopping_session == 1 for item in calibration.candidates[0].evaluation.examples)
    assert all(item.stopping_session == 3 for item in calibration.candidates[1].evaluation.examples)
    assert all(len(item["boundaries"]) == 3 for item in calibration.shared_executions)
    baseline = asyncio.run(experiment.system_validation())
    assert baseline == calibration.selected_evaluation
    assert len(models[0].calls) == after
    assert baseline.mean_normalized_cost == calibration.selected_evaluation.mean_normalized_cost


@pytest.mark.parametrize(
    "reason",
    [
        "input-token-limit: 4000 > 3072",
        "output-token-cap is outside the backend-supported range",
        "provider output count exceeded declared output-token-limit",
        "backend-context-limit",
        "logical-call-deadline",
        "development monetary budget cannot reserve this request",
        "usage subcategory exceeds its inclusive token total",
        "actual charge exceeded declared maximum; review the resource profile",
    ],
)
def test_pilot_requires_review_for_known_usage_safeguarded_resource_errors(reason):
    from examples.adaptive_multi_agent_collab.learning.calibration import (
        collect_pilot_observation,
    )

    trajectory = SimpleNamespace(
        example=SimpleNamespace(example_id="pilot"),
        sessions=(),
        call_durations=(0.0,),
        completed=True,
        error=None,
        call_records=(
            {
                "logical_call": {"call_type": "role"},
                "error": reason,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "monetary_cost": 0,
                    "paid_monetary_cost": 0,
                },
            },
        ),
    )
    observation = collect_pilot_observation(cast(Trajectory, trajectory))
    assert not observation.usage_missing and observation.successful
    assert observation.resource_limit_hits == (reason,)
    record = calibrate_safety(SafetyCalibrationPlan(("pilot",), 0), (observation,), SafetyConfig())
    assert not record.approved and record.requires_review


def test_pilot_detects_repaired_truncation_from_nested_physical_usage_metadata():
    from examples.adaptive_multi_agent_collab.learning.calibration import (
        collect_pilot_observation,
    )

    trajectory = SimpleNamespace(
        example=SimpleNamespace(example_id="pilot"),
        sessions=(),
        call_durations=(1.0,),
        completed=True,
        error=None,
        call_records=(
            {
                "logical_call": {"call_type": "initial"},
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 2,
                    "monetary_cost": 0,
                    "raw_metadata": {
                        "attempts": [
                            {"finish_reason": "length"},
                            {"finish_reason": "stop"},
                        ]
                    },
                },
            },
        ),
    )
    observation = collect_pilot_observation(cast(Trajectory, trajectory))
    assert observation.resource_limit_hits == ("output-token-limit: finish_reason=length",)
    assert not calibrate_safety(SafetyCalibrationPlan(("pilot",), 0), (observation,), SafetyConfig()).approved
