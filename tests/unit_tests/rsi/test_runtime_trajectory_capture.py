# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-process solver runs must archive the calls actually executed."""

import json

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator import case_backend
from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import RSISysOperationRail
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_rate", [1.0, 0.0])
@pytest.mark.parametrize("recover_empty", [False, True])
async def test_native_solver_archives_real_model_and_tool_calls(tmp_path, monkeypatch, sample_rate, recover_empty):
    from openjiuwen.core.single_agent.rail.base import AgentRail
    from openjiuwen.extensions.observability.config import ObservabilityConfig
    from openjiuwen.extensions.observability.setup import init_observability

    class ReminderRail(AgentRail):
        async def after_tool_call(self, ctx):
            ctx.push_steering("CAPTURE_TEST: check the written answer before finalizing.")

    init_observability(ObservabilityConfig(
        exporter="file", traces_dir=str(tmp_path / "otel"), sample_rate=sample_rate,
    ))
    client = MockLLMModel()
    client.set_responses([
        *([create_text_response("")] if recover_empty else []),
        create_tool_call_response("write_file", '{"file_path":"answer.txt","content":"captured"}'),
        create_text_response("Written answer.txt"),
    ])
    # Patch the transport before Model wraps it with the real callback framework.
    # Model.invoke/stream, Runner, DeepAgent and tools remain unpatched.
    monkeypatch.setattr("openjiuwen.core.foundation.llm.model.create_model_client", lambda **kwargs: client)
    model = Model(
        ModelClientConfig(client_provider="OpenAI", api_key="test-only", api_base="http://unused"),
        ModelRequestConfig(model_name="controlled-model"),
    )
    monkeypatch.setattr(case_backend, "load_member_optimizer_model", lambda ref: model)
    native_factory = case_backend.create_deep_agent

    def factory(**kwargs):
        return native_factory(**kwargs, enable_read_image_multimodal=False)

    monkeypatch.setattr(case_backend, "create_deep_agent", factory)
    monkeypatch.setattr(case_backend, "_single_harness_rails", lambda *args, **kwargs: [RSISysOperationRail(), ReminderRail()])
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "harness_config.yaml").write_text("schema_version: '1.0'\nid: capture_test\n", encoding="utf-8")
    output = tmp_path / "case"
    backend = case_backend.SingleHarnessExecutionBackend(EvaluatorConfig(solver_max_iterations=7))
    try:
        result = await backend.execute(
            case={"case_id": "capture", "input": "Write answer.txt"},
            output_dir=str(output), session_id="capture-session", harness_refs={"solver": str(plugin)},
        )
        assert result.execution_status == "passed", result.error
        assert (output / "workspace" / "answer.txt").is_file(), (result.response, client.call_count, client.call_history)
        assert "CAPTURE_TEST" in str(client.call_history[-1])
        evidence = result.metadata["execution_evidence"]
        assert result.metadata["solver_max_iterations"] == 7
        role_file = output / "tr" / "solver.jsonl"
        if sample_rate == 0:
            assert evidence["status"] == "incomplete"
            assert "run_root_not_recording" in evidence["reasons"]
            assert evidence["tool_call_count"] is None
            return
        assert role_file.is_file(), "Real executed calls have no native role archive"
        archive = json.loads(role_file.read_text())
        expected_kinds = [*(["llm"] if recover_empty else []), "llm", "tool", "llm"]
        assert [step["kind"] for step in archive["steps"]] == expected_kinds
        tool = next(step["detail"] for step in archive["steps"] if step["kind"] == "tool")
        assert tool["tool_name"] == "write_file"
        assert "captured" in json.dumps(tool["call_args"])
        assert tool["call_result"]
        evidence = result.metadata["execution_evidence"]
        assert evidence["status"] == "complete", evidence
        assert evidence["tool_call_count"] == 1
        assert evidence["llm_call_count"] == (3 if recover_empty else 2)
        assert evidence["timing_complete"] is True
        assert result.metadata["solver_max_iterations"] == 7
    finally:
        from openjiuwen.harness.observability.setup import release_observability
        from openjiuwen.extensions.observability.setup import shutdown_observability
        release_observability()
        shutdown_observability()
        await Runner.stop()


