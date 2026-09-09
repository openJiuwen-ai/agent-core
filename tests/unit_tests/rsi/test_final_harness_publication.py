# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Final publication stays inside the run even when optimizer paths are short."""

import json
from pathlib import Path

import pytest
import yaml

from openjiuwen.harness.resources import find_plugin_manifest, load_plugin_package
from openjiuwen.rsi.harness_rsi.single_harness.iterative import _ensure_final_publication


def _fixture(tmp_path):
    run = tmp_path / "task" / "run"
    (run / "evaluations").mkdir(parents=True)
    source = tmp_path / "task" / "mh" / "runs" / "m001" / "candidate" / "r123"
    (source / "prompt_sections").mkdir(parents=True)
    (source / "prompt_sections" / "retained.md").write_text("Retained policy", encoding="utf-8")
    (source / "manifest.json").write_text(json.dumps({
        "package_type": "plugin", "id": "published-fixture", "version": "1.0.0",
        "prompt_sections": [{"file": "prompt_sections/retained.md"}],
    }), encoding="utf-8")
    best_refs = run / "best_refs.yaml"
    best_refs.write_text(yaml.safe_dump({
        "harness_refs": {"solver": str(source)},
        "roles": [{"role": "solver", "member_name": "solver", "harness_ref_path": str(source)}],
    }), encoding="utf-8")
    state = {
        "candidate_gates": [{"accepted": True, "status": "accepted"}],
        "best_harness_refs_path": str(best_refs), "best_score": 1.0,
        "published_harness_refs_path": "",
    }
    return run, source, state


@pytest.mark.parametrize("existing_publication", [False, True])
def test_final_publication_contains_native_package_within_run(tmp_path, existing_publication):
    run, source, state = _fixture(tmp_path)
    if existing_publication:
        old_refs = run / "member_optimizations" / "current_harness_refs.yaml"
        old_refs.parent.mkdir()
        old_refs.write_text(yaml.safe_dump({"harness_refs": {"solver": str(source)}}), encoding="utf-8")
        state["published_harness_refs_path"] = str(old_refs)
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    _ensure_final_publication(state=state, output_dir=run)

    refs = Path(state["published_harness_refs_path"])
    assert refs == run / "member_optimizations" / "current_harness_refs.yaml"
    payload = yaml.safe_load(refs.read_text(encoding="utf-8"))
    published = Path(payload["harness_refs"]["solver"])
    assert published.resolve().is_relative_to(run.resolve())
    assert published != source
    assert payload["roles"][0]["harness_ref_path"] == str(published)
    assert payload["role_results"]["solver"]["after_harness_ref_path"] == str(published)
    assert payload["published_best_score"] == 1.0
    assert state["publication_status"] == "published"
    plugin = load_plugin_package(find_plugin_manifest(published))
    assert plugin.prompt_sections[0].content["en"] == "Retained policy"
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()} == before

    published_before = {p.relative_to(published): p.read_bytes() for p in published.rglob("*") if p.is_file()}
    refs_before = refs.read_bytes()
    _ensure_final_publication(state=state, output_dir=run)
    assert refs.read_bytes() == refs_before
    assert {p.relative_to(published): p.read_bytes() for p in published.rglob("*") if p.is_file()} == published_before


def test_provisional_only_candidate_is_not_published(tmp_path):
    run, _, state = _fixture(tmp_path)
    state["candidate_gates"][0]["status"] = "provisional"

    _ensure_final_publication(state=state, output_dir=run)

    assert state["published_harness_refs_path"] == ""
    assert state["publication_status"] == "not_published_no_improvement"
    assert not (run / "member_optimizations" / "current_harness_refs.yaml").exists()
