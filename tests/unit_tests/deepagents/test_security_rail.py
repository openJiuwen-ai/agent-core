# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_dashscope_stub() -> None:
    existing = sys.modules.get("dashscope")
    if existing is not None and hasattr(existing, "api_entities"):
        return

    module = types.ModuleType("dashscope")
    module.__path__ = []

    class _DummyApi:
        @staticmethod
        def call(*args, **kwargs):  # noqa: ANN001, ANN002
            class _Resp:
                status_code = 200
                output = {}
                code = ""
                message = ""

            return _Resp()

    class _DummyAioApi:
        @staticmethod
        async def call(*args, **kwargs):  # noqa: ANN001, ANN002
            class _Resp:
                status_code = 200
                output = {}
                code = ""
                message = ""

            return _Resp()

    class DashScopeAPIResponse:
        status_code = 200
        output = {}
        code = ""
        message = ""

    module.MultiModalConversation = _DummyApi
    module.MultiModalEmbedding = _DummyApi
    module.AioMultiModalEmbedding = _DummyAioApi
    module.VideoSynthesis = _DummyApi
    module.base_http_api_url = ""

    api_entities_module = types.ModuleType("dashscope.api_entities")
    api_entities_module.__path__ = []
    dashscope_response_module = types.ModuleType("dashscope.api_entities.dashscope_response")
    dashscope_response_module.DashScopeAPIResponse = DashScopeAPIResponse
    common_module = types.ModuleType("dashscope.common")
    common_module.__path__ = []
    constants_module = types.ModuleType("dashscope.common.constants")
    constants_module.REQUEST_TIMEOUT_KEYWORD = "timeout"

    module.api_entities = api_entities_module
    module.common = common_module
    api_entities_module.dashscope_response = dashscope_response_module
    common_module.constants = constants_module

    sys.modules["dashscope"] = module
    sys.modules["dashscope.api_entities"] = api_entities_module
    sys.modules["dashscope.api_entities.dashscope_response"] = dashscope_response_module
    sys.modules["dashscope.common"] = common_module
    sys.modules["dashscope.common.constants"] = constants_module


_install_dashscope_stub()

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.builder import SystemPromptBuilder
from openjiuwen.deepagents.prompts.sections import SectionName
from openjiuwen.deepagents.prompts.sections.identity import build_identity_section
from openjiuwen.deepagents.prompts.sections.safety import build_safety_section
from openjiuwen.deepagents.rails.security_rail import SecurityRail


def _make_agent(builder=None):
    return MagicMock(system_prompt_builder=builder)


def test_init_sets_system_prompt_builder_reference() -> None:
    builder = SystemPromptBuilder()
    rail = SecurityRail()
    agent = _make_agent(builder)

    rail.init(agent)

    assert rail.system_prompt_builder is builder


def test_uninit_removes_safety_section() -> None:
    builder = SystemPromptBuilder()
    builder.add_section(build_safety_section())
    rail = SecurityRail()
    agent = _make_agent(builder)
    rail.init(agent)

    assert builder.get_section(SectionName.SAFETY) is not None

    rail.uninit(agent)

    assert builder.get_section(SectionName.SAFETY) is None
    assert rail.system_prompt_builder is None


@pytest.mark.asyncio
async def test_before_model_call_injects_safety_section() -> None:
    builder = SystemPromptBuilder(language="en")
    builder.add_section(build_identity_section(language="en"))
    rail = SecurityRail(language="en")
    agent = _make_agent(builder)
    rail.init(agent)
    ctx = AgentCallbackContext(agent=agent, inputs=None, session=None)

    await rail.before_model_call(ctx)

    section = builder.get_section(SectionName.SAFETY)
    assert section is not None
    assert "Safety Principles" in section.render("en")
    assert "Safety Principles" in builder.build()


@pytest.mark.asyncio
async def test_before_model_call_skips_when_builder_missing() -> None:
    rail = SecurityRail()
    ctx = AgentCallbackContext(agent=_make_agent(), inputs=None, session=None)

    await rail.before_model_call(ctx)

    assert rail.system_prompt_builder is None