def test_execution_evidence_marks_missing_archive_unknown(tmp_path):
    from openjiuwen.rsi.harness_rsi.evaluator.trajectory_paths import role_execution_evidence

    evidence = role_execution_evidence(tmp_path, "solver", root_recording=True)
    assert evidence["status"] == "incomplete"
    assert "missing_role_archive" in evidence["reasons"]
    assert evidence["tool_call_count"] is None


def test_execution_evidence_marks_malformed_archive_unknown(tmp_path):
    from openjiuwen.rsi.harness_rsi.evaluator.trajectory_paths import role_execution_evidence

    (tmp_path / "solver.jsonl").write_text('{"steps": [', encoding="utf-8")
    evidence = role_execution_evidence(tmp_path, "solver", root_recording=True)
    assert evidence["status"] == "incomplete"
    assert "invalid_role_archive" in evidence["reasons"]
    assert evidence["tool_call_count"] is None


def test_normalized_execution_evidence_preserves_unknown(tmp_path):
    from openjiuwen.rsi.harness_rsi.evaluator.case_runner import _behavior_trace, _normalized_trace_from_events

    evidence = {"status": "incomplete", "reasons": ["missing_role_archive"], "tool_call_count": None}
    behavior = _behavior_trace({"execution_evidence": evidence})
    assert behavior["execution_evidence"] == evidence
    normalized = _normalized_trace_from_events(
        case_id="missing", events=[], judge_result=None, execution_evidence=evidence,
    )
    assert normalized["execution_evidence"] == evidence


@pytest.mark.parametrize("problem", ["missing_tool_result", "missing_tool_arguments", "redacted_arguments", "count_mismatch", "truncated_arguments", "invalid_detail"])
def test_execution_evidence_keeps_incomplete_calls_and_timing_unknown(tmp_path, problem):
    from openjiuwen.rsi.harness_rsi.evaluator.trajectory_paths import role_execution_evidence

    steps = [{"kind": "llm", "detail": {}}, {
        "kind": "tool", "detail": {"tool_name": "bash", "call_args": {"command": "echo ok"}, "call_result": "ok"},
    }]
    if problem == "missing_tool_result":
        del steps[1]["detail"]["call_result"]
    elif problem == "missing_tool_arguments":
        del steps[1]["detail"]["call_args"]
    elif problem == "redacted_arguments":
        steps[1]["detail"]["call_args"] = "sha256:123456789abcdef0"
    elif problem == "truncated_arguments":
        steps[1]["detail"]["call_args"] = {"command": "echo ...[truncated 1500 chars]"}
    elif problem == "invalid_detail":
        steps[1]["detail"] = None
    (tmp_path / "solver.jsonl").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    evidence = role_execution_evidence(
        tmp_path, "solver", root_recording=True,
        expected_model_calls=1, expected_tool_calls=2 if problem == "count_mismatch" else 1,
    )
    assert evidence["timing_complete"] is False
    if problem == "truncated_arguments":
        assert evidence["status"] == "complete"
        assert evidence["payloads_truncated"] is True
    else:
        assert evidence["status"] == "incomplete"


def test_normalized_role_trace_marks_bounded_projection_incomplete(tmp_path, monkeypatch):
    from openjiuwen.rsi.harness_rsi.evaluator import case_runner

    monkeypatch.setattr(case_runner, "_MAX_ROLE_TRACE_MESSAGES", 2)
    (tmp_path / "solver.jsonl").write_text(json.dumps({"steps": [
        {"kind": "tool", "detail": {"tool_name": f"probe_{index}", "call_args": {}, "call_result": "ok"}}
        for index in range(4)
    ]}), encoding="utf-8")
    # The native exporter WAL must not turn into a second fake solver role.
    (tmp_path / "traces-2026-10-22.jsonl").write_text('{"resourceSpans": []}\n', encoding="utf-8")
    traces = case_runner._normalized_role_traces_from_trajectory_dir(case_id="bounded", trajectory_dir=tmp_path)
    assert len(traces) == 1
    assert traces[0]["projection_complete"] is False
    assert len(traces[0]["messages"]) == 2
