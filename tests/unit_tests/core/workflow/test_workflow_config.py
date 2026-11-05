# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from pydantic import ValidationError

from openjiuwen.core.workflow.workflow_config import CompIOConfig, NodeSpec, WorkflowSpec


@pytest.mark.parametrize("field", ["io_configs", "stream_io_configs"])
def test_node_spec_accepts_explicit_null_io_config(field):
    assert getattr(NodeSpec(**{field: None}), field) is None


@pytest.mark.parametrize("json_mode", [False, True])
def test_default_node_spec_round_trip(json_mode):
    original = NodeSpec()
    if json_mode:
        restored = NodeSpec.model_validate_json(original.model_dump_json())
    else:
        restored = NodeSpec.model_validate(original.model_dump())
    assert restored == original


def test_workflow_spec_round_trip_with_default_node():
    original = WorkflowSpec(comp_configs={"node": NodeSpec()})
    assert WorkflowSpec.model_validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize("field", ["io_configs", "stream_io_configs"])
def test_node_spec_preserves_configured_io(field):
    config = CompIOConfig(inputs_schema={"value": "${start.value}"})
    original = NodeSpec(**{field: config})
    restored = NodeSpec.model_validate_json(original.model_dump_json())
    assert restored == original
    assert getattr(restored, field).inputs_schema == {"value": "${start.value}"}


@pytest.mark.parametrize("field", ["io_configs", "stream_io_configs"])
def test_node_spec_rejects_invalid_io_config(field):
    with pytest.raises(ValidationError):
        NodeSpec(**{field: "invalid"})
