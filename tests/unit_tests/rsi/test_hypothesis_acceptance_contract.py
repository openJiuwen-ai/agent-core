# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unverifiable hypotheses stop before candidate generation, including old artifacts."""

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import (
    _payload_digest,
    compile_optimization_hypotheses,
    load_optimization_hypotheses,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.loader import AnalysisUnavailableError


@pytest.mark.parametrize("contract", [None, {}, {"acceptance_observable": ""},
                                      {"acceptance_observable": " \n "},
                                      {"acceptance_observable": 123}])
def test_incomplete_analysis_fails_before_writing_hypotheses(tmp_path, contract):
    source = tmp_path / "analysis_ref.yaml"
    source.write_text(yaml.safe_dump({"issues": [{
        "issue_id": "issue_bad", "category": "execution", "severity": "high",
        "summary": "Wrong result", "affected_cases": ["case_a"],
        "optimization_target": "member_harness", "recommendation": "Verify the result",
        "metadata": {"attribution": {"target_ref": "member_harness.solver.prompt",
                                     "decision_contract": contract}},
    }]}), encoding="utf-8")
    original = source.read_bytes()
    output = tmp_path / "hypotheses.yaml"
    with pytest.raises(AnalysisUnavailableError, match="issue_bad.*acceptance_observable"):
        compile_optimization_hypotheses(analysis_ref_path=str(source), cases=[], output_path=output)
    assert not output.exists()
    assert source.read_bytes() == original


def test_legacy_hypothesis_with_valid_digest_still_requires_acceptance(tmp_path):
    payload = {"source_issue_id": "legacy", "decision_contract": {"required_action": "verify"}}
    payload["content_sha256"] = _payload_digest(payload)
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({"version": 3, "hypotheses": [payload]}), encoding="utf-8")
    with pytest.raises(AnalysisUnavailableError, match="legacy.*acceptance_observable"):
        load_optimization_hypotheses(path)


def test_valid_contract_keeps_existing_digest_format(tmp_path):
    payload = {"source_issue_id": "valid", "decision_contract": {
        "acceptance_observable": "The output contains the fields required by the task."}}
    digest = _payload_digest(payload)
    payload["content_sha256"] = digest
    path = tmp_path / "valid.yaml"
    path.write_text(yaml.safe_dump({"version": 4, "hypotheses": [payload]}), encoding="utf-8")
    assert load_optimization_hypotheses(path) == [payload]
