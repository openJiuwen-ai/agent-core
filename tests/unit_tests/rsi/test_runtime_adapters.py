# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for RSI-owned adapters over upstream Core and Harness APIs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.rsi.harness_rsi.evaluator import runtime_adapters
from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import (
    RSIBashTool,
    RSISkillUseRail,
    run_agent_with_empty_response_recovery,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("shell_only", [False, True])
async def test_empty_baseline_supports_candidate_skill_through_native_plugin_loader(tmp_path: Path, shell_only):
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
        _enforce_container_sys_operation_rail,
        _single_harness_rails,
    )

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "harness_config.yaml").write_text("schema_version: '1.0'\nid: baseline\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    skill_dir = candidate / "skills" / "verify_patch"
    skill_dir.mkdir(parents=True)
    (candidate / "harness_config.yaml").write_text("schema_version: '1.0'\nid: candidate\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: verify_patch\ndescription: Verify a code patch before delivery.\n---\nRun targeted tests.\n",
        encoding="utf-8",
    )
    for package, expected in ((baseline, []), (candidate, ["verify_patch"])):
        rails = _single_harness_rails(None, harness_path=package, shell_only=shell_only)
        agent = create_deep_agent(
            model=MagicMock(), card=AgentCard(name="plugin-regression", description="test"),
            workspace=str(tmp_path / package.name / "workspace"),
            rails=[rail for rail in rails if not isinstance(rail, RSISkillUseRail)],
            enable_task_loop=False, auto_create_workspace=True, restrict_to_work_dir=False,
        )
        for rail in rails:
            if isinstance(rail, RSISkillUseRail):
                await agent.register_rail(rail)
        # Exercise the real plugin binder, not a mock that bypasses its prerequisites.
        await agent.load_plugin(str(package))
        if shell_only:
            _enforce_container_sys_operation_rail(agent)
        registered = agent.find_rails_by_type((RSISkillUseRail,))
        assert len(registered) == 1
        rail = registered[0]
        await rail.reload_skills()
        assert [skill.name for skill in rail.skills] == expected
        assert rail.trigger_at_task_start is True
        assert rail.include_tools is (not shell_only)
        if expected:
            assert rail.skills[0].description == "Verify a code patch before delivery."
            result = await rail._runtime_skill_tool.invoke(
                {"skill_name": "verify_patch", "relative_file_path": "SKILL.md"}
            )
            assert result.success
            assert "Run targeted tests." in result.data["skill_content"]


@pytest.mark.asyncio
async def test_rsi_bash_pipefail_preserves_pipeline_producer_status() -> None:
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(
        return_value=SimpleNamespace(
            code=StatusCode.SUCCESS.code,
            message="",
            data=SimpleNamespace(exit_code=0, stdout="ok\n", stderr=""),
        )
    )
    operation = MagicMock()
    operation.shell.return_value = shell

    result = await RSIBashTool(operation, pipefail=True).invoke({"command": "python -m pytest -q | tail -30"})

    assert result.success is True
    args, kwargs = shell.execute_cmd.await_args
    assert args[0] == "set -o pipefail; python -m pytest -q | tail -30"
    assert kwargs["shell_type"] == "bash"


@pytest.mark.asyncio
async def test_empty_response_recovery_is_owned_by_rsi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_agent(agent, inputs, session):
        calls.append({"agent": agent, "inputs": inputs, "session": session})
        if len(calls) == 1:
            return {"output": "", "result_type": "answer"}
        return {"output": "recovered", "result_type": "answer"}

    monkeypatch.setattr(runtime_adapters.Runner, "run_agent", fake_run_agent)

    result = await run_agent_with_empty_response_recovery(
        object(),
        {"query": "solve"},
        session="case-1",
    )

    assert result["output"] == "recovered"
    assert len(calls) == 2
    assert calls[0]["inputs"] == {"query": "solve"}
    assert "[RECOVERY]" in calls[1]["inputs"]["query"]
