# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end protocol conformance of the Codex harness against the local ``codex`` CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PromptSectionSpec
from openjiuwen.harness_protocol import HarnessInput, HarnessProtocol, HostCapability, TurnEventKind
from openjiuwen.harness_providers import build_harness_context, create_harness
from openjiuwen.harness_providers.codex import CodexHarness, CodexHarnessConfig, CodexModelConfig
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
from tests.system_tests.harness_providers.conftest import requires_codex

pytestmark = [pytest.mark.asyncio, requires_codex]


# The SDK bundles its own codex binary; the locally installed ``codex`` carries
# the user's model configuration, so drive that binary explicitly.  The CLI
# default model is used unless ``CODEX_E2E_MODEL`` names another one (useful
# when the configured default is rejected by the local CLI build).
_CODEX_BIN = shutil.which("codex")
_MODEL = CodexModelConfig(model=os.environ["CODEX_E2E_MODEL"]) if os.environ.get("CODEX_E2E_MODEL") else None


def _config_values(workdir: Path) -> dict[str, object]:
    values: dict[str, object] = {"cwd": str(workdir), "codex_bin": _CODEX_BIN, "bypass_approvals_and_sandbox": True}
    if _MODEL is not None:
        values["model"] = {"model": _MODEL.model}
    return values


def _harness(workdir: Path, *, config_overrides: tuple[str, ...] = ()) -> CodexHarness:
    harness = CodexHarness(
        CodexHarnessConfig(
            cwd=str(workdir),
            codex_bin=_CODEX_BIN,
            bypass_approvals_and_sandbox=True,
            model=_MODEL,
            config_overrides=config_overrides,
        )
    )
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


async def test_request_user_input_routes_to_the_host(workdir: Path) -> None:
    handler = RecordingUserInputHandler("teal")
    context = make_context(
        cwd=str(workdir),
        host_capabilities=frozenset({HostCapability.USER_INPUT}),
        interactions=handler,
    )
    await run_user_input_turn(_harness(workdir), context, handler, tool_hint="request_user_input")


async def test_checkpoint_resumes_the_thread(workdir: Path) -> None:
    await run_resume_turns(lambda: _harness(workdir), {"cwd": str(workdir)})


async def test_manifest_factory_drives_the_developer_instructions(workdir: Path) -> None:
    manifest = AgentTemplateSpec(
        agent_card=AgentCard(id="codeword-agent", name="codeword-agent", description="Knows a codeword."),
        prompt_sections=[
            PromptSectionSpec(
                name="codeword",
                content={"en": "When asked for the codeword, answer with exactly AMBER-7 and nothing else."},
            )
        ],
    )
    harness = create_harness(
        manifest,
        provider="codex",
        config=_config_values(workdir),
    )
    context = build_harness_context(manifest, provider="codex", host_session_id="e2e-manifest", language="en")
    await harness.start(context)
    receipt = await harness.send(HarnessInput(content="What is the codeword?"))
    events = await collect_turn(harness, receipt.turn_id)
    terminal = terminal_of(events)
    assert terminal.kind is TurnEventKind.FINISHED, terminal.result
    assert "AMBER-7" in answer_text(terminal.result, events).upper()
    await harness.stop()


@pytest.mark.parametrize("mode", ["append", "replace"])
async def test_developer_instruction_modes(workdir: Path, mode: str) -> None:
    import json
    harness = CodexHarness(CodexHarnessConfig(
        cwd=str(workdir), codex_bin=_CODEX_BIN, model=_MODEL,
        bypass_approvals_and_sandbox=True, system_prompt_mode=mode,
        config_overrides=("developer_instructions=" + json.dumps("The base codeword is COBALT-17."),),
    ))
    context = make_context(cwd=str(workdir), system_prompt="The host codeword is AMBER-83. When asked, list only codewords given in your instructions.")
    await harness.start(context)
    try:
        receipt = await harness.send(HarnessInput(content="List the codewords from your instructions."))
        terminal = terminal_of(await collect_turn(harness, receipt.turn_id))
        assert terminal.kind is TurnEventKind.FINISHED
        assert "AMBER-83" in terminal.result.final_output
        assert ("COBALT-17" in terminal.result.final_output) is (mode == "append")
    finally:
        await harness.stop()
