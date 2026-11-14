# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Consumers recognize structured exhaustion independently of display language."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_runtime
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.output import invoke_member_optimizer_agent_structured


@pytest.mark.asyncio
async def test_judge_detects_localized_exhaustion(tmp_path, monkeypatch):
    agent = MagicMock()
    agent.configured_rails.return_value = []
    agent.cleanup_task_resources = AsyncMock()
    monkeypatch.setattr(judge_runtime, "inline_evidence", lambda *_a, **_kw: None)
    monkeypatch.setattr(judge_runtime, "build_judge_agent", lambda *_a, **_kw: agent)
    monkeypatch.setattr(
        Runner,
        "run_agent",
        AsyncMock(
            return_value={
                "result_type": "error",
                "output": "达到迭代上限",
                "termination_reason": "max_iterations",
            }
        ),
    )
    with pytest.raises(judge_runtime.JudgeIterationLimitError):
        await judge_runtime.run_judge_agent(EvaluatorConfig(), tmp_path, "judge", tmp_path / "log")
    agent.cleanup_task_resources.assert_awaited_once()


@pytest.mark.asyncio
async def test_optimizer_recovers_localized_exhaustion():
    agent = MagicMock(card=None)
    agent.invoke = AsyncMock(
        return_value={
            "result_type": "error",
            "output": "达到迭代上限",
            "termination_reason": "max_iterations",
        }
    )
    recover = AsyncMock(return_value={"output": '{"done": true}', "result_type": "answer"})
    result = await invoke_member_optimizer_agent_structured(
        agent=agent,
        agent_name="optimizer",
        user_message="improve",
        session_id="limit-test",
        retry_limit=1,
        parse_response=json.loads,
        recover_response=recover,
    )
    assert result == {"done": True}
    agent.invoke.assert_awaited_once()
    recover.assert_awaited_once()
