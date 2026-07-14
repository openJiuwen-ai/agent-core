# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.prompts import get_evolution_tool_input_params
from openjiuwen.harness.prompts.tools import get_tool_input_params


def test_legacy_simplify_tools_are_removed_from_metadata_registry():
    with pytest.raises(KeyError):
        get_evolution_tool_input_params("stage_skill_experience_simplify", "en")
    with pytest.raises(KeyError):
        get_evolution_tool_input_params("discard_skill_experience_simplify", "en")


@pytest.mark.parametrize(
    "tool_name",
    [
        "prepare_skill_evolution",
        "evolve_review_task",
        "list_skill_experiences",
        "read_skill_experiences",
        "evolve_skill_experiences",
        "simplify_skill_experiences",
    ],
)
def test_evolution_tool_metadata_is_not_forwarded_through_harness_registry(tool_name):
    with pytest.raises(KeyError):
        get_tool_input_params(tool_name, "en")

    assert get_evolution_tool_input_params(tool_name, "en")["type"] == "object"
