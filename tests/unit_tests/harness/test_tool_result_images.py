# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool images reach the model: shown once on request, and through ``tool_call``."""

from __future__ import annotations

import json

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.tool import ToolCard, ToolExposure
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails import build_read_image_multimodal_resolver
from openjiuwen.harness.tools.base_tool import ToolOutput
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    mock_llm_context,
)

KEPT_IMAGE = "data:image/png;base64,a2VwdA=="
ONCE_IMAGE = "data:image/png;base64,b25jZQ=="


class _ImageTool(Tool):
    def __init__(self, name: str, exposure: ToolExposure, *, keep_in_context: bool):
        super().__init__(
            ToolCard(
                id=name,
                name=name,
                description="Render the chart as an image",
                input_params={"type": "object", "properties": {}},
                exposure=exposure,
            )
        )
        self._keep_in_context = keep_in_context

    async def invoke(self, inputs, **kwargs):
        item = {
            "type": "image",
            "source": self.card.name,
            "source_path": f"{self.card.name}.png",
            "data_url": KEPT_IMAGE if self._keep_in_context else ONCE_IMAGE,
        }
        if not self._keep_in_context:
            item["keep_in_context"] = False
        return ToolOutput(success=True, data={"content": "rendered", "multimodal": [item]})

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _agent(tmp_path, tools, **config):
    card = SysOperationCard(
        id=f"tool_result_images_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    model = init_model(
        provider="OpenAI",
        model_name="mock-model",
        api_key="mock-api-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="tool-result-images", description="test"),
        system_prompt="You are a test assistant.",
        tools=tools,
        max_iterations=6,
        workspace=Workspace(root_path=str(tmp_path)),
        sys_operation=Runner.resource_mgr.get_sys_operation(card.id),
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        **config,
    )


class _Recorder(AgentRail):
    def __init__(self):
        super().__init__()
        self.turn_segments: list[tuple[int, int]] = []
        self.tool_results: dict[str, object] = {}

    async def before_model_call(self, ctx):
        current_turn = len(ctx.context.get_messages(with_history=False))
        self.turn_segments.append((len(ctx.context.get_messages()) - current_turn, current_turn))

    async def after_tool_call(self, ctx):
        self.tool_results[ctx.inputs.tool_name] = ctx.inputs.tool_result


def _image_urls(messages) -> list[str]:
    urls = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            urls.extend(
                part["image_url"]["url"]
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
    return urls


@pytest.mark.asyncio
async def test_image_not_kept_in_context_reaches_exactly_one_model_call(tmp_path):
    agent = _agent(
        tmp_path,
        [
            _ImageTool("render_kept", ToolExposure.DIRECT, keep_in_context=True),
            _ImageTool("render_once", ToolExposure.DIRECT, keep_in_context=False),
        ],
    )
    with mock_llm_context() as llm:
        llm.set_responses(
            [
                create_tool_call_response("render_kept", "{}", "call-kept"),
                create_tool_call_response("render_once", "{}", "call-once"),
                create_tool_call_response("render_kept", "{}", "call-kept-again"),
                create_text_response("done"),
            ]
        )
        await agent.invoke({"query": "render", "conversation_id": "images-once"})

    assert [_image_urls(call) for call in llm.call_history] == [
        [],
        [KEPT_IMAGE],
        [KEPT_IMAGE, ONCE_IMAGE],
        [KEPT_IMAGE, KEPT_IMAGE],
    ]
    last_call_text = json.dumps([getattr(m, "content", None) for m in llm.call_history[-1]])
    assert "Image loaded from render_once: render_once.png" in last_call_text
    assert "no longer attached" in last_call_text


@pytest.mark.asyncio
async def test_one_batch_keeps_its_kept_image_and_drops_its_once_image(tmp_path):
    agent = _agent(
        tmp_path,
        [
            _ImageTool("render_kept", ToolExposure.DIRECT, keep_in_context=True),
            _ImageTool("render_once", ToolExposure.DIRECT, keep_in_context=False),
        ],
    )
    both = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(id="call-kept", type="function", name="render_kept", arguments="{}"),
            ToolCall(id="call-once", type="function", name="render_once", arguments="{}"),
        ],
    )
    with mock_llm_context() as llm:
        llm.set_responses(
            [both, create_tool_call_response("render_kept", "{}", "call-again"), create_text_response("done")]
        )
        await agent.invoke({"query": "render", "conversation_id": "images-batch"})

    assert [_image_urls(call) for call in llm.call_history] == [
        [],
        [KEPT_IMAGE, ONCE_IMAGE],
        [KEPT_IMAGE, KEPT_IMAGE],
    ]


@pytest.mark.asyncio
async def test_dropping_an_image_keeps_earlier_turns_apart_from_the_current_one(tmp_path):
    recorder = _Recorder()
    agent = _agent(tmp_path, [_ImageTool("render_once", ToolExposure.DIRECT, keep_in_context=False)], rails=[recorder])
    with mock_llm_context() as llm:
        llm.set_responses([create_text_response("hi")])
        await agent.invoke({"query": "turn one", "conversation_id": "images-turns"})
        recorder.turn_segments.clear()
        llm.set_responses(
            [
                create_tool_call_response("render_once", "{}", "call-1"),
                create_tool_call_response("render_once", "{}", "call-2"),
                create_text_response("done"),
            ]
        )
        await agent.invoke({"query": "turn two", "conversation_id": "images-turns"})

    earlier_turns = {earlier for earlier, _ in recorder.turn_segments}
    assert len(earlier_turns) == 1 and earlier_turns != {0}, recorder.turn_segments


@pytest.mark.asyncio
async def test_deferred_tool_images_reach_the_model_through_tool_call(tmp_path):
    recorder = _Recorder()
    agent = _agent(
        tmp_path,
        [_ImageTool("render_deferred", ToolExposure.DEFERRED, keep_in_context=True)],
        rails=[recorder],
        progressive_tool_enabled=True,
    )
    with mock_llm_context() as llm:
        llm.set_responses(
            [
                create_tool_call_response("tool_search", json.dumps({"query": "render chart image"}), "call-search"),
                create_tool_call_response(
                    "tool_call", json.dumps({"name": "render_deferred", "args": {}}), "call-wrapped"
                ),
                create_text_response("done"),
            ]
        )
        await agent.invoke({"query": "render", "conversation_id": "images-deferred"})

    assert [_image_urls(call) for call in llm.call_history] == [[], [], [KEPT_IMAGE]]
    assert str(recorder.tool_results["tool_call"]).count(KEPT_IMAGE) == 1


@pytest.mark.parametrize("supports_images", [True, False])
def test_native_image_resolver_is_public_and_follows_the_agent_config(tmp_path, supports_images):
    agent = _agent(tmp_path, [], enable_read_image_multimodal=supports_images)

    assert build_read_image_multimodal_resolver(agent)() is supports_images
