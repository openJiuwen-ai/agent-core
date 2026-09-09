# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end protocol conformance of the Claude Code harness against the local ``claude`` CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PromptSectionSpec
from openjiuwen.harness_protocol import HarnessProtocol, HostCapability
from openjiuwen.harness_providers import build_harness_context, create_harness
from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness, ClaudeCodeHarnessConfig
from tests.system_tests.harness_providers._contract import (
    RecordingUserInputHandler,
    answer_text,
    collect_turn,
    make_context,
    run_abort_turn,
    run_follow_up_turns,
    run_resume_turns,
    run_steer_turn,
    run_text_turn,
    run_tool_turn,
    run_user_input_turn,
    terminal_of,
)
from tests.system_tests.harness_providers.conftest import requires_claude
from openjiuwen.harness_protocol import HarnessInput, TurnEventKind

pytestmark = [pytest.mark.asyncio, requires_claude]


# The SDK bundles its own CLI build; the locally installed ``claude`` carries
# the user's model configuration, so drive that binary explicitly.
_CLI_PATH = shutil.which("claude")


def _config(workdir: Path) -> ClaudeCodeHarnessConfig:
    return ClaudeCodeHarnessConfig(cwd=str(workdir), add_dirs=(str(workdir),), cli_path=_CLI_PATH)


def _harness(workdir: Path) -> ClaudeCodeHarness:
    harness = ClaudeCodeHarness(_config(workdir))
    assert isinstance(harness, HarnessProtocol)
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


async def test_ask_user_question_routes_to_the_host(workdir: Path) -> None:
    handler = RecordingUserInputHandler("teal")
    context = make_context(
        cwd=str(workdir),
        host_capabilities=frozenset({HostCapability.USER_INPUT}),
        interactions=handler,
    )
    await run_user_input_turn(_harness(workdir), context, handler)


async def test_checkpoint_resumes_the_session(workdir: Path) -> None:
    await run_resume_turns(lambda: _harness(workdir), {"cwd": str(workdir)})


async def test_manifest_factory_drives_the_system_prompt(workdir: Path) -> None:
    manifest = AgentTemplateSpec(
        agent_card=AgentCard(id="codeword-agent", name="codeword-agent", description="Knows a codeword."),
        prompt_sections=[
            PromptSectionSpec(
                name="codeword",
                content={"en": "When asked for the codeword, answer with exactly AMBER-7 and nothing else."},
            )
        ],
    )
    harness = create_harness(manifest, provider="claudecode", config={"cwd": str(workdir), "cli_path": _CLI_PATH})
    context = build_harness_context(manifest, provider="claudecode", host_session_id="e2e-manifest", language="en")
    await harness.start(context)
    receipt = await harness.send(HarnessInput(content="What is the codeword?"))
    events = await collect_turn(harness, receipt.turn_id)
    terminal = terminal_of(events)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert "AMBER-7" in answer_text(terminal.result, events).upper()
    await harness.stop()
