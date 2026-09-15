# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Epoch filtering must produce the same capabilities for the native loader."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from openjiuwen.harness.resources import find_plugin_manifest, load_plugin_package
from openjiuwen.rsi.harness_rsi.member_optimizer.plugin_manifest import prepare_plugin_registries
from openjiuwen.rsi.harness_rsi.single_harness.iterative import _materialize_checkpoint_filtered_harness


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(package, payload):
    _write(package / "manifest.json", json.dumps(payload))


def _refs(package):
    path = package.parent / f"{package.name}_refs.yaml"
    _write(path, yaml.safe_dump({"harness_refs": {"solver": str(package)}}))
    return str(path)


def _package(path):
    payload = {
        "package_type": "plugin", "id": "checkpoint-fixture", "version": "1.0.0",
        "display_name": {"en": "Preserved display name"}, "metadata": {"tag": "preserved"},
        "prompt_sections": [{"file": "prompt_sections/identity.md"}, {"file": "prompt_sections/soul.md"}],
        "skills": [{"dir": "skills/base", "mode": "auto_list"}],
        "tools": [{"file": "tools/base.py", "class": "BaseTool"}],
        "rails": [{"file": "rails/base.py", "class": "BaseRail"}],
    }
    _write(path / "prompt_sections/identity.md", "Original identity")
    _write(path / "prompt_sections/soul.md", "Original policy")
    _write(path / "skills/base/SKILL.md", "---\nname: base\ndescription: Baseline\n---\nCheck results.")
    _write(path / "tools/base.py", "# Existing tool\n")
    _write(path / "rails/base.py", "# Existing rail\n")
    _manifest(path, payload)
    return payload


def _gate(package, action_group, target, name="", operation="add"):
    return {
        "candidate_harness_refs_path": _refs(package),
        "capabilities": [{"role": "solver", "action_group": action_group,
                          "target_path": target, "runtime_name": name, "operation": operation}],
    }


def _filter(tmp_path, base, candidate, gates):
    result = _materialize_checkpoint_filtered_harness(
        output_dir=tmp_path / "run", epoch=1, base_harness_refs_path=_refs(base),
        replayed_harness_refs_path=_refs(candidate), retained_gates=gates,
        removed_gates=[], full_eval_ref_path=str(tmp_path / "full_eval_ref.yaml"),
    )
    return Path(yaml.safe_load(Path(result).read_text(encoding="utf-8"))["harness_refs"]["solver"])


def _snapshot(package):
    return {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()}


@pytest.mark.parametrize("with_sidecars", [False, True])
def test_filtered_native_prompt_preserves_h0_and_excludes_rejected_sibling(tmp_path, with_sidecars):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    original = _package(base)
    shutil.copytree(base, candidate)
    payload = json.loads(json.dumps(original))
    for name in ("rejected", "retained"):
        _write(candidate / f"prompt_sections/files/{name}.md", f"{name} instruction")
        payload["prompt_sections"].append({"name": name, "file": f"prompt_sections/files/{name}.md"})
    _manifest(candidate, payload)
    if with_sidecars:
        prepare_plugin_registries(candidate)
    before = (_snapshot(base), _snapshot(candidate))

    selected = _filter(tmp_path, base, candidate, [
        _gate(candidate, "prompt", "prompt_sections/files/retained.md", "retained"),
    ])
    relocated = tmp_path / "installed"
    shutil.copytree(selected, relocated)
    plugin = load_plugin_package(find_plugin_manifest(relocated))

    assert [section.name for section in plugin.prompt_sections] == ["identity", "soul", "retained"]
    assert plugin.prompt_sections[-1].content["en"] == "retained instruction"
    assert not (selected / "prompt_sections/files/rejected.md").exists()
    assert len(plugin.skills) == len(plugin.tools) == len(plugin.rails) == 1
    assert Path(plugin.skills[0].dir).is_relative_to(relocated)
    native = json.loads((selected / "manifest.json").read_text(encoding="utf-8"))
    for field in ("skills", "tools", "rails", "metadata", "display_name", "id", "version"):
        assert native[field] == original[field]
    assert (_snapshot(base), _snapshot(candidate)) == before


