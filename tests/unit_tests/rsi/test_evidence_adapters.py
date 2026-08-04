# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for artifact-specific machine evidence adapters."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.rsi.evaluator.evidence_adapters import registry
from openjiuwen.rsi.evaluator.evidence_adapters.web import (
    WebArtifactAdapter,
    _extract_runtime_errors,
    _run_verification_contract,
    _verification_assertion_script,
)
from openjiuwen.rsi.evaluator.judge_skills import (
    format_judge_skill_instructions,
    resolve_judge_skills,
    resolve_judge_skills_for_task,
)
from openjiuwen.rsi.evaluator.judger import llm_as_judge
from openjiuwen.rsi.evaluator.judger.llm_as_judge import (
    _artifact_runtime_score_ceiling,
)


def test_web_adapter_detects_index_entrypoint(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    adapter = WebArtifactAdapter()

    assert adapter.supports(artifacts) is False
    (artifacts / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert adapter.supports(artifacts) is True


def test_runtime_error_extraction_uses_browser_events() -> None:
    errors = _extract_runtime_errors(
        [
            {
                "method": "Runtime.exceptionThrown",
                "params": {"exceptionDetails": {"text": "ReferenceError"}},
            },
            {
                "method": "Log.entryAdded",
                "params": {"entry": {"level": "error", "text": "load failed"}},
            },
            {
                "method": "Log.entryAdded",
                "params": {"entry": {"level": "info", "text": "ignored"}},
            },
        ]
    )

    assert errors == [
        {"source": "page_exception", "text": "ReferenceError"},
        {"source": "console_error", "text": "load failed"},
    ]


def test_web_assertion_reports_observed_class_names() -> None:
    script = _verification_assertion_script("has_class", ".card", "selected")

    assert "class_names" in script
    assert "Array.from(element.classList)" in script


def test_web_assertion_compares_computed_style_with_default() -> None:
    script = _verification_assertion_script(
        "computed_style_not_default",
        ".card-title",
        "color",
    )

    assert "getPropertyValue(property)" in script
    assert "baseline.style.all = 'initial'" in script
    assert "default_value" in script


def test_web_verification_rejects_empty_text_without_executing_browser_script() -> None:
    class Client:
        def evaluate(self, _script: str):
            raise AssertionError("vacuous assertion must fail before browser execution")

    result = _run_verification_contract(
        Client(),
        {
            "steps": [
                {"assert": "text_contains", "selector": "#mana", "value": ""},
            ]
        },
    )

    assert result == {
        "passed": False,
        "steps": [
            {
                "index": 0,
                "assert": "text_contains",
                "selector": "#mana",
                "value": "",
                "passed": False,
                "reason": "missing_expected_value",
            }
        ],
    }


def test_web_verification_rejects_invalid_count_without_browser_script() -> None:
    class Client:
        def evaluate(self, _script: str):
            raise AssertionError("invalid count must fail before browser execution")

    result = _run_verification_contract(
        Client(),
        {
            "steps": [
                {"assert": "count_at_least", "selector": ".card", "value": -1},
            ]
        },
    )

    assert result is not None
    assert result["passed"] is False
    assert result["steps"][0]["reason"] == "invalid_count_value"


def test_web_verification_rejects_vacuous_count_at_least_without_browser_script() -> None:
    class Client:
        def evaluate(self, _script: str):
            raise AssertionError("vacuous count must fail before browser execution")

    result = _run_verification_contract(
        Client(),
        {
            "steps": [
                {"assert": "count_at_least", "selector": ".card", "value": 0},
            ]
        },
    )

    assert result is not None
    assert result["passed"] is False
    assert result["steps"][0]["reason"] == "vacuous_count_value"


def test_registry_reuses_cached_machine_evidence(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    evidence_dir = tmp_path / "judge" / "evidence"
    artifacts.mkdir()
    evidence_dir.mkdir(parents=True)
    (artifacts / "index.html").write_text("<!doctype html>", encoding="utf-8")
    expected = {"adapter": "web", "status": "collected", "observations": []}
    (evidence_dir / "artifact_runtime_evidence.json").write_text(
        json.dumps(expected),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        WebArtifactAdapter,
        "collect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must use cache")),
    )

    assert registry.collect_artifact_runtime_evidence(artifacts, evidence_dir) == expected


def test_web_runtime_score_ceiling_distinguishes_smoke_from_failure() -> None:
    smoke = {
        "adapter": "web",
        "status": "collected",
        "score_policy": {"failure_ceiling": 0.65, "smoke_only_ceiling": 0.85},
        "observations": [
            {"type": "browser_execution", "status": "passed"},
            {"type": "runtime_errors", "status": "passed"},
        ],
    }
    broken = {
        "adapter": "web",
        "status": "collected",
        "score_policy": {"failure_ceiling": 0.65, "smoke_only_ceiling": 0.85},
        "observations": [{"type": "runtime_errors", "status": "failed"}],
    }

    assert _artifact_runtime_score_ceiling(smoke) == 0.85
    assert _artifact_runtime_score_ceiling(broken) == 0.65
    assert _artifact_runtime_score_ceiling({"adapter": "none"}) is None


def test_web_judge_skill_is_discovered_from_artifact_marker(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    assert resolve_judge_skills(artifacts) == []

    (artifacts / "index.html").write_text("<!doctype html>", encoding="utf-8")
    skills = resolve_judge_skills(artifacts)

    assert [skill.name for skill in skills] == ["web"]
    assert skills[0].evidence_profile == "web_browser"
    assert skills[0].required_case_evidence == ("web_verification",)
    assert skills[0].runtime_failure_ceiling == 0.65
    assert "Web Judge Skill" in format_judge_skill_instructions(skills)
    assert [skill.name for skill in resolve_judge_skills_for_task("Build a browser game")] == ["web"]
    assert resolve_judge_skills_for_task("Create an investor memo") == []


def test_registry_attaches_active_judge_skill_policy(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    evidence_dir = tmp_path / "judge" / "evidence"
    artifacts.mkdir()
    (artifacts / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(
        WebArtifactAdapter,
        "collect",
        lambda *_args, **_kwargs: {
            "adapter": "web",
            "status": "collected",
            "observations": [],
        },
    )

    evidence = registry.collect_artifact_runtime_evidence(
        artifacts,
        evidence_dir,
        evidence_profiles=["web_browser"],
        judge_skills=["web"],
        score_policy={"failure_ceiling": 0.65, "smoke_only_ceiling": 0.85},
    )

    assert evidence["evidence_profile"] == "web_browser"
    assert evidence["judge_skills"] == ["web"]
    assert evidence["score_policy"]["smoke_only_ceiling"] == 0.85


def test_direct_judge_prompt_mounts_web_skill_instead_of_domain_prompt_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "index.html").write_text("<!doctype html><button>Play</button>", encoding="utf-8")
    monkeypatch.setattr(
        llm_as_judge,
        "collect_artifact_runtime_evidence",
        lambda *_args, **_kwargs: {
            "adapter": "web",
            "status": "collected",
            "observations": [],
        },
    )

    prompt = llm_as_judge._build_direct_judge_prompt(
        base_prompt="judge this artifact",
        case_dir=tmp_path,
    )

    assert "### Active domain judge skills" in prompt
    assert "#### Judge skill: web" in prompt
    assert "## Quality contract" in prompt
