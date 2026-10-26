# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-task SkillsBench loop: a new skill continues, a missing rewrite stops."""

from __future__ import annotations

from openjiuwen.agent_evolving.skill_train.envs.skillsbench.per_task_train import (
    _revise,
    _task_base_skill,
    evolve_task,
)


def test_nested_analyst_edits_are_applied(tmp_path) -> None:
    class _Adapter:
        def reflect(self, *args, **kwargs):
            return [
                {
                    "source_type": "failure",
                    "patch": {
                        "reasoning": "add a rule",
                        "edits": [{"op": "append", "content": "Check the schema."}],
                    },
                }
            ]

    revised = _revise(
        _Adapter(),
        {"id": "t"},
        {"id": "t", "hard": 0},
        "# Skill\n",
        tmp_path,
        seed=1,
        edit_budget=4,
        merge_batch_size=8,
        analyst_workers=1,
        update_mode="patch",
    )
    assert revised is not None
    assert "Check the schema." in revised
    assert (tmp_path / "apply_report.json").is_file()


def test_pass_still_continues_on_a_new_skill(tmp_path) -> None:
    calls = []

    def rollout(batch, skill, out_dir):
        calls.append(skill)
        return [{"id": "t", "hard": 1}]

    def revise(item, result, skill, step_dir, step):
        return f"{skill}+{step}"

    record = evolve_task(
        {"id": "t"},
        initial_skill="base",
        iterations=5,
        out_dir=tmp_path,
        rollout=rollout,
        revise=revise,
    )
    assert calls == ["base", "base+1", "base+1+2", "base+1+2+3", "base+1+2+3+4"]
    assert record["stopped_reason"] == "budget"
    assert record["passed_iteration"] == 1
    assert record["n_iterations_run"] == 5
    # Equal hard=1 scores keep the later iteration's skill.
    assert record["best_iteration"] == 5
    assert (tmp_path / "best_skill.md").read_text(encoding="utf-8") == "base+1+2+3+4"
    assert (tmp_path / "final_skill.md").read_text(encoding="utf-8") == "base+1+2+3+4"


def test_pass_without_a_new_skill_continues(tmp_path) -> None:
    calls = []

    def rollout(batch, skill, out_dir):
        calls.append(skill)
        return [{"id": "t", "hard": 1 if skill == "v1" else 0}]

    def revise(item, result, skill, step_dir, step):
        return None if skill == "v1" else "v1"

    record = evolve_task(
        {"id": "t"},
        initial_skill="base",
        iterations=5,
        out_dir=tmp_path,
        rollout=rollout,
        revise=revise,
    )
    assert calls == ["base", "v1", "v1", "v1", "v1"]
    assert record["stopped_reason"] == "budget"
    assert record["n_iterations_run"] == 5
    assert record["passed_iteration"] == 2
    # Equal hard=1 scores on iters 2–5 keep the later iteration.
    assert record["best_iteration"] == 5
    assert (tmp_path / "best_skill.md").read_text(encoding="utf-8") == "v1"
    assert len(record["versions"]) == 1
    assert (tmp_path / "skill_v001.md").read_text(encoding="utf-8") == "v1"
    assert not (tmp_path / "skill_v002.md").exists()


def test_missing_gradient_reruns_the_same_skill(tmp_path) -> None:
    def rollout(batch, skill, out_dir):
        return [{"id": "t", "hard": 0}]

    record = evolve_task(
        {"id": "t"},
        initial_skill="base",
        iterations=5,
        out_dir=tmp_path,
        rollout=rollout,
        revise=lambda *args: None,
    )
    assert record["stopped_reason"] == "budget"
    assert record["n_iterations_run"] == 5
    assert record["versions"] == []


def test_budget_writes_one_version_per_failing_iteration(tmp_path) -> None:
    def rollout(batch, skill, out_dir):
        return [{"id": "t", "hard": 0}]

    def revise(item, result, skill, step_dir, step):
        return f"{skill}+{step}"

    record = evolve_task(
        {"id": "t"},
        initial_skill="base",
        iterations=5,
        out_dir=tmp_path,
        rollout=rollout,
        revise=revise,
    )
    assert record["stopped_reason"] == "budget"
    assert record["n_iterations_run"] == 5
    # Equal hard=0 scores keep the later iteration's skill.
    assert record["best_iteration"] == 5
    assert [p.endswith(f"skill_v00{i}.md") for i, p in enumerate(record["versions"], start=1)] == [True] * 5
    assert (tmp_path / "best_skill.md").read_text(encoding="utf-8") == "base+1+2+3+4"
    assert (tmp_path / "final_skill.md").read_text(encoding="utf-8") == "base+1+2+3+4"


