"""Regression coverage for the same production failure pattern documented in
test_experiment_design_schemas.py: some tool-calling backends don't
dereference the `$ref` a nested model/list gets in the JSON schema and emit
it as a JSON string instead of the real structure, which used to fail
validation outright.
"""

from __future__ import annotations

import json

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import (
    EvidenceItem,
    ReflectionJudgment,
)

_JUDGMENT_KWARGS = dict(
    validity="valid",
    hypothesis_verdict="supported",
    objective_progress="advanced",
    confidence="high",
    confidence_reason="clear separation between variants",
    recommendation="accept_and_report",
    recommendation_reason="all gates satisfied",
    summary="the hypothesis is supported by the evidence",
)

_EVIDENCE_KWARGS = dict(
    metric="accuracy",
    values={"proposed": 0.82, "baseline": 0.61},
    comparison="proposed > baseline",
    what_it_shows="the proposed method improves accuracy",
)


def test_evidence_accepts_list_of_dicts():
    judgment = ReflectionJudgment(**_JUDGMENT_KWARGS, evidence=[dict(_EVIDENCE_KWARGS)])
    assert isinstance(judgment.evidence[0], EvidenceItem)
    assert judgment.evidence[0].metric == "accuracy"


def test_evidence_decodes_json_string():
    judgment = ReflectionJudgment(**_JUDGMENT_KWARGS, evidence=json.dumps([_EVIDENCE_KWARGS]))
    assert isinstance(judgment.evidence[0], EvidenceItem)
    assert judgment.evidence[0].metric == "accuracy"


def test_evidence_rejects_malformed_json_string():
    with pytest.raises(Exception):
        ReflectionJudgment(**_JUDGMENT_KWARGS, evidence="{not json")
