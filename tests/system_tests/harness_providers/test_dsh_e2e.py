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