def test_full_rewrite_replaces_the_skill(tmp_path) -> None:
    class _Adapter:
        def reflect(self, *args, **kwargs):
            return [
                {
                    "source_type": "failure",
                    "patch": {
                        "reasoning": "rewrite",
                        "skill_candidates": [
                            {
                                "title": "azure-bgp",
                                "change_summary": ["full document"],
                                "new_skill": "---\nname: azure-bgp\n---\n\n# Azure BGP\n",
                            }
                        ],
                    },
                }
            ]

    revised = _revise(
        _Adapter(),
        {"id": "t"},
        {"id": "t", "hard": 0},
        "# placeholder\n",
        tmp_path,
        seed=1,
        edit_budget=4,
        merge_batch_size=8,
        analyst_workers=1,
        update_mode="full_rewrite_minibatch",
    )
    assert revised == "---\nname: azure-bgp\n---\n\n# Azure BGP\n"
    report = (tmp_path / "apply_report.json").read_text(encoding="utf-8")
    assert "applied_full_rewrite" in report


def test_task_base_skill_reads_bundled_markdown(tmp_path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Original\n", encoding="utf-8")
    text = _task_base_skill({"skills": [{"name": "azure-bgp", "path": str(skill)}]}, "fallback")
    assert text == "# Original\n"
    assert _task_base_skill({"skills": []}, "fallback") == "fallback"


def test_task_base_skills_keeps_named_bundle(tmp_path) -> None:
    from openjiuwen.agent_evolving.skill_train.envs.skillsbench.per_task_train import _task_base_skills

    a = tmp_path / "a" / "SKILL.md"
    b = tmp_path / "b" / "SKILL.md"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("# A\n", encoding="utf-8")
    b.write_text("# B\n", encoding="utf-8")
    bundle = _task_base_skills(
        {
            "skills": [
                {"name": "alpha", "path": str(a)},
                {"name": "beta", "path": str(b)},
            ]
        },
        {"default": "fallback"},
    )
    assert bundle == {"alpha": "# A\n", "beta": "# B\n"}


def test_evolve_task_multi_skill_writes_per_name_versions(tmp_path) -> None:
    calls = []

    def rollout(batch, skill, out_dir):
        calls.append(dict(skill) if isinstance(skill, dict) else skill)
        return [{"id": "t", "hard": 0}]

    def revise(item, result, skill, step_dir, step):
        assert isinstance(skill, dict)
        return {name: f"{body}+{step}" for name, body in skill.items()}

    record = evolve_task(
        {"id": "t"},
        initial_skills={"alpha": "a", "beta": "b"},
        iterations=2,
        out_dir=tmp_path,
        rollout=rollout,
        revise=revise,
    )
    assert calls == [{"alpha": "a", "beta": "b"}, {"alpha": "a+1", "beta": "b+1"}]
    assert (tmp_path / "skills" / "alpha" / "skill_v001.md").read_text(encoding="utf-8") == "a+1"
    # Equal hard=0 scores keep the later iteration's bundle.
    assert (tmp_path / "best_skills" / "beta" / "SKILL.md").read_text(encoding="utf-8") == "b+1"
    assert record["best_iteration"] == 2
    assert record["skill_names"] == ["alpha", "beta"]


def test_revise_attributes_patch_to_named_skill(tmp_path) -> None:
    class _Adapter:
        def reflect(self, *args, **kwargs):
            assert kwargs.get("skills") == {"alpha": "# A\n", "beta": "# B\n"}
            return [
                {
                    "skill_name": "beta",
                    "source_type": "failure",
                    "patch": {
                        "reasoning": "fix beta",
                        "edits": [{"op": "append", "content": "Rule-B"}],
                    },
                }
            ]

    revised = _revise(
        _Adapter(),
        {"id": "t"},
        {"id": "t", "hard": 0},
        {"alpha": "# A\n", "beta": "# B\n"},
        tmp_path,
        seed=1,
        edit_budget=4,
        merge_batch_size=8,
        analyst_workers=1,
        update_mode="patch",
    )
    assert isinstance(revised, dict)
    assert revised["alpha"] == "# A\n"
    assert "Rule-B" in revised["beta"]
    assert (tmp_path / "candidate_skill__beta.md").is_file()

