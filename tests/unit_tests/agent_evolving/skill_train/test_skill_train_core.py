# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.benchmarks.parity_runner import run_parity_report
from openjiuwen.agent_evolving.skill_train.envs.searchqa.evaluator import evaluate, exact_match, f1_score
from openjiuwen.agent_evolving.skill_train.gate import evaluate_gate, select_gate_score
from openjiuwen.agent_evolving.skill_train.skill_patch import apply_patch


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "skill_train" / "searchqa" / "evaluator_fixtures.json"


class TestSearchQAEvaluator:
    def test_exact_match(self):
        assert exact_match("Paris", ["Paris"]) == 1.0
        assert exact_match("paris", ["Paris"]) == 1.0
        assert exact_match("London", ["Paris"]) == 0.0

    def test_f1_score(self):
        assert f1_score("new york city", ["new york"]) > 0.5

    def test_evaluate_extracts_answer_tags(self):
        result = evaluate("reasoning\n<answer>42</answer>", ["42"])
        assert result["em"] == 1.0
        assert result["predicted_answer"] == "42"


class TestGate:
    def test_accept_new_best(self):
        gate = evaluate_gate(
            "candidate",
            cand_hard=0.8,
            current_skill="current",
            current_score=0.5,
            best_skill="best",
            best_score=0.6,
            best_step=1,
            global_step=2,
        )
        assert gate.action == "accept_new_best"
        assert gate.best_score == pytest.approx(select_gate_score(0.8, 0.0))

    def test_reject(self):
        gate = evaluate_gate(
            "candidate",
            cand_hard=0.3,
            current_skill="current",
            current_score=0.5,
            best_skill="best",
            best_score=0.6,
            best_step=1,
            global_step=2,
        )
        assert gate.action == "reject"


class TestSkillPatch:
    def test_append_edit(self):
        skill = "# Title\n\nBody"
        updated = apply_patch(skill, {"edits": [{"op": "append", "content": "New rule"}]})
        assert "New rule" in updated

    def test_protected_region_skipped(self):
        skill = "Before\n<!-- SLOW_UPDATE_START -->\nprotected\n<!-- SLOW_UPDATE_END -->"
        updated = apply_patch(
            skill,
            {"edits": [{"op": "replace", "target": "protected", "content": "hack"}]},
        )
        assert "hack" not in updated


class TestParityRunner:
    def test_searchqa_evaluator_parity(self):
        report = run_parity_report("evaluator", FIXTURES)
        if report.get("skipped"):
            pytest.skip(report.get("reason", "SkillOpt not available"))
        assert report["passed"], report.get("mismatches")


class TestEnvRegistry:
    def test_all_six_envs_registered(self):
        from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter, list_env_adapters

        expected = {
            "searchqa",
            "docvqa",
            "alfworld",
            "officeqa",
            "spreadsheetbench",
            "livemathematicianbench",
        }
        registered = set(list_env_adapters())
        assert expected <= registered, f"missing: {expected - registered}"

        for name in expected:
            adapter = get_env_adapter(name)
            assert adapter is not None
            assert hasattr(adapter, "rollout")
            assert hasattr(adapter, "get_task_types")


class TestReasoningEffort:
    def test_set_get_reasoning_effort(self):
        from openjiuwen.agent_evolving.skill_train.model_compat import (
            get_reasoning_effort,
            set_reasoning_effort,
        )

        set_reasoning_effort("medium")
        assert get_reasoning_effort() == "medium"
        set_reasoning_effort("")
        assert get_reasoning_effort() is None
        set_reasoning_effort(None)
        assert get_reasoning_effort() is None
        set_reasoning_effort("medium")


class TestLlmInvokePolicy:
    def test_make_policy_and_timeout_override(self):
        from openjiuwen.agent_evolving.skill_train.llm_client import (
            ChatLLMClient,
            make_llm_invoke_policy,
        )

        policy = make_llm_invoke_policy(
            attempt_timeout_secs=300,
            total_budget_secs=200,  # raised to >= attempt
            max_attempts=5,
        )
        assert policy.attempt_timeout_secs == 300.0
        assert policy.total_budget_secs == 300.0
        assert policy.max_attempts == 5

        client = ChatLLMClient(llm=object(), model="m", policy=policy)  # type: ignore[arg-type]
        overridden = client._policy(retries=3, timeout=180)
        assert overridden.attempt_timeout_secs == 300.0  # policy floor wins over smaller timeout
        boosted = client._policy(retries=3, timeout=400)
        assert boosted.attempt_timeout_secs == 400.0
        assert boosted.total_budget_secs >= 400.0 * 3


