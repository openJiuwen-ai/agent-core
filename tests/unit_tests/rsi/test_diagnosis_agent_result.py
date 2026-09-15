# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Keep failed agent execution out of successful diagnosis/format repair."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.rsi.harness_rsi import model_call
from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.agent_runtime import run_deep_agent_text
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    DeterministicSignals,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "Max iterations reached without completion",
        '{"target_ref": "unassigned"}',
    ],
)
async def test_failed_execution_is_not_a_successful_or_repairable_answer(monkeypatch, output):
    run = AsyncMock(return_value={"result_type": "error", "output": output})
    monkeypatch.setattr(Runner, "run_agent", run)

    with pytest.raises(RuntimeError):
        await analyzer._run_agent(object(), "Diagnose evidence", max_retries=3)

    assert run.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"result_type": "answer", "output": '{"target_ref": "unassigned"}'},
        {"output": '{"target_ref": "unassigned"}'},
        {"answer": '{"target_ref": "unassigned"}'},
        '{"target_ref": "unassigned"}',
    ],
)
async def test_successful_and_legacy_answers_remain_compatible(monkeypatch, result):
    run = AsyncMock(return_value=result)
    monkeypatch.setattr(Runner, "run_agent", run)

    raw = await run_deep_agent_text(
        object(),
        "Diagnose evidence",
        max_retries=0,
        operation_name="diagnosis",
    )

    assert json.loads(raw) == {"target_ref": "unassigned"}
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_transient_error_result_keeps_existing_service_retry(monkeypatch):
    run = AsyncMock(
        side_effect=[
            {"result_type": "error", "output": "HTTP 502 Bad Gateway"},
            {"result_type": "answer", "output": '{"target_ref": "unassigned"}'},
        ]
    )
    monkeypatch.setattr(Runner, "run_agent", run)
    monkeypatch.setattr(model_call.asyncio, "sleep", AsyncMock())

    raw = await analyzer._run_agent(object(), "Diagnose evidence", max_retries=1)

    assert json.loads(raw) == {"target_ref": "unassigned"}
    assert run.await_count == 2
    assert len({call.kwargs["session"] for call in run.await_args_list}) == 2
    assert all(call.kwargs["inputs"]["query"] == "Diagnose evidence" for call in run.await_args_list)


@pytest.mark.asyncio
async def test_step_exhaustion_is_reported_once_and_next_case_still_runs(monkeypatch):
    strategy = analyzer.DiagnosisAgentStrategy(
        EvaluationResultAnalyzerConfig(
            model_config_ref="unused.yaml",
            diagnosis_agent_max_retries=3,
        )
    )
    monkeypatch.setattr(strategy, "_build_agent", AsyncMock(return_value=object()))
    monkeypatch.setattr(analyzer, "_prepare_diagnosis_evidence", lambda **kwargs: False)
    monkeypatch.setattr(analyzer, "_build_diagnosis_prompt", lambda **kwargs: "Diagnose evidence")
    run = AsyncMock(
        side_effect=[
            {"result_type": "error", "output": "Max iterations reached without completion"},
            {
                "result_type": "answer",
                "output": json.dumps(
                    {
                        "target_ref": "unassigned",
                        "confidence": "low",
                        "root_cause": "No supported cause in this independent case.",
                        "summary": "Insufficient evidence.",
                    }
                ),
            },
        ]
    )
    monkeypatch.setattr(Runner, "run_agent", run)
    cases = [
        CaseAnalysisInput(
            case_id=case_id,
            status="failed",
            score=0.0,
            input="input",
            expected=None,
            response="response",
            error="",
            evaluation_method="unit_test",
            evaluation_passed=False,
            evaluation_reason="failed",
            evaluation_metadata={},
            trace_path="",
            result_path="",
        )
        for case_id in ("exhausted", "following")
    ]

    results = await strategy._per_case_diagnosis(cases, DeterministicSignals(method="unit_test"), None)

    assert run.await_count == 2
    assert results[0]["case_id"] == "exhausted"
    assert results[0]["analysis_failed"] is True
    assert results[0]["diagnosis_error_type"] == "agent_runtime"
    assert "Max iterations" in results[0]["error"]
    assert results[1]["case_id"] == "following"
    assert not results[1].get("analysis_failed")


@pytest.mark.asyncio
async def test_budget_note_and_final_tool_boundary_reach_actual_deep_agent_model(monkeypatch, tmp_path):
    from openjiuwen.core.foundation.llm import AssistantMessage, Model
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import agent_runtime

    monkeypatch.setattr(
        agent_runtime,
        "load_model_config_ref",
        lambda _: {
            "model_client_config": {
                "client_provider": "OpenAI",
                "api_key": "test-key",
                "api_base": "http://test.invalid/v1",
                "verify_ssl": False,
            },
            "model_request_config": {"model": "test-model"},
        },
    )
    calls = []

    async def invoke(self, **kwargs):
        calls.append(kwargs)
        if kwargs.get("tools"):
            return AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id=uuid.uuid4().hex,
                        type="function",
                        name="read_file",
                        arguments=json.dumps({"file_path": str(tmp_path / "evidence.txt")}),
                    )
                ],
            )
        return AssistantMessage(content='{"diagnoses": [{"target_ref": "unassigned"}]}')

    monkeypatch.setattr(Model, "invoke", invoke)
    (tmp_path / "evidence.txt").write_text("An observation, not an answer.", encoding="utf-8")
    runtime = agent_runtime.DiagnosisAgentRuntime(
        EvaluationResultAnalyzerConfig(
            model_config_ref="test.yaml",
            diagnosis_agent_max_iterations=2,
        )
    )
    agent = await runtime.build_agent(str(tmp_path), system_prompt=analyzer.DIAGNOSIS_SYSTEM_PROMPT)
    for _ in range(2):
        result = await Runner.run_agent(
            agent=agent, inputs={"query": "Inspect evidence and diagnose."}, session=f"budget_test_{uuid.uuid4().hex}"
        )
        assert result.get("result_type") != "error"
        assert json.loads(result["output"])["diagnoses"][0]["target_ref"] == "unassigned"
    assert len(calls) == 4
    for first, final in (calls[:2], calls[2:]):
        assert first["tools"]
        assert "Investigation turn 1/2" in first["messages"][-1].content
        assert not final.get("tools")
        assert "final turn" in final["messages"][-1].content
        assert "Preserve uncertainty" in final["messages"][-1].content
