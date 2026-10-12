# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The workflow flavour of the CLI member runtime: no team-join injection."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.external.cli_agent import spawn as spawn_mod
from openjiuwen.agent_teams.external.cli_agent.spawn import build_cli_workflow_runtime
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliModelConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_agent", ["claude", "codex"])
async def test_workflow_runtime_has_no_team_join_and_no_mcp(monkeypatch, cli_agent):
    """A workflow member is dispatched by the script, never chats with the team."""
    captured: dict = {}

    class _Harness:
        def __init__(self, config, **kw):
            captured["config"] = config

        card = type("C", (), {"name": cli_agent})()

    monkeypatch.setattr(spawn_mod, "ClaudeCodeHarness", _Harness)
    monkeypatch.setattr(spawn_mod, "CodexHarness", _Harness)
    entry = ExternalCliAgentSpec(
        cli_agent=cli_agent,
        cwd="C:/proj",
        env={"MY_VAR": "1"},
    )
    runtime = await build_cli_workflow_runtime(
        cli_agent=cli_agent,
        entry=entry,
        member_name="wf-r1-ext-abc",
        member_agent_id="t_wf-r1-ext-abc",
        session_id="s1",
        team_name="t",
        system_prompt="you are a swarmflow worker",
        cwd="C:/proj/wt",
        model_config=ExternalCliModelConfig(model="declared-model"),
    )
    config = captured["config"]
    assert "OPENJIUWEN_TEAM_JOIN" not in config.env
    assert config.env["MY_VAR"] == "1"
    assert config.cwd == "C:/proj/wt"
    assert config.model.model == "declared-model"
    assert runtime.inject_mcp is False
    assert runtime._extra_mcp_servers == []
    ctx = runtime._context_source
    assert ctx.agent_name == "wf-r1-ext-abc"
    assert ctx.agent_id == "t_wf-r1-ext-abc"
    assert ctx.host_session_id == "s1"
    assert ctx.system_prompt == "you are a swarmflow worker"
    assert ctx.metadata["team_name"] == "t"


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_agent", ["claude", "codex"])
async def test_workflow_runtime_model_config_reaches_harness(monkeypatch, cli_agent):
    """model_config is used verbatim: name+effort pass through, endpoint fields stay empty."""
    captured: dict = {}

    class _Harness:
        def __init__(self, config, **kw):
            captured["config"] = config

        card = type("C", (), {"name": cli_agent})()

    monkeypatch.setattr(spawn_mod, "ClaudeCodeHarness", _Harness)
    monkeypatch.setattr(spawn_mod, "CodexHarness", _Harness)
    entry = ExternalCliAgentSpec(cli_agent=cli_agent)
    await build_cli_workflow_runtime(
        cli_agent=cli_agent, entry=entry, member_name="m", member_agent_id="t_m", session_id="s1",
        team_name="t", system_prompt="p", cwd=None,
        model_config=ExternalCliModelConfig(model="gpt-5-codex", effort="medium"),
    )
    config = captured["config"].model
    assert config.model == "gpt-5-codex"
    assert config.effort == "medium"
    assert config.api_base is None
    assert config.api_key is None
    if cli_agent == "codex":  # Claude's model config has no provider field
        assert config.provider is None


@pytest.mark.asyncio
async def test_workflow_runtime_rejects_adapter_backends():
    entry = ExternalCliAgentSpec(cli_agent="gemini")
    with pytest.raises(ValueError, match="claude, codex"):
        await build_cli_workflow_runtime(
            cli_agent="gemini", entry=entry, member_name="m", member_agent_id="t_m", session_id="s1",
            team_name="t", system_prompt="p", cwd=None,
        )


@pytest.mark.asyncio
async def test_workflow_runtime_forwards_seed_checkpoint(monkeypatch):
    class _Harness:
        def __init__(self, config, **kw):
            pass

        card = type("C", (), {"name": "codex"})

    monkeypatch.setattr(spawn_mod, "CodexHarness", _Harness)
    runtime = await build_cli_workflow_runtime(
        cli_agent="codex", entry=ExternalCliAgentSpec(cli_agent="codex"), member_name="m", member_agent_id="t_m",
        session_id="s1", team_name="t", system_prompt="p", cwd=None,
        resume_external_backend=True, require_checkpoint=True, seed_checkpoint={"thread_id": "thr_child"},
    )
    assert runtime._seed_checkpoint == {"thread_id": "thr_child"}
