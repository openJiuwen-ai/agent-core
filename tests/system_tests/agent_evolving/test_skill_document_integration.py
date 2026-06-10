# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for SkillDocumentOptimizer + Trainer + Operator.

Covers end-to-end scenarios that unit tests cannot exercise in isolation:
multi-epoch training, gate rollback, checkpoint resume, slow_update injection,
artifact export, meta_skill context, and state serialization round-trip.

All LLM calls are mocked. The agent and evaluator are also mocked —
only the optimizer, updater, trainer, and operator use real code paths.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.evaluator import BaseEvaluator
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.trainer import Trainer
from openjiuwen.agent_evolving.updater.single_dim import SingleDimUpdater
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_cases(n: int = 4) -> CaseLoader:
    cases = [
        Case(
            inputs={"query": f"question {i}"},
            label={"answer": f"answer {i}"},
            case_id=f"case_{i}",
        )
        for i in range(n)
    ]
    return CaseLoader(cases=cases)


def _reflect_response() -> str:
    """JSON response for failure/success analyst LLM calls."""
    return json.dumps(
        {
            "patch": {
                "edits": [
                    {
                        "op": "append",
                        "content": "\n- Always check edge cases",
                        "target": "skill_content",
                    }
                ],
                "reasoning": "Improve edge case handling",
            },
            "failure_summary": "Missing edge case checks",
        }
    )


def _slow_update_response() -> str:
    """JSON response for slow_update LLM calls."""
    return json.dumps(
        {
            "reasoning": "Regression detected in case_1",
            "slow_update_content": "Focus on regression patterns in edge cases",
            "action": "update",
        }
    )


def _meta_skill_response() -> str:
    """JSON response for meta_skill LLM calls."""
    return json.dumps({"meta_skill_content": "Prioritize error handling patterns based on case failures"})


def _make_llm_mock():
    """Create LLM mock that dispatches responses based on prompt keywords.

    Each prompt template has a unique system message prefix:
    - slow_update: "strategic skill advisor"
    - meta_skill: "optimizer-coach"
    - analyst_error/success: "failure-analysis" / "success-pattern"
    - merge_*: "skill-edit coordinator"
    - ranking: "RANK the edits"
    """
    llm = MagicMock()

    async def mock_invoke(*, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        if "strategic skill advisor" in prompt:
            content = _slow_update_response()
        elif "optimizer-coach" in prompt:
            content = _meta_skill_response()
        else:
            # Reflect / aggregate / select all use the same response shape
            content = _reflect_response()

        response = MagicMock()
        response.content = content
        return response

    llm.invoke = mock_invoke
    return llm


def _make_evaluator(score: float = 0.5) -> BaseEvaluator:
    """Create mock evaluator returning consistent scores."""
    evaluator = MagicMock(spec=BaseEvaluator)

    def batch_eval(cases, predicts, num_parallel=1):
        return [EvaluatedCase(case=c, answer=p, score=score, reason="mock eval") for c, p in zip(cases, predicts)]

    evaluator.batch_evaluate = MagicMock(side_effect=batch_eval)
    return evaluator


def _make_agent():
    """Create mock agent that returns simple output on invoke."""
    agent = MagicMock()

    async def mock_invoke(inputs, session=None):
        return {"output": f"answer for {inputs.get('query', 'unknown')}"}

    agent.invoke = mock_invoke
    return agent


def _make_optimizer(
    train_cases: CaseLoader,
    *,
    use_slow_update: bool = True,
    use_meta_skill: bool = True,
    artifact_dir: str | None = None,
    **overrides,
) -> SkillDocumentOptimizer:
    """Create SkillDocumentOptimizer with small hyperparameters for fast testing."""
    defaults = dict(
        agent=_make_agent(),
        evaluator=_make_evaluator(score=0.5),
        llm=_make_llm_mock(),
        model="test-model",
        train_cases=train_cases,
        batch_size=2,
        accumulation=1,
        steps_per_epoch=1,
        minibatch_size=2,
        edit_budget=5,
        score_threshold=0.5,
        parallelism=2,
        use_slow_update=use_slow_update,
        use_meta_skill=use_meta_skill,
        artifact_dir=artifact_dir,
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


# ── Multi-epoch training ─────────────────────────────────────────────────


class TestMultiEpochTraining:
    @staticmethod
    def test_two_epochs_complete_successfully():
        """Trainer runs 2 epochs with SkillDocumentOptimizer end-to-end."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Test Skill\n\nInitial content.")
        opt.bind(operators={op.operator_id: op})

        updater = SingleDimUpdater(optimizer=opt)
        evaluator = _make_evaluator(score=0.5)
        trainer = Trainer(updater=updater, evaluator=evaluator, early_stop_score=1.0)

        agent = _make_agent()
        agent.get_operators = MagicMock(return_value={op.operator_id: op})

        result = trainer.train(
            agent=agent,
            train_cases=cases,
            val_cases=cases,
            num_iterations=2,
        )
        assert result is agent

    @staticmethod
    def test_optimizer_state_advances_after_epochs():
        """After 2 epochs, global_step should have advanced."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Test Skill")
        opt.bind(operators={op.operator_id: op})

        updater = SingleDimUpdater(optimizer=opt)
        evaluator = _make_evaluator(score=0.5)
        trainer = Trainer(updater=updater, evaluator=evaluator, early_stop_score=1.0)

        agent = _make_agent()
        agent.get_operators = MagicMock(return_value={op.operator_id: op})

        trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=2)

        state = opt.get_state()
        assert state["global_step"] >= 2


