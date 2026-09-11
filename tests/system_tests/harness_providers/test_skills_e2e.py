# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real CLI skill discovery, resource copying and collision policies."""

import shutil
import uuid
from pathlib import Path

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, SkillSpec
from openjiuwen.harness_protocol import HarnessInput, TurnEventKind
from openjiuwen.harness_providers import create_harness, build_harness_context
from tests.system_tests.harness_providers._contract import collect_turn, terminal_of
from tests.system_tests.harness_providers.conftest import requires_claude, requires_codex, requires_dsh, dsh_home, dsh_default_model


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,relative", [
    pytest.param("claudecode", ".claude/skills", marks=requires_claude),
    pytest.param("codex", ".agents/skills", marks=requires_codex),
    pytest.param("dsh", ".dsh/skills", marks=requires_dsh),
])
@pytest.mark.parametrize("conflict", ["skip", "replace"])
async def test_skill_bundle_is_discovered_and_uses_its_resource(tmp_path: Path, provider: str, relative: str, conflict: str):
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    (source / "assets").mkdir(parents=True)
    new_marker, old_marker = uuid.uuid4().hex, uuid.uuid4().hex
    (source / "SKILL.md").write_text(
        "---\nname: portable-proof\ndescription: Use when asked for the portable skill proof marker.\n---\n"
        "Read assets/marker.txt relative to this skill directory and return its exact content. Do not guess the marker.\n"
    )
    (source / "assets/marker.txt").write_text(new_marker)
    existing = project / relative / "portable-proof"
    shutil.copytree(source, existing)
    (existing / "assets/marker.txt").write_text(old_marker)
    manifest = AgentTemplateSpec(agent_card=AgentCard(id="portable", name="portable"), skills=[SkillSpec(dir=str(source))])
    config = {"cwd": str(project), "skill_conflict": conflict}
    if provider == "claudecode":
        config["cli_path"] = shutil.which("claude")
    elif provider == "codex":
        config.update(codex_bin=shutil.which("codex"), bypass_approvals_and_sandbox=True)
    else:
        config.update(dsh_home=str(dsh_home()), **dsh_default_model())
    harness = create_harness(manifest, provider=provider, config=config)
    context = build_harness_context(manifest, provider=provider, host_session_id=uuid.uuid4().hex, cwd=str(project))
    await harness.start(context)
    try:
        receipt = await harness.send(HarnessInput(content="Use the portable-proof skill and give me the exact portable skill proof marker."))
        terminal = terminal_of(await collect_turn(harness, receipt.turn_id))
        assert terminal.kind is TurnEventKind.FINISHED
        expected = old_marker if conflict == "skip" else new_marker
        assert expected in terminal.result.final_output
        assert (existing / "assets/marker.txt").read_text() == expected
    finally:
        await harness.stop()
    assert (existing / "SKILL.md").is_file()


@pytest.mark.asyncio
@requires_dsh
async def test_dsh_minimal_profile_loads_skill_plugins(tmp_path: Path):
    from openjiuwen.harness_providers.dsh import DshHarness, DshHarnessConfig
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: minimal-proof\ndescription: Minimal skill fixture\n---\nSay ready.\n")
    harness = DshHarness(DshHarnessConfig(dsh_home=str(dsh_home()), profile="sdk-minimal", skills=(str(source),), cwd=str(tmp_path)))
    from openjiuwen.harness_protocol import HarnessContext
    await harness.start(HarnessContext(agent_name="minimal", agent_id="minimal", host_session_id=uuid.uuid4().hex, system_prompt="", cwd=str(tmp_path)))
    try:
        assert (tmp_path / ".dsh/skills/minimal-proof/SKILL.md").is_file()
    finally:
        await harness.stop()
