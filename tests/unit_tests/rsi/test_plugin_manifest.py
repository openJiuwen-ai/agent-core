# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native Plugin input/output parity for RSI's editable registries."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from openjiuwen.harness.resources import find_plugin_manifest, load_plugin_package
from openjiuwen.rsi.harness_rsi.member_optimizer.plugin_manifest import synchronize_plugin_manifest
from openjiuwen.rsi.harness_rsi.member_optimizer.verification import _check_skills_manifest, _load_harness_plugin
from openjiuwen.rsi.harness_rsi.member_optimizer.worktree_coordinator import MemberWorktreeCoordinator
from openjiuwen.rsi.harness_rsi.member_optimizer.action_executor import _sync_skill_registry_for_written_files


def _package(root: Path) -> Path:
    root.mkdir()
    (root / "tools").mkdir()
    (root / "tools" / "existing.py").write_text("# Existing tool\n", encoding="utf-8")
    (root / "skills" / "existing").mkdir(parents=True)
    (root / "skills" / "existing" / "SKILL.md").write_text(
        "---\nname: existing\ndescription: Existing skill\n---\nCheck the result.\n", encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": "plugin-fixture",
                "package_type": "plugin",
                "version": "1.0.0",
                "display_name": {"en": "Keep me"},
                "tools": [{"file": "tools/existing.py", "class": "ExistingTool"}],
                "skills": [{"dir": "skills/existing", "mode": "auto_list"}],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_json_plugin_edits_reach_native_loader_and_survive_relocation(tmp_path):
    source = _package(tmp_path / "source")
    original = (source / "manifest.json").read_bytes()
    work = MemberWorktreeCoordinator.prepare_integration_worktree("solver", str(source), str(tmp_path / "wt"))
    before = load_plugin_package(find_plugin_manifest(work))
    assert len(before.tools) == len(before.skills) == 1
    assert before.skills[0].mode == "auto_list"
    assert all(check.status == "passed" for check in _check_skills_manifest("solver", work))
    (work / "prompt_sections" / "files").mkdir()
    (work / "prompt_sections" / "files" / "lesson.md").write_text("New instruction", encoding="utf-8")
    (work / "prompt_sections" / "sections.yaml").write_text(
        "sections:\n- name: lesson\n  file: lesson.md\n", encoding="utf-8"
    )
    (work / "rails" / "new.py").write_text("# New rail\n", encoding="utf-8")
    (work / "rails" / "rails.yaml").write_text(
        "rails:\n- file: rails/new.py\n  class_name: NewRail\n", encoding="utf-8"
    )
    (work / "skills" / "new").mkdir()
    (work / "skills" / "new" / "SKILL.md").write_text(
        "---\nname: new\ndescription: New skill\n---\nVerify the output.\n", encoding="utf-8"
    )
    _sync_skill_registry_for_written_files(
        action_worktree=work,
        action=SimpleNamespace(action_group="skill"),
        declared_paths=["skills/skills.yaml", "skills/new/SKILL.md"],
        written_files=["skills/new/SKILL.md"],
    )
    after = _load_harness_plugin(work)
    assert len(after.tools) == 1
    assert len(after.rails) == 1
    assert len(after.skills) == 2
    assert after.skills[0].mode == "auto_list"
    assert after.prompt_sections[0].content["en"] == "New instruction"
    assert all(check.status == "passed" for check in _check_skills_manifest("solver", work))
    (work / "tools" / "tools.yaml").write_text("tools: []\n", encoding="utf-8")
    synchronize_plugin_manifest(work)
    (work / "tools" / "existing.py").unlink()
    relocated = tmp_path / "published"
    shutil.copytree(work, relocated)
    final = load_plugin_package(find_plugin_manifest(relocated))
    assert not final.tools
    assert len(final.skills) == 2
    assert Path(final.rails[0].params["file_path"]).is_relative_to(relocated)
    assert json.loads((relocated / "manifest.json").read_text())["display_name"] == {"en": "Keep me"}
    assert (source / "manifest.json").read_bytes() == original
    assert all(check.status == "passed" for check in _check_skills_manifest("solver", relocated))
    second = MemberWorktreeCoordinator.prepare_integration_worktree("solver", str(relocated), str(tmp_path / "wt2"))
    assert len(_load_harness_plugin(second).skills) == 2


def test_native_yaml_baseline_gains_registered_prompt_without_changing_h0(tmp_path):
    source = tmp_path / "native"
    source.mkdir()
    manifest = source / "harness_config.yaml"
    original = (
        "schema_version: expert_harness.v1\nid: native\nprompt_sections:\n- name: old\n  content: {en: Old policy}\n"
    )
    manifest.write_text(original, encoding="utf-8")
    work = MemberWorktreeCoordinator.prepare_integration_worktree("solver", str(source), str(tmp_path / "wt"))
    (work / "prompt_sections" / "sections.yaml").write_text(
        "sections:\n- name: new\n  content: New policy\n", encoding="utf-8"
    )
    plugin = _load_harness_plugin(work)
    assert len(plugin.prompt_sections) == 1
    assert plugin.prompt_sections[0].content["en"] == "New policy"
    assert manifest.read_text(encoding="utf-8") == original


def test_malformed_plugin_registry_is_not_silently_dropped(tmp_path):
    package = _package(tmp_path / "plugin")
    (package / "tools" / "tools.yaml").write_text("tools: wrong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list"):
        _load_harness_plugin(package)


@pytest.mark.parametrize(
    "entry",
    [
        "skills/existing",
        {"dir": "skills/existing", "mode": "all"},
        {"dir": "skills/existing", "mode": "auto_list", "enabled_skills": ["existing"]},
        {"dir": "skills", "mode": "all"},
    ],
)
def test_skill_registry_accepts_native_and_legacy_mounts_without_mutation(tmp_path, entry):
    package = _package(tmp_path / "plugin")
    registry = package / "skills" / "skills.yaml"
    registry.write_text(yaml.safe_dump({"skills": [entry]}), encoding="utf-8")
    original = registry.read_bytes()

    checks = _check_skills_manifest("solver", package)

    assert len(checks) == 2
    assert all(check.status == "passed" for check in checks)
    assert any(check.name.startswith("skill_safety:") for check in checks)
    assert registry.read_bytes() == original


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"mode": "all"},
        {"dir": None},
        {"dir": 123},
        {"dir": ""},
        {"dir": "   "},
        {"dir": "../outside"},
        {"dir": "skills/missing"},
        {"dir": "skills/existing/SKILL.md"},
    ],
)
def test_skill_registry_rejects_invalid_native_mounts(tmp_path, entry):
    package = _package(tmp_path / "plugin")
    (package / "skills" / "skills.yaml").write_text(yaml.safe_dump({"skills": [entry]}), encoding="utf-8")

    checks = _check_skills_manifest("solver", package)

    assert len(checks) == 1
    assert checks[0].name == "skill_ref:solver:0"
    assert checks[0].status == "failed"


def test_skill_registry_still_rejects_absolute_native_mounts(tmp_path):
    package = _package(tmp_path / "plugin")
    entry = {"dir": str((package / "skills" / "existing").resolve()), "mode": "all"}
    (package / "skills" / "skills.yaml").write_text(yaml.safe_dump({"skills": [entry]}), encoding="utf-8")

    checks = _check_skills_manifest("solver", package)

    assert checks[0].status == "failed"
    assert "must be relative" in checks[0].error


def test_skill_registry_keeps_safety_checks_for_native_mounts(tmp_path):
    package = _package(tmp_path / "plugin")
    (package / "skills" / "existing" / "unsafe.py").write_text("import subprocess\n", encoding="utf-8")
    (package / "skills" / "skills.yaml").write_text(
        "skills:\n- dir: skills/existing\n  mode: all\n", encoding="utf-8"
    )

    checks = _check_skills_manifest("solver", package)

    assert checks[0].status == "passed"
    assert checks[1].name.startswith("skill_safety:")
    assert checks[1].status == "failed"
    assert "dangerous import" in checks[1].error
