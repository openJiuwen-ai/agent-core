# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_context_rail - AutoHarnessContextRail tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.auto_harness.rails.context_rail import (
    AutoHarnessContextRail,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
)
from openjiuwen.harness.prompts.builder import (
    SystemPromptBuilder,
)


def _make_agent() -> SimpleNamespace:
    config = SimpleNamespace(
        context_processors=[],
        model_config_obj=None,
        model_client_config=None,
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section = Mock(wraps=builder.add_section)
    builder.remove_section = Mock(wraps=builder.remove_section)
    return SimpleNamespace(
        react_agent=SimpleNamespace(_config=config),
        system_prompt_builder=builder,
        ability_manager=SimpleNamespace(),
    )


def test_auto_harness_context_rail_init_keeps_context_processors():
    """应复用父类 processor 装配能力。"""
    rail = AutoHarnessContextRail(preset=True)
    agent = _make_agent()

    rail.init(agent)

    processors = dict(agent.react_agent._config.context_processors)
    assert "DialogueCompressor" in processors
    assert "MessageSummaryOffloader" in processors
    assert "CurrentRoundCompressor" in processors
    assert "RoundLevelCompressor" in processors


@pytest.mark.asyncio
async def test_auto_harness_context_rail_skips_prompt_section_injection():
    """不应注入 workspace/tools/context prompt sections。"""
    rail = AutoHarnessContextRail(preset=True)
    agent = _make_agent()
    rail.init(agent)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "test"}]
        ),
        session=None,
    )
    await rail.before_model_call(ctx)

    assert agent.system_prompt_builder.add_section.call_count == 0
    assert agent.system_prompt_builder.remove_section.call_count == 0


def test_auto_harness_context_rail_uninit_is_noop():
    """卸载时不应修改 builder sections。"""
    rail = AutoHarnessContextRail(preset=True)
    agent = _make_agent()
    rail.init(agent)

    rail.uninit(agent)

    assert agent.system_prompt_builder.remove_section.call_count == 0
