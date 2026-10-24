"""Bounded synthetic probe receipts never promote tuning or missing native data."""

import copy

import pytest

from examples.adaptive_multi_agent_collab.learning.calibration import (
    compatibility_attempt_assessment,
    compatibility_probe_prompt,
    compatibility_report_assessment,
)


def receipt(index=0):
    return {
        "dispatch_id": index,
        "status": "completed",
        "input_count": 300,
        "usage": {
            "input_tokens": 200,
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "total_cost": 0.002,
            "is_byok": False,
            "finish_reason": "stop",
            "native_finish_reason": "end_turn",
            "served_model": "approved-model",
            "served_provider": "approved-provider",
            "response_identity_verified": True,
            "usage_sources": {"input_tokens": "generation_native", "output_tokens": "generation_native"},
            "accounting_methods": {"input_tokens": "provider", "output_tokens": "provider", "total_cost": "provider"},
        },
    }


def test_unknown_reasoning_and_normalized_tokens_are_never_native_zero_evidence():
    entry = receipt()
    valid = compatibility_attempt_assessment(entry)
    assert set(valid["checks"].values()) == {"validated"} and not valid["failed"]
    del entry["usage"]["reasoning_tokens"]
    entry["usage"]["usage_sources"]["input_tokens"] = "generation_normalized"
    entry["usage"].pop("is_byok")
    unknown = compatibility_attempt_assessment(entry)
    assert unknown["native_reasoning_tokens"] is None and unknown["native_input_tokens"] is None
    assert unknown["checks"]["non_thinking"] == "unavailable"
    assert unknown["checks"]["token_counter"] == "unavailable"
    assert unknown["checks"]["billing"] == "unavailable"


@pytest.mark.parametrize("change", ("undercount", "thinking", "truncation"))
def test_contradictory_native_receipts_fail_instead_of_retuning(change):
    entry = receipt()
    if change == "undercount":
        entry["usage"]["input_tokens"] = 301
    elif change == "thinking":
        entry["usage"]["reasoning_tokens"] = 1
    else:
        entry["usage"]["finish_reason"] = "length"
    assert compatibility_attempt_assessment(entry)["failed"]


def test_tuning_calls_cannot_validate_independent_counter_inputs():
    row = compatibility_attempt_assessment(receipt())
    requests = [{"probe": {"agent_id": 0, "evidence_role": "tuning"}, "assessment_receipts": [row]}]
    tg = [compatibility_attempt_assessment(receipt(index)) for index in range(10, 13)]
    result = compatibility_report_assessment(requests, tg, (0,))
    assert result["backends"]["0"]["token_counter"]["status"] == "unavailable" and not result["passed"]
    for index, shape in enumerate(("long_json_unicode", "near_limit_json_unicode", "repair_json_unicode")):
        requests.append(
            {
                "probe": {"agent_id": 0, "evidence_role": "verification", "shape": shape},
                "assessment_receipts": [compatibility_attempt_assessment(receipt(index + 1))],
            }
        )
    assert compatibility_report_assessment(requests, tg, (0,))["passed"]
    failed = copy.deepcopy(requests)
    failed[-1]["assessment_receipts"][0]["checks"]["native_usage"] = "unavailable"
    assert not compatibility_report_assessment(failed, tg, (0,))["passed"]


@pytest.mark.parametrize("shape", ("long_json_unicode", "near_limit_json_unicode", "repair_json_unicode"))
def test_probe_is_deterministic_counts_scaffold_and_contains_independent_json_unicode(shape):
    def complete_count(prompt):
        return 150 + len(prompt.encode("utf-8"))

    prompt, count = compatibility_probe_prompt(shape, complete_count, 1800)
    assert (prompt, count) == compatibility_probe_prompt(shape, complete_count, 1800)
    assert count == complete_count(prompt) <= 1800 and count > 1500
    assert "café" in prompt and "雪" in prompt and '"answer": "yes"' in prompt
    assert ("previous response failed validation" in prompt) == shape.startswith("repair")
