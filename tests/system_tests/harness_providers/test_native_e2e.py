# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end protocol conformance of the in-process DeepAgent harness.

Requires a real model endpoint through ``API_BASE`` / ``API_KEY`` /
``MODEL_NAME`` (the same variables the other harness system tests use).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import ModelSpec, RailSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PromptSectionSpec
from openjiuwen.harness_protocol import HarnessInput, HarnessProtocol, HostCapability, TurnEventKind
from openjiuwen.harness_providers import build_harness_context, create_harness
from openjiuwen.harness_providers.native import DeepAgentHarness
from tests.system_tests.harness_providers._contract import (
    RecordingUserInputHandler,
    answer_text,
    collect_turn,
    make_context,
    run_abort_turn,
    run_follow_up_turns,
    run_steer_turn,
    run_text_turn,
    run_tool_turn,
    run_user_input_turn,
    terminal_of,
)
from tests.system_tests.harness_providers.conftest import requires_native_model

pytestmark = [pytest.mark.asyncio, requires_native_model]


@pytest_asyncio.fixture(autouse=True)
async def _runner() -> None:
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


def _manifest(*, with_ask_user: bool = False) -> AgentTemplateSpec:
    # File / shell tools are mounted by the sys-operation rail, which a spec
    # build does not add on its own; the manifest declares it explicitly.
    rails = [RailSpec(type="core.sys_operation")]
    if with_ask_user:
        rails.append(RailSpec(type="core.ask_user"))
    return AgentTemplateSpec(
        agent_card=AgentCard(id="native-e2e", name="native-e2e", description="Native harness e2e agent."),
        model=ModelSpec(
            model_client_config=ModelClientConfig(
                client_provider=os.environ.get("MODEL_PROVIDER", "OpenAI"),
                api_key=os.environ["API_KEY"],
                api_base=os.environ["API_BASE"],
                verify_ssl=False,
            ),
            model_request_config=ModelRequestConfig(model=os.environ["MODEL_NAME"]),
        ),
        prompt_sections=[PromptSectionSpec(name="identity", content={"en": "You are a concise assistant."})],
        rails=rails,
    )


def _harness(workdir: Path, *, with_ask_user: bool = False) -> DeepAgentHarness:
    harness = create_harness(
        _manifest(with_ask_user=with_ask_user),
        provider="native",
        config={"deep_agent": {"cwd": str(workdir), "enable_task_loop": False, "workspace": {"root_path": str(workdir)}}},
        language="en",
    )
    assert isinstance(harness, HarnessProtocol)
    assert isinstance(harness, DeepAgentHarness)
    return harness


async def test_text_turn(workdir: Path) -> None:
    await run_text_turn(_harness(workdir), make_context(cwd=str(workdir)))


async def test_tool_turn_reads_a_file(workdir: Path) -> None:
    await run_tool_turn(_harness(workdir), make_context(cwd=str(workdir)), workdir)


async def test_follow_up_turns_share_one_stream(workdir: Path) -> None:
    await run_follow_up_turns(_harness(workdir), make_context(cwd=str(workdir)))


async def test_abort_terminates_the_active_turn(workdir: Path) -> None:
    await run_abort_turn(_harness(workdir), make_context(cwd=str(workdir)))


async def test_steer_targets_the_active_turn(workdir: Path) -> None:
    await run_steer_turn(_harness(workdir), make_context(cwd=str(workdir)))


async def test_ask_user_interrupt_routes_to_the_host(workdir: Path) -> None:
    handler = RecordingUserInputHandler("teal")
    context = make_context(
        cwd=str(workdir),
        host_capabilities=frozenset({HostCapability.USER_INPUT}),
        interactions=handler,
    )
    await run_user_input_turn(_harness(workdir, with_ask_user=True), context, handler)


async def test_manifest_prompt_sections_reach_the_agent(workdir: Path) -> None:
    manifest = _manifest().model_copy(
        update={
            "prompt_sections": [
                PromptSectionSpec(
                    name="codeword",
                    content={"en": "When asked for the codeword, answer with exactly AMBER-7 and nothing else."},
                )
            ]
        }
    )
    harness = create_harness(manifest, provider="native", config={"deep_agent": {"enable_task_loop": False}}, language="en")
    context = build_harness_context(manifest, provider="native", host_session_id="e2e-manifest", language="en")
    await harness.start(context)
    receipt = await harness.send(HarnessInput(content="What is the codeword?"))
    events = await collect_turn(harness, receipt.turn_id)
    terminal = terminal_of(events)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert "AMBER-7" in answer_text(terminal.result, events).upper()
    await harness.stop()
