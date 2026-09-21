# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution failures must not enter structured-output repair loops."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import RSISysOperationRail
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.output import (
    invoke_member_optimizer_agent_structured,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"result_type": "error", "output": "Max iterations reached without completion"},
    ValueError("backend execution failed"),
])
async def test_execution_failure_is_not_retried_or_parsed(failure):
    invoke = AsyncMock()
    if isinstance(failure, Exception):
        invoke.side_effect = failure
    else:
        invoke.return_value = failure
    parse = Mock()
    repair = Mock()
    with pytest.raises(RuntimeError, match="execution failed"):
        await invoke_member_optimizer_agent_structured(
            agent=SimpleNamespace(invoke=invoke), agent_name="planner",
            user_message="full original evidence", session_id="planner-test",
            retry_limit=20, parse_response=parse, build_retry_message=repair,
        )
    assert invoke.await_count == 1
    parse.assert_not_called()
    repair.assert_not_called()


def test_planner_read_tools_exclude_shell_and_writes():
    registered = []
    agent = SimpleNamespace(
        card=SimpleNamespace(id="planner-test"),
        system_prompt_builder=SimpleNamespace(language="en"),
        ability_manager=SimpleNamespace(
            add_ability=lambda card, tool: registered.append(type(tool).__name__),
        ),
    )
    RSISysOperationRail(read_only=True, allow_shell=False).init(agent)
    assert set(registered) == {"ReadFileTool", "GlobTool", "ListDirTool", "GrepTool"}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["natural", "budget", "markup", "invalid_final"])
async def test_native_planner_preserves_evidence_without_restarting_reads(monkeypatch, tmp_path, outcome):
    from openjiuwen.core.foundation.llm import AssistantMessage, Model
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.rsi.harness_rsi.member_optimizer.action_planner import MemberActionPlannerAgent
    from openjiuwen.rsi.harness_rsi.member_optimizer.agents.profiles import ACTION_PLANNING
    from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
        MechanismAttributionReport,
        RoleAttributionReport,
    )

    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps({
        "model_client_config": {
            "client_provider": "OpenAI", "api_key": "test-key",
            "api_base": "http://test.invalid/v1", "verify_ssl": False,
        },
        "model_request_config": {"model": "test-model"},
    }), encoding="utf-8")
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("Existing component exports validate_input.", encoding="utf-8")
    calls = []
    plan = {"actions": [{"action_id": "repair_existing_skill"}], "metadata": {"source": "validate_input"}}
    markup = "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>list_files</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"

    async def invoke(self, **kwargs):
        calls.append(kwargs)
        if kwargs.get("tools"):
            if outcome == "natural" and len(calls) == 5:
                return AssistantMessage(content=json.dumps(plan))
            if outcome in {"markup", "invalid_final"} and len(calls) == 3:
                return AssistantMessage(content=markup)
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id=uuid.uuid4().hex, type="function", name="read_file",
                arguments=json.dumps({"file_path": str(evidence)}),
            )])
        assert "validate_input" in str(kwargs["messages"])
        assert all(message.role in {"system", "user"} for message in kwargs["messages"])
        payload = json.loads(kwargs["messages"][-1].content)
        assert "Original task:" in payload["request"]
        first_read = payload["collected_evidence"][0]
        assert json.loads(first_read["arguments"])["file_path"] == str(evidence)
        assert first_read["result"]["data"]["content"] == "     1\tExisting component exports validate_input."
        expected_reads = ACTION_PLANNING.max_iterations if outcome == "budget" else 2
        assert len(payload["collected_evidence"]) == expected_reads
        return AssistantMessage(content=markup if outcome == "invalid_final" else json.dumps(plan))

    monkeypatch.setattr(Model, "invoke", invoke)
    planner = MemberActionPlannerAgent(
        model_config_ref=str(model_path), workspace=tmp_path,
    )
    request = dict(
        targets=[], role_attribution_report=RoleAttributionReport(),
        mechanism_attribution_report=MechanismAttributionReport(), action_definitions=[],
        optimization_experience={"sibling_generation": {"candidate_id": uuid.uuid4().hex}},
    )
    if outcome == "invalid_final":
        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            await planner.create_plan(**request)
    else:
        assert await planner.create_plan(**request) == plan
    if outcome == "natural":
        assert len(calls) == 5
        assert all(call.get("tools") for call in calls)
    else:
        assert len(calls) == (ACTION_PLANNING.max_iterations + 1 if outcome == "budget" else 4)
        assert all(call.get("tools") for call in calls[:-1])
        assert not calls[-1].get("tools")


@pytest.mark.asyncio
async def test_recovery_does_not_hide_execution_errors():
    recovery = AsyncMock()
    with pytest.raises(RuntimeError, match="execution failed: provider unavailable"):
        await invoke_member_optimizer_agent_structured(
            agent=SimpleNamespace(invoke=AsyncMock(return_value={
                "result_type": "error", "output": "provider unavailable",
            })),
            agent_name="planner", user_message="original task", session_id=uuid.uuid4().hex,
            retry_limit=1, parse_response=json.loads, recover_response=recovery,
        )
    recovery.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"result_type": "error", "output": "Max iterations reached without completion"},
    {"output": "not a plan"},
])
async def test_zero_retry_budget_disables_finalization(response):
    recovery = AsyncMock()
    with pytest.raises(RuntimeError):
        await invoke_member_optimizer_agent_structured(
            agent=SimpleNamespace(invoke=AsyncMock(return_value=response)),
            agent_name="planner", user_message="original task", session_id=uuid.uuid4().hex,
            retry_limit=0, parse_response=json.loads, recover_response=recovery,
        )
    recovery.assert_not_called()