# ── Gate rollback ─────────────────────────────────────────────────────────


class TestGateRollback:
    @staticmethod
    def test_base_selected_when_candidate_is_worse():
        """When candidate skill scores worse, Trainer keeps base."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Original Skill\n\nGood content.")
        opt.bind(operators={op.operator_id: op})

        updater = SingleDimUpdater(optimizer=opt)

        # Evaluator returns different scores depending on skill content
        call_count = {"n": 0}

        def smart_eval(case_list, predicts, num_parallel=1):
            call_count["n"] += 1
            # Check current operator skill — if it contains the edit marker, score lower
            current_skill = op.get_state().get("skill_content", "")
            score = 0.3 if "Always check edge cases" in current_skill else 0.8
            return [
                EvaluatedCase(case=c, answer=p, score=score, reason="gate test") for c, p in zip(case_list, predicts)
            ]

        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.batch_evaluate = MagicMock(side_effect=smart_eval)

        trainer = Trainer(updater=updater, evaluator=evaluator, early_stop_score=1.0)

        agent = _make_agent()
        agent.get_operators = MagicMock(return_value={op.operator_id: op})

        trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

        # Base skill should be restored (without the edit marker)
        final_skill = op.get_state()["skill_content"]
        assert "Always check edge cases" not in final_skill

    @staticmethod
    def test_candidate_selected_when_better():
        """When candidate skill scores better, Trainer commits candidate."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Original Skill")
        opt.bind(operators={op.operator_id: op})

        updater = SingleDimUpdater(optimizer=opt)

        def smart_eval(case_list, predicts, num_parallel=1):
            current_skill = op.get_state().get("skill_content", "")
            score = 0.9 if "Always check edge cases" in current_skill else 0.3
            return [
                EvaluatedCase(case=c, answer=p, score=score, reason="gate test") for c, p in zip(case_list, predicts)
            ]

        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.batch_evaluate = MagicMock(side_effect=smart_eval)

        trainer = Trainer(updater=updater, evaluator=evaluator, early_stop_score=1.0)

        agent = _make_agent()
        agent.get_operators = MagicMock(return_value={op.operator_id: op})

        trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

        # Candidate should be committed (with the edit marker)
        final_skill = op.get_state()["skill_content"]
        assert "Always check edge cases" in final_skill


# ── Checkpoint resume ────────────────────────────────────────────────────


