# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""subagent_spawn forwards thinking / model selection to the runtime control."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.prompts.tools.subagent_tools import get_subagent_spawn_input_params
from openjiuwen.harness.subagent_runtime.models import SubagentCreateOptions, SubagentStatus
from openjiuwen.harness.tools.subagent.subagent_tools import SubagentSpawnTool


def _fake_control() -> SimpleNamespace:
    return SimpleNamespace(
        spawn=AsyncMock(
            return_value=SimpleNamespace(
                subagent_id="sid",
                task_id="tid",
                status=SubagentStatus.pending_init(),
            ),
        ),
        emit_status_update=AsyncMock(),
    )


async def _invoke_spawn(payload: dict) -> SimpleNamespace:
    control = _fake_control()
    tool = SubagentSpawnTool(
        card=ToolCard(id="spawn_options", name="subagent_spawn", description="test"),
        parent_agent=SimpleNamespace(),
    )
    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        await tool.invoke(
            {
                "subagent_type": "general-purpose",
                "task_description": "render slide 3",
                "display_name": "Slide 3",
                "role": "slide designer",
                **payload,
            },
        )
    return control


@pytest.mark.asyncio
async def test_spawn_forwards_thinking_and_model_selection() -> None:
    control = await _invoke_spawn({"thinking": "off", "model_name": "glm-5.2", "model_tier": "Lite"})

    create_options = control.spawn.await_args.kwargs["create_options"]
    assert create_options == SubagentCreateOptions(thinking="off", model_name="glm-5.2", model_tier="lite")


@pytest.mark.asyncio
async def test_spawn_without_options_uses_defaults() -> None:
    control = await _invoke_spawn({})

    assert control.spawn.await_args.kwargs["create_options"] == SubagentCreateOptions()


@pytest.mark.parametrize("language", ["cn", "en"])
def test_spawn_schema_exposes_optional_create_options(language: str) -> None:
    schema = get_subagent_spawn_input_params(language)

    for name in ("thinking", "model_name", "model_tier"):
        assert schema["properties"][name]["type"] == "string"
        assert schema["properties"][name]["description"]
        assert name not in schema["required"]
