# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared MoA model, tool, session and Spec builders for offline tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard
from openjiuwen.harness import DeepAgent, create_deep_agent
from openjiuwen.harness.moa import MoAConfig
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, ModelSpec
from openjiuwen.harness.tools.moa import CONSULT_TOOL

_REFERENCE_PROPOSAL = "private-reference-result-for-test"


def model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test",
            api_base="http://localhost:1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model", max_tokens=1024),
    )


def primary(*responses: AssistantMessage) -> Model:
    result = model()
    result.invoke = AsyncMock(side_effect=responses)
    return result


def consult_call(call_id: str = "consult-1") -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[ToolCall(id=call_id, type="function", name=CONSULT_TOOL, arguments="{}")],
    )


class Echo(Tool):
    def __init__(self):
        super().__init__(ToolCard(id="echo", name="echo", description="Echo", input_params={"type": "object"}))

    async def invoke(self, inputs, **kwargs):
        del kwargs
        return {"content": str(inputs)}

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def agent(primary_model: Model, moa: MoAConfig | None = None, *, tools=None, rails=None, enable_task_loop=False):
    needs_skill_loading = moa is not None and moa.mode == "aggregator_on_demand"
    operation = SysOperation(SysOperationCard(id="moa-test-skill-loader")) if needs_skill_loading else None
    return create_deep_agent(
        model=primary_model,
        moa=moa,
        tools=tools,
        rails=rails,
        enable_sys_operation=needs_skill_loading,
        sys_operation=operation,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_read_image_multimodal=False,
        auto_create_workspace=False,
        enable_task_loop=enable_task_loop,
    )


def _call(name: str, arguments: dict | None = None, *, call_id: str = "call-1") -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[ToolCall(id=call_id, type="function", name=name, arguments=json.dumps(arguments or {}))],
    )


def _unique_rails(agent: DeepAgent, rail_type: type) -> list:
    return list({id(rail): rail for rail in agent.find_rails_by_type((rail_type,))}.values())


def _session(agent: DeepAgent) -> Session:
    return Session(session_id=f"moa-skill-{uuid4().hex}", card=agent.card)


def _messages_text(messages) -> str:
    return "\n".join(
        message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
        for message in messages
    )


def _write_skill(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: User skill for unit tests.\n---\n\n# User skill\nUser body.\n",
        encoding="utf-8",
    )
    return directory


def _spec():
    return DeepAgentSpec(
        model=ModelSpec(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://example.test/v1"
            ),
            model_request_config=ModelRequestConfig(model="primary"),
        ),
        enable_sys_operation=False,
        auto_create_workspace=False,
        enable_task_loop=False,
    )