class TestCheckpointResume:
    @staticmethod
    def test_save_and_resume_restores_global_step(tmp_path):
        """Checkpoint saves and resumes global_step correctly."""
        ckpt_dir = str(tmp_path / "checkpoints")
        cases = _make_cases(4)

        # Phase 1: train 1 epoch, save checkpoint
        opt1 = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op1 = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt1.bind(operators={op1.operator_id: op1})

        updater1 = SingleDimUpdater(optimizer=opt1)
        evaluator1 = _make_evaluator(score=0.5)
        trainer1 = Trainer(
            updater=updater1,
            evaluator=evaluator1,
            checkpoint_dir=ckpt_dir,
            checkpoint_every_n_epochs=1,
            early_stop_score=1.0,
        )

        agent1 = _make_agent()
        agent1.get_operators = MagicMock(return_value={op1.operator_id: op1})

        trainer1.train(agent=agent1, train_cases=cases, val_cases=cases, num_iterations=1)
        state_after_epoch_1 = opt1.get_state()
        assert state_after_epoch_1["global_step"] >= 1

        # Find the checkpoint file
        ckpt_files = list(Path(ckpt_dir).glob("*.json"))
        assert len(ckpt_files) >= 1

        # Phase 2: resume from checkpoint
        opt2 = _make_optimizer(cases, use_slow_update=False, use_meta_skill=False)
        op2 = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt2.bind(operators={op2.operator_id: op2})

        updater2 = SingleDimUpdater(optimizer=opt2)
        evaluator2 = _make_evaluator(score=0.5)
        trainer2 = Trainer(
            updater=updater2,
            evaluator=evaluator2,
            checkpoint_dir=ckpt_dir,
            resume_from=str(ckpt_files[0]),
            early_stop_score=1.0,
        )

        agent2 = _make_agent()
        agent2.get_operators = MagicMock(return_value={op2.operator_id: op2})

        trainer2.train(agent=agent2, train_cases=cases, val_cases=cases, num_iterations=3)

        # After resume + 2 more epochs (3 - 1 resumed), global_step should advance further
        final_state = opt2.get_state()
        assert final_state["global_step"] >= state_after_epoch_1["global_step"]


# ── Slow update ───────────────────────────────────────────────────────────


class TestSlowUpdate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_slow_update_markers_injected_after_epoch_end():
        """After run_epoch_end with slow_update enabled, markers appear in skill."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=True, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Skill\n\nContent.")
        opt.bind(operators={op.operator_id: op})

        opt._current_skill_content = "# Skill v2\n\nContent."
        opt._prev_epoch_skill = "# Skill v1\n\nContent."
        opt._prev_epoch_comparison = [{"case_id": "c1", "curr_score": 0.8}]
        opt._curr_epoch_comparison = [{"case_id": "c1", "curr_score": 0.3}]

        await opt.run_epoch_end(epoch=1)

        assert "<!-- SLOW_UPDATE_START -->" in opt._current_skill_content
        assert "Focus on regression" in opt._current_skill_content

    @staticmethod
    @pytest.mark.asyncio
    async def test_slow_update_skipped_on_first_epoch():
        """epoch < 1 skips slow_update entirely."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=True, use_meta_skill=False)
        op = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt.bind(operators={op.operator_id: op})

        skill_before = "# Skill\n\nContent."
        op.set_parameter("skill_content", skill_before)
        opt._current_skill_content = skill_before

        await opt.run_epoch_end(epoch=0)

        # No markers injected (epoch 0 skips slow_update)
        assert "<!-- SLOW_UPDATE_START -->" not in opt._current_skill_content
        assert opt._current_skill_content == skill_before


# ── Meta skill ────────────────────────────────────────────────────────────


