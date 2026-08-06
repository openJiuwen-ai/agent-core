# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "openjiuwen.harness.prompts.tools",
        "openjiuwen.harness.prompts.sections.tools",
    ],
)
def test_list_tool_metadata_returns_sorted_localized_entries(module_name: str) -> None:
    tools = import_module(module_name)

    metadata = tools.list_tool_metadata("en")

    names = [entry["name"] for entry in metadata]
    assert names == sorted(names)
    assert set(metadata[0]) == {"name", "description"}
    by_name = {entry["name"]: entry for entry in metadata}
    assert by_name["bash"]["description"] == tools.get_tool_description("bash", "en")


@pytest.mark.parametrize(
    "module_name",
    [
        "openjiuwen.harness.prompts.tools",
        "openjiuwen.harness.prompts.sections.tools",
    ],
)
def test_list_tool_metadata_does_not_expose_registry_state(module_name: str) -> None:
    tools = import_module(module_name)

    metadata = tools.list_tool_metadata()
    metadata.clear()

    assert tools.list_tool_metadata()
