# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared fixtures for DeepAgent + ExtensionEventBus harness E2E tests."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator
from unittest.mock import patch

import pytest

from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from tests.unit_tests.fixtures.mock_llm import MockLLMModel

PROBE_ECHO_TOOL_NAME = "probe_echo"


def permissions_allow_all() -> dict:
    """Return permissive tool security config for harness E2E."""
    return {
        "enabled": True,
        "defaults": {"*": "allow"},
        "tools": {},
    }


def fake_model(*, api_key: str = "sk-fake-agent-loop-harness-e2e") -> Model:
    """Build a placeholder model config for create_deep_agent."""
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key=api_key,
            api_base="https://api.example.com/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
    )


def probe_echo_tool(invoke_count: list[int]) -> LocalFunction:
    """Local probe tool that increments invoke_count on each execution."""

    async def _echo(payload: str) -> dict[str, str]:
        invoke_count[0] += 1
        return {"echo": payload}

    return LocalFunction(
        card=ToolCard(
            id=PROBE_ECHO_TOOL_NAME,
            name=PROBE_ECHO_TOOL_NAME,
            description="Harness E2E probe tool",
            input_params={
                "type": "object",
                "properties": {"payload": {"type": "string"}},
                "required": ["payload"],
            },
        ),
        func=_echo,
    )


async def build_extension_bus() -> ExtensionEventBus:
    """Create a bus with ops.build completed."""
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


@asynccontextmanager
async def harness_runner(*, bus: ExtensionEventBus | None) -> AsyncIterator[None]:
    """Start Runner, optionally publish bus, then tear down."""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        Runner.set_extension_bus(bus)
        yield
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()


@contextmanager
def patch_mock_llm(mock_llm: MockLLMModel):
    """Patch Model.stream/invoke to delegate to MockLLMModel."""
    with (
        patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ),
        patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ),
    ):
        yield


def default_agent_options(tmp_path) -> dict:
    """Common create_deep_agent kwargs for in-process harness E2E."""
    return {
        "workspace": str(tmp_path),
        "permissions": permissions_allow_all(),
        "enable_task_loop": False,
        "auto_create_workspace": False,
        "enable_read_image_multimodal": False,
    }


@pytest.fixture
def invoke_counter() -> list[int]:
    """Mutable single-element list counting probe tool invocations."""
    return [0]