class TestMetaSkill:
    @staticmethod
    @pytest.mark.asyncio
    async def test_meta_skill_context_updated_after_epoch():
        """After run_epoch_end, _meta_skill_context contains LLM response."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=False, use_meta_skill=True)
        op = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt.bind(operators={op.operator_id: op})

        opt._current_skill_content = "# Skill v2"
        opt._prev_epoch_skill = "# Skill v1"
        opt._prev_epoch_comparison = [{"case_id": "c1", "curr_score": 0.8}]
        opt._curr_epoch_comparison = [{"case_id": "c1", "curr_score": 0.4}]

        await opt.run_epoch_end(epoch=1)

        assert "error handling" in opt._meta_skill_context.lower() or len(opt._meta_skill_context) > 0


# ── Artifact export ───────────────────────────────────────────────────────


class TestArtifactExport:
    @staticmethod
    def test_artifact_directory_structure_correct(tmp_path):
        """ArtifactExporter creates expected directory structure."""
        from openjiuwen.agent_evolving.optimizer.skill_document.artifact_exporter import (
            ArtifactExporter,
        )

        output_dir = str(tmp_path / "artifacts")
        exporter = ArtifactExporter(output_dir)

        # Export some artifacts
        exporter.export_skill_snapshot(0, 0, "# Skill before", tag="before")
        exporter.export_skill_snapshot(0, 0, "# Skill after", tag="after")
        exporter.export_skill_diff(0, 0, "# Skill before", "# Skill after")
        exporter.export_metrics(0, 0, {"n_edits": 3, "score": 0.5})
        exporter.export_gate_result(0, 0.5, 0.7, "candidate")

        # Skill snapshots are at epoch level
        epoch_dir = Path(output_dir) / "epoch_0"
        assert epoch_dir.exists()
        assert (epoch_dir / "skill_before.md").exists()
        assert (epoch_dir / "skill_after.md").exists()
        assert (epoch_dir / "gate_result.json").exists()

        # Step-level artifacts are under step_N
        step_dir = epoch_dir / "step_0"
        assert step_dir.exists()
        assert (step_dir / "metrics.json").exists()
        assert (step_dir / "applied_diff.patch").exists()

    @staticmethod
    def test_skill_snapshots_written(tmp_path):
        """Skill before/after snapshots contain correct content."""
        from openjiuwen.agent_evolving.optimizer.skill_document.artifact_exporter import (
            ArtifactExporter,
        )

        output_dir = str(tmp_path / "artifacts")
        exporter = ArtifactExporter(output_dir)

        exporter.export_skill_snapshot(0, 0, "# Original Skill", tag="before")
        exporter.export_skill_snapshot(0, 0, "# Updated Skill", tag="after")

        # Skill snapshots are at epoch level, not step level
        epoch_dir = Path(output_dir) / "epoch_0"
        before_path = epoch_dir / "skill_before.md"
        after_path = epoch_dir / "skill_after.md"
        assert before_path.read_text() == "# Original Skill"
        assert after_path.read_text() == "# Updated Skill"


# ── State round-trip ─────────────────────────────────────────────────────


class TestStateRoundTrip:
    @staticmethod
    def test_get_state_load_state_preserves_fields():
        """get_state → load_state round-trip preserves all serialized fields."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases, use_slow_update=True, use_meta_skill=True)
        op = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt.bind(operators={op.operator_id: op})

        # Set some state
        opt._global_step = 5
        opt._step_buffer = [{"step": 3, "n_edits": 2, "failure_patterns": [], "rejected_edits": []}]
        opt._meta_skill_context = "Focus on error patterns"
        opt._prev_epoch_skill = "# Previous skill"
        opt._prev_epoch_comparison = [
            {"case_id": "c1", "curr_score": 0.8, "curr_reason": "good"},
        ]

        state = opt.get_state()

        # Create a fresh optimizer and load the state
        opt2 = _make_optimizer(cases, use_slow_update=True, use_meta_skill=True)
        op2 = SkillDocumentOperator("test_skill2", initial_content="# Skill 2")
        opt2.bind(operators={op2.operator_id: op2})

        opt2.load_state(state)

        assert opt2._global_step == 5
        assert len(opt2._step_buffer) == 1
        assert opt2._meta_skill_context == "Focus on error patterns"
        assert opt2._prev_epoch_skill == "# Previous skill"
        assert len(opt2._prev_epoch_comparison) == 1

    @staticmethod
    def test_state_roundtrip_through_json():
        """State survives JSON serialization/deserialization."""
        cases = _make_cases(4)
        opt = _make_optimizer(cases)
        op = SkillDocumentOperator("test_skill", initial_content="# Skill")
        opt.bind(operators={op.operator_id: op})

        opt._global_step = 3
        opt._meta_skill_context = "test context"
        opt._prev_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.6, "curr_reason": "partial"},
        ]

        state = opt.get_state()
        # Round-trip through JSON (as checkpoint would)
        json_str = json.dumps(state, ensure_ascii=False)
        restored = json.loads(json_str)

        opt2 = _make_optimizer(cases)
        op2 = SkillDocumentOperator("test_skill2", initial_content="# Skill 2")
        opt2.bind(operators={op2.operator_id: op2})

        opt2.load_state(restored)

        assert opt2._global_step == 3
        assert opt2._meta_skill_context == "test context"
        assert opt2._prev_epoch_comparison[0]["case_id"] == "c0"


# ── Regression: backward compat ──────────────────────────────────────────


class TestBackwardCompat:
    @staticmethod
    def test_base_optimizer_get_state_returns_empty():
        """BaseOptimizer.get_state() still returns {} (backward compat)."""
        from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer

        class PlainOptimizer(BaseOptimizer):
            domain = "plain"

            async def _backward(self, signals):
                pass

            def _step(self):
                return {}

        assert PlainOptimizer().get_state() == {}

    @staticmethod
    def test_single_dim_updater_with_plain_optimizer():
        """SingleDimUpdater with plain BaseOptimizer subclass returns {} state."""
        from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer

        class PlainOptimizer(BaseOptimizer):
            domain = "plain"

            async def _backward(self, signals):
                pass

            def _step(self):
                return {}

        updater = SingleDimUpdater(optimizer=PlainOptimizer())
        assert updater.get_state() == {}
        updater.load_state({"some": "data"})
        assert updater.get_state() == {}
