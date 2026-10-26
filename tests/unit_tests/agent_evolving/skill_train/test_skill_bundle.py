# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.epoch_stages import SkillProgress, save_best_skills, save_skill_version
from openjiuwen.agent_evolving.skill_train.gate import GateTuning, evaluate_gate
from openjiuwen.agent_evolving.skill_train.skill_bundle import (
    DEFAULT_SKILL_NAME,
    format_skill_bundle,
    group_patches_by_skill,
    load_skill_bundle,
    normalize_skills,
)


def test_load_skill_bundle_from_skill_inits(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("skill-a", encoding="utf-8")
    b.write_text("skill-b", encoding="utf-8")
    bundle = load_skill_bundle(skill_inits={"search": str(a), "math": str(b)})
    assert bundle == {"search": "skill-a", "math": "skill-b"}


def test_load_skill_bundle_legacy_skill_init(tmp_path: Path) -> None:
    path = tmp_path / "init.md"
    path.write_text("solo", encoding="utf-8")
    bundle = load_skill_bundle(skill_init=str(path))
    assert bundle == {DEFAULT_SKILL_NAME: "solo"}


def test_format_skill_bundle_multi() -> None:
    text = format_skill_bundle({"alpha": "body-a", "beta": "body-b"})
    assert "## Skill: alpha" in text
    assert "## Skill: beta" in text
    assert "body-a" in text


def test_group_patches_by_skill_multi_drops_unscoped() -> None:
    current = {"alpha": "a", "beta": "b"}
    patches = [
        {"skill_name": "alpha", "source_type": "failure", "patch": {"edits": []}},
        {"source_type": "failure", "patch": {"edits": []}},
        {"skill_name": "beta", "source_type": "success", "patch": {"edits": []}},
    ]
    grouped = group_patches_by_skill(patches, skill_names=current)
    assert len(grouped["alpha"]) == 1
    assert len(grouped["beta"]) == 1


def test_group_patches_singleton_inherits_missing_name() -> None:
    current = {"only": "body"}
    patches = [{"source_type": "failure", "patch": {"edits": []}}]
    grouped = group_patches_by_skill(patches, skill_names=current)
    assert len(grouped["only"]) == 1


def test_adopt_bundle_atomic_gate(tmp_path: Path) -> None:
    progress = SkillProgress(
        current_skills={"a": "old-a", "b": "old-b"},
        current_score=0.4,
        best_skills={"a": "old-a", "b": "old-b"},
        best_score=0.4,
        best_step=0,
    )
    candidate = {"a": "new-a", "b": "new-b"}
    verdict = evaluate_gate(
        format_skill_bundle(candidate),
        0.9,
        progress.gate_state(),
        global_step=3,
        tuning=GateTuning(soft=0.9),
    )
    action = progress.adopt_bundle(candidate, verdict)
    assert action == "accept_new_best"
    assert progress.current_skills == candidate
    assert progress.best_skills == candidate
    assert progress.best_step == 3

    save_skill_version(str(tmp_path), 3, progress.current_skills)
    save_best_skills(str(tmp_path), progress.best_skills)
    assert (tmp_path / "skills" / "a" / "skill_v0003.md").read_text(encoding="utf-8") == "new-a"
    assert (tmp_path / "best_skills" / "b" / "SKILL.md").read_text(encoding="utf-8") == "new-b"


def test_normalize_skills_requires_content() -> None:
    with pytest.raises(ValueError):
        normalize_skills(None)
