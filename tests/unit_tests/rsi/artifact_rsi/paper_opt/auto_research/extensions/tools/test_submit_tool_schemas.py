"""Regression coverage for the GLM-5.2 $ref schema bug.

A submit tool built from raw ``BaseModel.model_json_schema()`` sends nested
models to the LLM as a bare ``{"$ref": "#/$defs/X"}`` against a separate
``$defs`` block. Weaker function-calling models fail to resolve that and
serialize the nested field as a JSON string instead of an object, which then
fails Pydantic validation on every single submission attempt (observed with
GLM-5.2 on ``code_agent_instruction`` across 57 consecutive experiment_design
rounds). ``CallableSchemaExtractor.get_base_model_schema`` expands every
``$ref`` inline instead; these tests pin that expansion for each submit tool.
"""

import json

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_experiment_design import (
    SubmitExperimentDesignTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_manager_decision import (
    SubmitManagerDecisionTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_topic_survey import (
    SubmitTopicSurveyTool,
)


def _assert_no_bare_refs(schema: dict) -> None:
    assert "$defs" not in schema
    assert "definitions" not in schema
    assert "$ref" not in json.dumps(schema)


def test_submit_experiment_design_schema_has_no_bare_refs():
    tool = SubmitExperimentDesignTool()
    schema = tool.card.input_params
    _assert_no_bare_refs(schema)
    # code_agent_instruction is the field that actually broke in production.
    nested = schema["properties"]["code_agent_instruction"]
    assert nested["type"] == "object"
    assert "goal" in nested["properties"]


def test_submit_manager_decision_schema_has_no_bare_refs():
    tool = SubmitManagerDecisionTool()
    _assert_no_bare_refs(tool.card.input_params)


def test_submit_topic_survey_schema_has_no_bare_refs():
    tool = SubmitTopicSurveyTool()
    _assert_no_bare_refs(tool.card.input_params)
