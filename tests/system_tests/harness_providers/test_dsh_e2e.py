# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end protocol conformance of the DSH harness against the local DeepSeek Harness runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_protocol import HarnessProtocol, UnsupportedHarnessCapabilityError
from openjiuwen.harness_providers import build_harness_context, create_harness
from openjiuwen.harness_providers.dsh import DshHarness, DshHarnessConfig
from tests.system_tests.harness_providers._contract import (
    make_context,
    run_follow_up_turns,
    run_text_turn,
    run_tool_turn,
)
from tests.system_tests.harness_providers.conftest import dsh_default_model, dsh_home, requires_dsh

pytestmark = [pytest.mark.asyncio, requires_dsh]


def _config_values(workdir: Path) -> dict[str, object]:
    # The SDK never falls back to ``~/.dsh`` implicitly; point it at the CLI
    # home and reuse the CLI's default provider/model choice.
    return {"cwd": str(workdir), "dsh_home": str(dsh_home()), **dsh_default_model()}


def _harness(workdir: Path) -> DshHarness:
    harness = DshHarness(DshHarnessConfig(**_config_values(workdir)))  # type: ignore[arg-type]
    assert isinstance(harness, HarnessProtocol)
    return harness


async def test_text_turn(workdir: Path) -> None:
    await run_text_turn(_harness(workdir), make_context(cwd=str(workdir)))


async def test_tool_turn_reads_a_file(workdir: Path) -> None:
    await run_tool_turn(_harness(workdir), make_context(cwd=str(workdir)), workdir)


async def test_follow_up_turns_share_one_stream(workdir: Path) -> None:
    await run_follow_up_turns(_harness(workdir), make_context(cwd=str(workdir)))


async def test_unsupported_capabilities_fail_loudly(workdir: Path) -> None:
    harness = _harness(workdir)
    await harness.start(make_context(cwd=str(workdir)))
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await harness.abort()
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await harness.pause()
    assert await harness.export_checkpoint() is None
    await harness.stop()


async def test_manifest_factory_creates_a_dsh_harness(workdir: Path) -> None:
    manifest = AgentTemplateSpec(agent_card=AgentCard(id="dsh-agent", name="dsh-agent", description="DSH agent."))
    harness = create_harness(manifest, provider="dsh", config=_config_values(workdir))
    assert isinstance(harness, DshHarness)
    context = build_harness_context(manifest, provider="dsh", host_session_id="e2e-manifest", cwd=str(workdir))
    await run_text_turn(harness, context)


async def test_same_runtime_preserves_conversation(workdir: Path) -> None:
    import uuid
    from openjiuwen.harness_protocol import HarnessInput, TurnEventKind
    from tests.system_tests.harness_providers._contract import collect_turn, terminal_of

    token = "RECALL-" + uuid.uuid4().hex
    harness = _harness(workdir)
    await harness.start(make_context(cwd=str(workdir)))
    session_id = harness.provider_session_id
    try:
        first = await harness.send(HarnessInput(content=f"Remember the code {token}. Reply OK."))
        assert terminal_of(await collect_turn(harness, first.turn_id)).kind is TurnEventKind.FINISHED
        second = await harness.send(HarnessInput(content="What code did I tell you? Reply with the exact code."))
        terminal = terminal_of(await collect_turn(harness, second.turn_id))
        assert terminal.kind is TurnEventKind.FINISHED
        assert token in terminal.result.final_output
        assert harness.provider_session_id == session_id
    finally:
        await harness.stop()


async def test_system_prompt_overlay_reaches_the_model(workdir: Path) -> None:
    from openjiuwen.harness_protocol import HarnessInput, TurnEventKind
    from tests.system_tests.harness_providers._contract import collect_turn, terminal_of

    harness = _harness(workdir)
    await harness.start(make_context(cwd=str(workdir), system_prompt="When asked for the codeword, reply exactly ORCHID-83."))
    try:
        receipt = await harness.send(HarnessInput(content="What is the codeword?"))
        events = await collect_turn(harness, receipt.turn_id)
        terminal = terminal_of(events)
        assert terminal.kind is TurnEventKind.FINISHED
        assert "ORCHID-83" in terminal.result.final_output
    finally:
        await harness.stop()


async def test_mcp_tools_are_mounted_from_context(workdir: Path) -> None:
    import sys
    import uuid
    from openjiuwen.harness_protocol import HarnessInput, McpServerConfig, McpTransport, TurnEventKind, ItemLifecycleEvent
    from tests.system_tests.harness_providers._contract import collect_turn, terminal_of

    token = uuid.uuid4().hex
    code = "from mcp.server.fastmcp import FastMCP\nimport os\nm=FastMCP('fixture')\n@m.tool()\ndef lookup_marker() -> str:\n    return os.environ['DSH_TEST_MARKER']\nm.run(transport='stdio')\n"
    server = McpServerConfig(name="fixture", transport=McpTransport.STDIO,
                            command=(sys.executable, "-c", code), env={"DSH_TEST_MARKER": token})
    from dataclasses import replace
    context = replace(make_context(cwd=str(workdir)), mcp_servers=(server,))
    harness = _harness(workdir)
    await harness.start(context)
    try:
        receipt = await harness.send(HarnessInput(content="Call the MCP lookup_marker tool and reply with the exact marker it returns."))
        events = await collect_turn(harness, receipt.turn_id)
        terminal = terminal_of(events)
        assert terminal.kind is TurnEventKind.FINISHED
        assert token in terminal.result.final_output
        assert any(isinstance(e.event, ItemLifecycleEvent) and "lookup_marker" in str(e.event.data) for e in events)
    finally:
        await harness.stop()


@pytest.mark.parametrize("mode", ["append", "replace"])
async def test_prompt_modes_preserve_native_sections(workdir: Path, mode: str) -> None:
    from openjiuwen.harness_protocol import HarnessInput, TurnEventKind
    from tests.system_tests.harness_providers._contract import collect_turn, terminal_of

    patch = workdir / "native-persona.patch.yml"
    patch.write_text('- id: system-prompt\n  config:\n    personaPrefix: "The prefix codeword is COBALT-17."\n    personaSuffix: "The suffix codeword is JADE-29."\n')
    config = DshHarnessConfig.from_mapping({**_config_values(workdir), "patches": [str(patch)], "system_prompt_mode": mode})
    harness = DshHarness(config)
    host = "The host codeword is AMBER-83. When asked, list only codewords given in your instructions."
    if mode == "append":
        host += " The literal token is {{unregistered_variable}}; keep it verbatim when asked."
    await harness.start(make_context(cwd=str(workdir), system_prompt=host))
    try:
        receipt = await harness.send(HarnessInput(content="List the codewords and any literal token from your instructions."))
        terminal = terminal_of(await collect_turn(harness, receipt.turn_id))
        assert terminal.kind is TurnEventKind.FINISHED
        assert "AMBER-83" in terminal.result.final_output
        assert "JADE-29" in terminal.result.final_output
        assert ("COBALT-17" in terminal.result.final_output) is (mode == "append")
        if mode == "append":
            assert "{{unregistered_variable}}" in terminal.result.final_output
    finally:
        await harness.stop()
