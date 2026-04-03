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
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.identity import build_identity_section
from openjiuwen.harness.prompts.sections.safety import build_safety_section
from openjiuwen.harness.rails.security_rail import SecurityRail


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
    rail = SecurityRail()
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


# ---------------------------------------------------------------------------
# Language resolved from shared prompt builder
# ---------------------------------------------------------------------------

def test_language_read_from_builder_after_init() -> None:
    """After init(), before_model_call reads language from builder, not a stored attribute."""
    builder = SystemPromptBuilder(language="en")
    rail = SecurityRail()
    rail.init(_make_agent(builder))

    assert rail.system_prompt_builder.language == "en"


def test_language_update_on_builder_reflected_immediately() -> None:
    """Changing builder.language is picked up on the next call without re-init."""
    builder = SystemPromptBuilder(language="cn")
    rail = SecurityRail()
    rail.init(_make_agent(builder))

    builder.language = "en"

    assert rail.system_prompt_builder.language == "en"


def test_all_rails_consistent_via_shared_builder() -> None:
    """Multiple rails sharing the same builder always see the same language."""
    builder = SystemPromptBuilder(language="cn")
    rail_a = SecurityRail()
    rail_b = SecurityRail()
    rail_a.init(_make_agent(builder))
    rail_b.init(_make_agent(builder))

    builder.language = "en"

    assert rail_a.system_prompt_builder.language == rail_b.system_prompt_builder.language == "en"


@pytest.mark.asyncio
async def test_before_model_call_uses_updated_builder_language() -> None:
    """before_model_call injects a section in the language currently set on the builder."""
    builder = SystemPromptBuilder(language="cn")
    rail = SecurityRail()
    rail.init(_make_agent(builder))

    builder.language = "en"
    ctx = AgentCallbackContext(agent=_make_agent(builder), inputs=None, session=None)
    await rail.before_model_call(ctx)

    section = builder.get_section(SectionName.SAFETY)
    assert section is not None
    assert "Safety Principles" in section.render("en")