@pytest.mark.parametrize("group,target,field,entry", [
    ("prompt", "prompt_sections/identity.md", "prompt_sections",
     {"file": "prompt_sections/identity.md", "priority": 77}),
    ("skill", "skills/base/SKILL.md", "skills", {"dir": "skills/base", "mode": "all"}),
    ("tool", "tools/base.py", "tools", {"file": "tools/base.py", "class": "UpdatedTool"}),
])
def test_filtered_native_modification_replaces_registration_once(tmp_path, group, target, field, entry):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    payload = _package(base)
    shutil.copytree(base, candidate)
    payload[field][0] = entry
    _manifest(candidate, payload)
    prepare_plugin_registries(candidate)
    selected = _filter(tmp_path, base, candidate, [_gate(candidate, group, target, operation="modify")])

    plugin = load_plugin_package(find_plugin_manifest(selected))
    actual = json.loads((selected / "manifest.json").read_text(encoding="utf-8"))[field]
    assert len(actual) == len(payload[field])
    assert all(actual[0][key] == value for key, value in entry.items())
    assert len(plugin.prompt_sections) == 2
    assert len(plugin.skills) == len(plugin.tools) == 1


@pytest.mark.parametrize("group,target,field", [
    ("prompt", "prompt_sections/identity.md", "prompt_sections"),
    ("skill", "skills/base/SKILL.md", "skills"),
    ("tool", "tools/base.py", "tools"),
])
def test_filtered_native_removal_does_not_resurrect_from_manifest(tmp_path, group, target, field):
    base = tmp_path / "base"
    payload = _package(base)
    selected = _filter(tmp_path, base, base, [_gate(base, group, target, operation="remove")])

    plugin = load_plugin_package(find_plugin_manifest(selected))
    assert len(getattr(plugin, field)) == len(payload[field]) - 1
    assert not (selected / target).exists()
    assert (base / target).exists()


def test_invalid_native_checkpoint_does_not_replace_previous_selection(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    payload = _package(base)
    shutil.copytree(base, candidate)
    payload["tools"] = [{"file": "tools/base.py", "class": "UpdatedTool"}]
    _manifest(candidate, payload)
    prepare_plugin_registries(candidate)
    # The selected file exists, but another declaration references a missing file.
    base_payload = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    base_payload["rails"] = [{"file": "rails/missing.py", "class": "MissingRail"}]
    _manifest(base, base_payload)
    sentinel = tmp_path / "run/epoch_selections/e001/previous.txt"
    _write(sentinel, "Previous selection")

    with pytest.raises((FileNotFoundError, ValueError)):
        _filter(tmp_path, base, candidate, [_gate(candidate, "tool", "tools/base.py", operation="modify")])

    assert sentinel.read_text(encoding="utf-8") == "Previous selection"
    assert not (tmp_path / "run/epoch_selections/e001.filter_tmp").exists()


def test_native_candidate_with_unregistered_file_is_rejected(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    _package(base)
    shutil.copytree(base, candidate)
    _write(candidate / "prompt_sections/new.md", "Unregistered instruction")
    _write(candidate / "prompt_sections/sections.yaml", "sections:\n- file: prompt_sections/new.md\n")

    with pytest.raises(RuntimeError, match="no matching entry"):
        _filter(tmp_path, base, candidate, [_gate(candidate, "prompt", "prompt_sections/new.md")])

    assert not (tmp_path / "run/epoch_selections/e001").exists()


def test_checkpoint_keeps_native_yaml_baseline_compatible(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    _write(base / "harness_config.yaml", yaml.safe_dump({
        "schema_version": "expert_harness.v1", "id": "native-yaml",
        "prompt_sections": [{"name": "baseline", "content": {"en": "Original instruction"}}],
    }))
    shutil.copytree(base, candidate)
    prepare_plugin_registries(candidate)
    _write(candidate / "prompt_sections/new.md", "New instruction")
    _write(candidate / "prompt_sections/sections.yaml", "sections:\n- name: new\n  file: prompt_sections/new.md\n")

    selected = _filter(tmp_path, base, candidate, [_gate(candidate, "prompt", "prompt_sections/new.md", "new")])
    plugin = load_plugin_package(find_plugin_manifest(selected))

    assert [section.name for section in plugin.prompt_sections] == ["baseline", "new"]
    assert plugin.prompt_sections[-1].content["en"] == "New instruction"