class TestSlowUpdateField:
    def test_inject_replace_extract(self):
        from openjiuwen.agent_evolving.skill_train.slow_update import (
            extract_slow_update_field,
            has_slow_update_field,
            inject_empty_slow_update_field,
            replace_slow_update_field,
        )

        skill = "# Skill\n\nBody"
        with_field = inject_empty_slow_update_field(skill)
        assert has_slow_update_field(with_field)
        assert extract_slow_update_field(with_field) == ""

        updated = replace_slow_update_field(with_field, "Keep regressions in check")
        assert extract_slow_update_field(updated) == "Keep regressions in check"

        # Step-level patch must not touch protected region
        patched = apply_patch(
            updated,
            {
                "edits": [
                    {
                        "op": "replace",
                        "target": "Keep regressions in check",
                        "content": "hacked",
                    }
                ]
            },
        )
        assert "hacked" not in patched
        assert extract_slow_update_field(patched) == "Keep regressions in check"

    def test_format_comparison_categories(self):
        from openjiuwen.agent_evolving.skill_train.slow_update import (
            build_comparison_pairs,
            format_comparison_text,
        )

        items = [{"id": "1", "question": "Q1"}, {"id": "2", "question": "Q2"}]
        prev = [
            {"id": "1", "hard": 1, "soft": 1.0, "predicted_answer": "a"},
            {"id": "2", "hard": 0, "soft": 0.0, "predicted_answer": "b"},
        ]
        curr = [
            {"id": "1", "hard": 0, "soft": 0.0, "predicted_answer": "x"},
            {"id": "2", "hard": 1, "soft": 1.0, "predicted_answer": "b"},
        ]
        pairs = build_comparison_pairs(prev, curr, items)
        cats = {p["id"]: p["category"] for p in pairs}
        assert cats["1"] == "regressed"
        assert cats["2"] == "improved"
        text = format_comparison_text(pairs)
        assert "Regressions" in text
        assert "Improvements" in text


class TestMetaSkill:
    def test_format_meta_skill_context(self):
        from openjiuwen.agent_evolving.skill_train.meta_skill import format_meta_skill_context

        assert format_meta_skill_context("") == ""
        block = format_meta_skill_context("Prefer concrete edits")
        assert block.startswith("## Optimizer Meta Skill")
        assert "Prefer concrete edits" in block

    def test_load_save_meta_skill(self, tmp_path):
        from openjiuwen.agent_evolving.skill_train.meta_skill import (
            load_meta_skill_content,
            save_meta_skill_result,
        )

        assert load_meta_skill_content(str(tmp_path), 0) == ""
        save_meta_skill_result(
            str(tmp_path),
            2,
            {"meta_skill_content": "memory-v2", "action": "write_meta_skill"},
        )
        assert load_meta_skill_content(str(tmp_path), 2) == "memory-v2"


class TestConfigDefaults:
    def test_skillopt_base_defaults(self):
        from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig

        cfg = SkillTrainConfig()
        flat = cfg.to_trainer_cfg()
        assert flat["use_slow_update"] is True
        assert flat["slow_update_samples"] == 20
        assert flat["slow_update_gate_with_selection"] is False
        assert flat["longitudinal_pair_policy"] == "mixed"
        assert flat["use_meta_skill"] is True
        assert flat["reasoning_effort"] == "medium"


class TestEpochEndHooks:
    def test_epoch1_placeholder_and_meta_skip(self, tmp_path, monkeypatch):
        from openjiuwen.agent_evolving.skill_train.trainer import SkillReflACTTrainer
        from openjiuwen.agent_evolving.skill_train.slow_update import has_slow_update_field
        from openjiuwen.agent_evolving.skill_train.state import load_json

        trainer = SkillReflACTTrainer.__new__(SkillReflACTTrainer)
        out_root = str(tmp_path)
        current = "# Skill\nbody"
        current, *_rest, pairs = trainer._run_epoch_slow_update(
            adapter=None,
            dataloader=None,
            cfg={},
            out_root=out_root,
            epoch=0,
            display_epoch=1,
            history=[],
            current_skill=current,
            current_score=0.5,
            best_skill=current,
            best_score=0.5,
            best_step=0,
            global_step=1,
            seed=42,
            slow_n=20,
            longitudinal_pair_policy="mixed",
            slow_gate_with_selection=False,
            gate_metric="hard",
            gate_mixed_weight=0.5,
            sel_env=None,
            sel_cache={},
        )
        assert has_slow_update_field(current)
        assert pairs is None
        slow_result = load_json(tmp_path / "slow_update" / "epoch_01" / "slow_result.json")
        assert slow_result["action"] == "inject_placeholder"

        pairs2 = trainer._run_epoch_meta_skill(
            adapter=None,
            dataloader=None,
            out_root=out_root,
            epoch=0,
            display_epoch=1,
            history=[],
            epoch_last_step_skill=current,
            epoch_comparison_pairs=None,
            seed=42,
            slow_n=20,
            longitudinal_pair_policy="mixed",
        )
        assert pairs2 is None
        meta = load_json(tmp_path / "meta_skill" / "epoch_01" / "meta_skill_result.json")
        assert meta["action"] == "skip_first_epoch"
