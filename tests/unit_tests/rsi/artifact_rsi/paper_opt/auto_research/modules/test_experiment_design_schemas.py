"""Regression coverage for a production failure: some tool-calling backends
(observed with GLM-5.3) don't dereference the `$ref` that
`code_agent_instruction` gets in `ExperimentDesignDraft.model_json_schema()`
and emit it as a JSON string instead of an object, which used to fail
validation with "Input should be a valid dictionary or instance of
CodeAgentInstruction" on every retry.
"""

from __future__ import annotations

import json

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    CodeAgentInstruction,
    ExperimentDesignDraft,
)

_INSTRUCTION_KWARGS = dict(
    goal="implement baseline",
    read_first=["design.md"],
    scope=["src/"],
    required_work=["write the training loop"],
    validation=["pytest"],
    expected_outputs=["metrics.json"],
    completion_report=["summary of results"],
)

_DRAFT_KWARGS = dict(
    objective="objective",
    hypothesis="hypothesis",
    metrics=[{"name": "accuracy", "spec": "top-1 accuracy on test set"}],
    experiment="experiment plan",
    grounding=["grounded claim"],
)


def test_code_agent_instruction_accepts_dict():
    draft = ExperimentDesignDraft(
        **_DRAFT_KWARGS,
        code_agent_instruction=dict(_INSTRUCTION_KWARGS),
    )
    assert isinstance(draft.code_agent_instruction, CodeAgentInstruction)
    assert draft.code_agent_instruction.goal == "implement baseline"


def test_code_agent_instruction_decodes_json_string():
    draft = ExperimentDesignDraft(
        **_DRAFT_KWARGS,
        code_agent_instruction=json.dumps(_INSTRUCTION_KWARGS),
    )
    assert isinstance(draft.code_agent_instruction, CodeAgentInstruction)
    assert draft.code_agent_instruction.goal == "implement baseline"


def test_code_agent_instruction_rejects_malformed_json_string():
    with pytest.raises(Exception):
        ExperimentDesignDraft(
            **_DRAFT_KWARGS,
            code_agent_instruction="{not json",
        )
