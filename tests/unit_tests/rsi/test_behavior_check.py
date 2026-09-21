# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Behavior checks are paired observations, never substitute task scores."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.harness_rsi.single_harness import behavior_check as module


@pytest.mark.parametrize(
    ("observation", "passed", "route"),
    [
        ({"availability": "no"}, False, "repair_activation"),
        ({"availability": "yes", "behavior_changed": "no"}, False, "repair_execution"),
        ({"behavior_changed": "no"}, True, "collect_behavior_evidence"),
        ({"behavior_changed": "yes", "candidate_check": "no"}, False, "revise_implementation_or_cause"),
        ({"source_check": "no", "candidate_check": "yes"}, False, "investigate_residuals"),
        ({"source_check": "no", "candidate_check": "yes"}, True, "verified"),
        ({"source_check": "yes", "candidate_check": "yes"}, True, "collect_behavior_evidence"),
    ],
)
def test_feedback_uses_observation_not_just_score(observation, passed, route):
    assert module.feedback_route(observation, task_passed=passed) == route


def test_quotes_must_exist_on_the_claimed_side():
    evidence = {
        "source": {"files": {"output.py": "return 2  # fixed cost"}},
        "candidate": {"files": {"output.py": "return 1.5  # expected cost"}},
    }
    raw = {
        "source_check": "no",
        "candidate_check": "yes",
        "behavior_changed": "yes",
        "evidence": [
            {"side": "source", "file": "output.py", "quote": "return 2  # fixed cost"},
            {"side": "candidate", "file": "output.py", "quote": "this never appeared"},
        ],
    }
    result = module._validate_observation(raw, evidence)
    assert result["source_check"] == "no"
    assert result["candidate_check"] == result["behavior_changed"] == "unknown"


def test_task_contract_alone_is_not_behavior_evidence():
    evidence = {"candidate": {"files": {"task": "Compute expected cost"}}}
    result = module._validate_observation(
        {
            "candidate_check": "yes",
            "evidence": [{"side": "candidate", "file": "task", "quote": "Compute expected cost"}],
        },
        evidence,
    )
    assert result["candidate_check"] == "unknown"


@pytest.mark.parametrize("locations,expected", [
    ({"response": "Delivered verified answer"}, "yes"),
    ({"task": "Delivered verified answer"}, "unknown"),
    ({"response": "Delivered a different answer"}, "unknown"),
    ({"one": "Delivered verified answer", "two": "Delivered verified answer"}, "unknown"),
])
def test_mislabelled_quote_requires_unique_exact_same_side_match(locations, expected):
    evidence = {
        "source": {"files": {"response": "Old response: Delivered verified answer"}},
        "candidate": {"files": locations},
    }
    result = module._validate_observation({
        "candidate_check": "yes",
        "evidence": [{"side": "candidate", "file": "wrong.json", "quote": "Delivered verified answer"}],
    }, evidence)
    assert result["candidate_check"] == expected
    if expected == "yes":
        assert result["evidence"][0]["file"] == "response"
    assert result["behavior_changed"] == "unknown"


def test_unreadable_case_evidence_is_unknown_not_a_task_error(tmp_path):
    case = tmp_path / "cases" / "broken"
    case.mkdir(parents=True)
    (case / "result.json").write_text("{partial", encoding="utf-8")
    evidence = module._evidence(str(tmp_path / "eval_ref.yaml"), "broken")
    assert evidence == {"files": {}, "omitted": ["case evidence unreadable"]}


def test_model_failure_does_not_fabricate_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_evidence", lambda *_: {"files": {"response": "unverified response"}})

    def fail(_):
        raise ValueError("bad diagnostic configuration")

    monkeypatch.setattr(module, "load_member_optimizer_model", fail)
    result = asyncio.run(
        module.check_candidate_behavior(
            source_eval_ref="source",
            candidate_eval_ref="candidate",
            model_config_ref="missing",
            capabilities=[
                {"target_case_ids": ["one"], "decision_contracts": [{"acceptance_observable": "required operation"}]}
            ],
            output_dir=tmp_path,
        )
    )
    assert result["one"]["error_type"] == "ValueError"
    assert result["one"]["candidate_check"] == "unknown"


def test_check_caches_exact_pair_but_invalidates_model_or_evidence_change(tmp_path, monkeypatch):
    calls = []
    evidence = {"files": {"response": "Independent result is 1.5"}, "omitted": []}
    monkeypatch.setattr(
        module,
        "_evidence",
        lambda ref, _: (
            evidence if ref == "candidate" else {"files": {"response": "Independent result is 2.0"}, "omitted": []}
        ),
    )

    async def invoke(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "source_check": "no",
                    "candidate_check": "yes",
                    "behavior_changed": "yes",
                    "evidence": [
                        {
                            "side": side,
                            "file": "response",
                            "quote": "Independent result is " + ("2.0" if side == "source" else "1.5"),
                        }
                        for side in ("source", "candidate")
                    ],
                }
            )
        )

    monkeypatch.setattr(module, "load_member_optimizer_model", lambda _: SimpleNamespace(invoke=invoke))
    model = tmp_path / "model.yaml"
    model.write_text("model: first", encoding="utf-8")
    arguments = {
        "source_eval_ref": "source",
        "candidate_eval_ref": "candidate",
        "model_config_ref": str(model),
        "capabilities": [
            {
                "target_case_ids": ["one"],
                "decision_contracts": [
                    {"acceptance_observable": "Independent enumeration matches; no-stop cost unchanged"}
                ],
            }
        ],
        "output_dir": tmp_path / "checks",
    }
    result = asyncio.run(module.check_candidate_behavior(**arguments))
    assert result["one"]["candidate_check"] == "yes"
    asyncio.run(module.check_candidate_behavior(**arguments))
    assert len(calls) == 1
    model.write_text("model: second", encoding="utf-8")
    asyncio.run(module.check_candidate_behavior(**arguments))
    assert len(calls) == 2
    evidence["files"]["response"] += " with a boundary"
    asyncio.run(module.check_candidate_behavior(**arguments))
    assert len(calls) == 3
    assert calls[0]["tools"] is None


def test_missing_check_is_unknown_without_calling_model(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_evidence", lambda *_: {"files": {"response": "hello"}})

    def unexpected(_):
        raise AssertionError("missing check must not invoke the model")

    monkeypatch.setattr(module, "load_member_optimizer_model", unexpected)
    results = asyncio.run(
        module.check_candidate_behavior(
            source_eval_ref="source",
            candidate_eval_ref="candidate",
            model_config_ref="missing",
            capabilities=[{"target_case_ids": ["one"], "decision_contracts": [{}]}],
            output_dir=tmp_path,
        )
    )
    assert results["one"]["candidate_check"] == "unknown"
