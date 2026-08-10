# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit coverage for Symphony's fail-soft evaluation suite."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, cast

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.evaluation import (
    AccuracyEvaluator,
    CapabilitySelectionEvaluator,
    CompletenessEvaluator,
    CompositionEffectivenessEvaluator,
    EvaluationContext,
    EvaluationSuite,
    EvaluationWindow,
    LatencyEvaluator,
)
from openjiuwen.symphony.models import (
    CapabilityFingerprint,
    EvaluationCase,
    EvidenceRef,
    Latency,
    MetricResult,
    MetricStatus,
)


def fingerprint(**updates: Any) -> CapabilityFingerprint:
    values: dict[str, Any] = {
        "capability_id": "weather",
        "capability_type": "skill",
        "name": "Weather",
        "description": "Return current weather for a requested city.",
        "classification": "information",
        "tags": ("weather",),
        "content_hash": "sha256:weather",
    }
    values.update(updates)
    return CapabilityFingerprint(**values)


def case(**updates: Any) -> EvaluationCase:
    values: dict[str, Any] = {
        "case_id": "case-weather",
        "capability_id": "weather",
        "capability_type": "skill",
        "query": "What is the weather?",
        "expected_output": "sunny",
        "message": (
            {"role": "user", "content": "What is the weather?"},
            {"role": "assistant", "content": "sunny"},
        ),
        "output": "sunny",
        "success": True,
        "latency": Latency(ttft=100, e2e=100),
        "event_time": datetime(2026, 8, 3, tzinfo=UTC),
    }
    values.update(updates)
    return EvaluationCase(**values)


def metric_map(result: Any) -> dict[str, MetricResult]:
    return {metric.metric_id: metric for metric in result.metrics}


class FakeLLM:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.messages: list[Any] = []

    async def invoke(self, messages: Any, **kwargs: Any) -> Any:
        self.messages.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def function_call(call_id: str, name: str, arguments: str = "{}") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def assistant_calls(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def prompt_payload(llm: FakeLLM, index: int = 0) -> dict[str, Any]:
    prompt = llm.messages[index][0]["content"]
    return json.loads(prompt.split("Evaluation data:\n", maxsplit=1)[1])


async def judge(
    evaluators: list[Any],
    response: Any,
    trace: EvaluationCase,
    metric_ids: tuple[str, ...],
) -> tuple[tuple[MetricResult, ...], FakeLLM]:
    llm = FakeLLM(response)
    metrics = await EvaluationSuite(evaluators, llm=llm, enable_llm=True).evaluate_case(
        fingerprint(), trace, metric_ids=metric_ids
    )
    return metrics, llm


@pytest.mark.asyncio
async def test_accuracy_prompt_uses_event_time_and_migrated_rubric() -> None:
    trace = case(
        expected_output=None,
        output="The event has happened.",
        message=(
            {"role": "user", "content": "Did the event happen?"},
            {"role": "assistant", "content": "The event has happened."},
        ),
        event_time=datetime(2026, 8, 3, 8, 30, tzinfo=UTC),
    )

    _, llm = await judge([AccuracyEvaluator()], '{"score": 1, "reason": "工具结果支持该回答。"}', trace, ("accuracy",))

    prompt = llm.messages[0][0]["content"]
    payload = prompt_payload(llm)
    assert payload["reference_time"] == "2026-08-03T08:30:00+00:00"
    assert payload["reference_time_source"] == "event_time"
    for phrase in (
        "准确性三值评分标准",
        "请先判断本次准确性评估是否适用",
        "用户 query 与 Evaluation data 中 fingerprint.description 描述的 Skill",
        "完整 message 和可选 output 中没有任何可供用户使用",
        "工具结果中如果包含明确、完整、实际可作为会话结果的自然语言回答",
        "事实证据不足、缺少后续确认或评估模型不了解新知识",
        "只要其中存在属于当前 Skill 的实质子意图",
        "新知识/新产品场景",
        "reference_time 是本次评估的权威当前时间",
        "工具内容的发布时间与其描述的事件日期是两个不同时间",
        "缺少事件发生后的二次确认属于证据不足",
        "证据不足不等于事实错误，也不等于证据矛盾",
        "判定 score=0 时，必须指出回答中的具体事实",
        "新闻发布时间早于事件日期本身不构成冲突",
        "如果数据不完整但不存在明确错误或相反证据",
    ):
        assert phrase in prompt
    assert "Return only JSON with score 0, 1, or null and a concise reason." in prompt
    assert "When score is null, reason must state which not-applicable condition applies." in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace",
    [
        case(expected_output=None, output="面向用户的回答"),
        case(
            expected_output=None,
            output=None,
            message=(
                {"role": "user", "content": "查一下附近的诊所"},
                assistant_calls(function_call("call-search", "poi_search", '{"keyword":"诊所"}')),
            ),
        ),
    ],
)
async def test_accuracy_accepts_explicit_null_as_llm_not_applicable(trace: EvaluationCase) -> None:
    metrics, llm = await judge(
        [AccuracyEvaluator()],
        '{"score": null, "reason": "本次准确性评估不适用。"}',
        trace,
        ("accuracy",),
    )

    metric = metrics[0]
    assert metric.status == MetricStatus.NOT_APPLICABLE
    assert metric.score is None
    assert metric.reason == "本次准确性评估不适用。"
    assert metric.details == {
        "not_applicable_code": "llm_not_applicable",
        "evaluation_method": "llm",
    }
    assert metric.evidence[0].evidence_type == "llm_judgment"
    assert len(llm.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '{"reason": "缺少 score。"}',
        '{"score": null, "reason": ""}',
        '{"score": 0.5, "reason": "非法分值。"}',
    ],
)
async def test_accuracy_rejects_malformed_or_invalid_three_value_response(response: str) -> None:
    metrics, _ = await judge(
        [AccuracyEvaluator()], response, case(expected_output=None, output="面向用户的回答"), ("accuracy",)
    )

    assert metrics[0].status == MetricStatus.ERROR
    assert metrics[0].score is None


@pytest.mark.asyncio
async def test_non_accuracy_llm_evaluator_rejects_null_and_keeps_binary_prompt() -> None:
    metrics, llm = await judge(
        [CompletenessEvaluator()],
        '{"score": null, "reason": "不能用于完整性评分。"}',
        case(expected_output=None, output="面向用户的回答"),
        ("completeness",),
    )

    assert metrics[0].status == MetricStatus.ERROR
    prompt = llm.messages[0][0]["content"]
    assert "Return only JSON with score 0 or 1 and a concise reason." in prompt
    assert "0, 1, or null" not in prompt


@pytest.mark.asyncio
async def test_accuracy_prompt_uses_current_utc_time_without_polluting_completeness() -> None:
    trace = case(expected_output=None, output="Current answer.", event_time=None)
    before = datetime.now(UTC)

    _, llm = await judge(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        '{"score": 1, "reason": "回答没有明确错误。"}',
        trace,
        ("accuracy", "completeness"),
    )

    after = datetime.now(UTC)
    accuracy_payload = prompt_payload(llm)
    reference_time = datetime.fromisoformat(accuracy_payload["reference_time"])
    assert accuracy_payload["reference_time_source"] == "evaluation_time"
    assert reference_time.tzinfo is not None
    assert before <= reference_time.astimezone(UTC) <= after
    assert "reference_time" not in prompt_payload(llm, 1)
    assert "reference_time_source" not in prompt_payload(llm, 1)


@pytest.mark.asyncio
async def test_accuracy_prompt_omits_empty_case_fields_without_changing_public_case() -> None:
    trace = case(
        query="",
        inputs=None,
        expected_output=None,
        output=None,
        success=False,
        latency=None,
        event_time=None,
    )

    public_payload = trace.model_dump(mode="json")
    assert public_payload["query"] == ""
    assert public_payload["inputs"] is None
    assert public_payload["output"] is None
    assert public_payload["success"] is False

    _, llm = await judge(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        '{"score": 1, "reason": "ok"}',
        trace,
        ("accuracy", "completeness"),
    )

    accuracy_payload = prompt_payload(llm)
    completeness_payload = prompt_payload(llm, 1)
    accuracy_case = accuracy_payload["case"]
    assert "query" not in accuracy_case
    for field_name in ("inputs", "expected_output", "output", "latency", "event_time"):
        assert field_name not in accuracy_case
    assert accuracy_case["success"] is False
    assert accuracy_case["message"] == public_payload["message"]
    completeness_case = completeness_payload["case"]
    assert completeness_case["query"] == ""
    assert completeness_case["inputs"] is None
    assert completeness_case["expected_output"] is None
    assert completeness_case["output"] is None
    assert completeness_case["latency"] is None
    assert completeness_case["event_time"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "output", "evidence"),
    [
        (({"role": "assistant", "content": "assistant evidence"},), None, "assistant evidence"),
        (({"role": "assistant", "content": "assistant evidence"},), "NO_REPLY", "assistant evidence"),
        ((), "standalone output evidence", "standalone output evidence"),
    ],
)
async def test_output_metrics_use_message_or_standalone_output(
    message: tuple[dict[str, Any], ...],
    output: Any,
    evidence: str,
) -> None:
    trace = case(expected_output=None, message=message, output=output)
    metrics, llm = await judge(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        '{"score": 1, "reason": "the supplied evidence is sufficient"}',
        trace,
        ("accuracy", "completeness"),
    )

    assert all(metric.status == MetricStatus.PASS for metric in metrics)
    assert len(llm.messages) == 2
    assert all(evidence in messages[0]["content"] for messages in llm.messages)


@pytest.mark.asyncio
async def test_accuracy_exact_matches_output_only_when_output_is_present() -> None:
    matched = await EvaluationSuite.default().evaluate(
        fingerprint(),
        [case(expected_output="sunny", output="sunny")],
        metric_ids=("accuracy",),
    )
    missing = await EvaluationSuite.default().evaluate(
        fingerprint(),
        [case(expected_output=None, output=None, message=())],
        metric_ids=("accuracy",),
    )

    assert metric_map(matched)["accuracy"].details["evaluation_method"] == "exact_match"
    assert metric_map(missing)["accuracy"].details["not_applicable_code"] == "missing_output"


@pytest.mark.asyncio
async def test_default_suite_registers_all_metrics_and_keeps_llm_off() -> None:
    llm = FakeLLM('{"score": 1, "reason": "good"}')
    suite = EvaluationSuite.default(llm=llm)

    result = await suite.evaluate(fingerprint(), [case()])
    metrics = metric_map(result)

    assert suite.metric_ids == (
        "structure_conformance",
        "description_quality",
        "classification_consistency",
        "success_rate",
        "latency",
        "accuracy",
        "completeness",
        "capability_selection",
        "composition_effectiveness",
    )
    assert metrics["structure_conformance"].status == "pass"
    assert metrics["description_quality"].status == "not_applicable"
    assert metrics["classification_consistency"].status == "not_applicable"
    assert metrics["completeness"].status == "not_applicable"
    assert all(not metric.failures for metric in metrics.values() if metric.status == "not_applicable")
    assert llm.messages == []
    assert result.score is None
    assert result.details["composite_score"] == "not_computed"


@pytest.mark.asyncio
async def test_static_missing_fields_produce_findings_and_suggestions() -> None:
    result = await EvaluationSuite.default().evaluate(
        fingerprint(description="", classification="", tags=()),
    )
    metrics = metric_map(result)

    assert metrics["structure_conformance"].status == "pass"
    assert metrics["description_quality"].status == "fail"
    assert metrics["description_quality"].failures[0].code == "missing_description"
    assert metrics["description_quality"].suggestions
    assert metrics["classification_consistency"].status == "fail"
    assert metrics["classification_consistency"].failures[0].code == "missing_classification"


@pytest.mark.asyncio
async def test_window_aggregates_success_and_separate_latency_observations() -> None:
    cases = [
        case(
            case_id="inside-1",
            success=True,
            latency=Latency(ttft=50, e2e=30_000),
        ),
        case(
            case_id="inside-2",
            success=False,
            event_time=datetime(2026, 8, 3, 1, tzinfo=UTC),
            latency=Latency(ttft=70),
        ),
        case(
            case_id="outside",
            event_time=datetime(2026, 8, 4, tzinfo=UTC),
            latency=Latency(ttft=50, e2e=999_999),
        ),
    ]
    result = await EvaluationSuite.default().evaluate(
        fingerprint(),
        cases,
        window=EvaluationWindow(
            start=datetime(2026, 8, 3, tzinfo=UTC),
            end=datetime(2026, 8, 4, tzinfo=UTC),
            label="daily",
        ),
    )
    metrics = metric_map(result)

    assert result.sample_count == 2
    assert result.score is None
    window_details = result.details["window"]
    assert isinstance(window_details, dict)
    assert window_details["label"] == "daily"
    assert metrics["success_rate"].score == pytest.approx(0.5)
    assert metrics["success_rate"].status == "fail"
    assert metrics["latency"].status == "observed"
    assert metrics["latency"].score is None
    latency_details = dict(metrics["latency"].details)
    case_references = latency_details.pop("case_references")
    assert isinstance(case_references, list)
    assert len(case_references) == 2
    assert all(isinstance(reference, str) and reference.startswith("case:sha256:") for reference in case_references)
    assert latency_details == {
        "window_result_count": 2,
        "pass_count": 0,
        "fail_count": 0,
        "not_applicable_count": 0,
        "error_count": 0,
        "observed_count": 2,
        "ttft": {
            "avg_ms": 60.0,
            "p50_ms": 60.0,
            "p95_ms": 69.0,
            "max_ms": 70.0,
            "count": 2,
        },
        "e2e": {
            "avg_ms": 30_000.0,
            "p50_ms": 30_000.0,
            "p95_ms": 30_000.0,
            "max_ms": 30_000.0,
            "count": 1,
        },
    }


@pytest.mark.parametrize(
    ("latency", "expected_details"),
    [
        (Latency(ttft=5_000, e2e=30_000), {"ttft_ms": 5_000.0, "e2e_ms": 30_000.0}),
        (Latency(ttft=5_000), {"ttft_ms": 5_000.0}),
        (Latency(e2e=30_000), {"e2e_ms": 30_000.0}),
    ],
)
def test_latency_records_ttft_and_e2e_without_scoring(
    latency: Latency,
    expected_details: dict[str, float],
) -> None:
    context = EvaluationContext(fingerprint=fingerprint(), case=case(latency=latency))
    metric = LatencyEvaluator().evaluate(context)

    assert metric.score is None
    assert metric.status == MetricStatus.OBSERVED
    assert metric.details == expected_details
    assert metric.reason == "Latency was observed without applying a target or admission threshold."


@pytest.mark.parametrize("latency", [None, Latency()])
def test_latency_without_observed_values_is_not_applicable(latency: Latency | None) -> None:
    metric = LatencyEvaluator().evaluate(EvaluationContext(fingerprint=fingerprint(), case=case(latency=latency)))

    assert metric.status == MetricStatus.NOT_APPLICABLE
    assert metric.score is None
    assert metric.details["not_applicable_code"] == "missing_latency_observation"


def test_latency_is_observational_and_has_no_scenario_configuration() -> None:
    configured = fingerprint(
        static_data={"evaluation": {"latency_scenario": "realtime_interaction"}},
        metadata={"latency_target_ms": 1},
    )
    metric = LatencyEvaluator().evaluate(
        EvaluationContext(fingerprint=configured, case=case(latency=Latency(ttft=6_000, e2e=90_001)))
    )

    assert metric.status == MetricStatus.OBSERVED
    assert metric.score is None
    assert metric.details == {"ttft_ms": 6_000.0, "e2e_ms": 90_001.0}
    with pytest.raises(TypeError):
        LatencyEvaluator(scenario="short_task")


@pytest.mark.asyncio
async def test_trace_can_reference_evaluated_capability_through_a_call() -> None:
    trace = EvaluationCase(
        case_id="orchestrator-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="What is the weather?",
        message=(
            {"role": "user", "content": "What is the weather?"},
            assistant_calls(function_call("weather-step", "weather", '{"city":"Shenzhen"}')),
            {"role": "tool", "tool_call_id": "weather-step", "content": "sunny"},
        ),
        success=True,
        latency=Latency(ttft=10, e2e=125),
    )

    result = await EvaluationSuite.default().evaluate(fingerprint(), [trace])
    metrics = metric_map(result)

    assert result.capability_id == "weather"
    assert metrics["success_rate"].status == MetricStatus.NOT_APPLICABLE
    assert metrics["success_rate"].details["not_applicable_code"] == "missing_success_outcome"
    assert metrics["latency"].status == MetricStatus.NOT_APPLICABLE
    assert metrics["latency"].details["not_applicable_code"] == "missing_latency_observation"

    mismatched = trace.model_copy(
        update={
            "message": (
                {"role": "user", "content": "What is the weather?"},
                assistant_calls(function_call("weather-step", "other")),
            )
        }
    )
    with pytest.raises(BaseError) as exc_info:
        await EvaluationSuite.default().evaluate(fingerprint(), [mismatched])
    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_success_rate_does_not_infer_success_from_tool_messages() -> None:
    trace = case(
        success=None,
        message=(
            {"role": "user", "content": "What is the weather?"},
            assistant_calls(function_call("weather-step", "weather-api")),
            {"role": "tool", "tool_call_id": "weather-step", "content": "success"},
        ),
    )

    result = await EvaluationSuite.default().evaluate(
        fingerprint(),
        [trace],
        metric_ids=("success_rate",),
    )

    assert metric_map(result)["success_rate"].status == MetricStatus.NOT_APPLICABLE


@pytest.mark.asyncio
async def test_selection_and_composition_use_projected_message_call_order() -> None:
    trace = case(
        expected_output=None,
        message=(
            {"role": "user", "content": "Format the weather"},
            assistant_calls(function_call("weather-step", "weather-api", '{"city":"Shenzhen"}')),
            {"role": "tool", "tool_call_id": "weather-step", "content": "sunny"},
            assistant_calls(function_call("format-step", "formatter", '{"weather":"sunny"}')),
            {"role": "tool", "tool_call_id": "format-step", "content": "It is sunny."},
        ),
    )

    metrics, llm = await judge(
        [CapabilitySelectionEvaluator(), CompositionEffectivenessEvaluator()],
        '{"score": 1, "reason": "calls are well composed"}',
        trace,
        ("capability_selection", "composition_effectiveness"),
    )

    assert all(metric.status == MetricStatus.PASS for metric in metrics)
    assert len(llm.messages) == 2
    prompt = llm.messages[1][0]["content"]
    assert prompt.index("weather-step") < prompt.index("format-step")
    assert "Shenzhen" in prompt
    assert "It is sunny." in prompt


@pytest.mark.asyncio
async def test_indirect_child_output_metrics_ignore_parent_output() -> None:
    parent_trace = EvaluationCase(
        case_id="parent-output-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="Return weather",
        expected_output="parent-expected-output",
        output="parent-only-output",
        message=(
            {"role": "user", "content": "Return weather"},
            assistant_calls(
                function_call("weather-step", "weather", '{"city":"Shenzhen"}'),
                function_call("private-step", "private_tool", '{"secret":true}'),
            ),
        ),
    )

    missing = await EvaluationSuite.default().evaluate(
        fingerprint(),
        (parent_trace,),
        metric_ids=("accuracy", "completeness"),
    )
    missing_metrics = metric_map(missing)
    assert missing_metrics["accuracy"].details["not_applicable_code"] == "missing_output"
    assert missing_metrics["completeness"].status == MetricStatus.FAIL
    assert missing_metrics["completeness"].failures[0].code == "missing_output"

    llm = FakeLLM('{"score": 1, "reason": "child evidence is sufficient"}')
    child_trace = parent_trace.model_copy(
        update={
            "message": (
                *parent_trace.message,
                {"role": "tool", "tool_call_id": "private-step", "content": "private-tool-output"},
                {"role": "tool", "tool_call_id": "weather-step", "content": "child-only-output"},
                {"role": "assistant", "content": "parent-final-content"},
            )
        },
    )
    evaluated = await EvaluationSuite.default(llm=llm, enable_llm=True).evaluate(
        fingerprint(),
        (child_trace,),
        metric_ids=("accuracy", "completeness"),
    )

    assert all(metric.status == MetricStatus.PASS for metric in evaluated.metrics)
    prompts = "\n".join(str(messages) for messages in llm.messages)
    assert "child-only-output" in prompts
    assert "parent-only-output" not in prompts
    assert "parent-expected-output" not in prompts
    assert "parent-final-content" not in prompts
    assert "private-tool-output" not in prompts
    assert "private_tool" not in prompts
    assert "Return weather" in prompts


@pytest.mark.asyncio
async def test_indirect_child_prompt_keeps_user_message_when_query_is_empty() -> None:
    trace = EvaluationCase(
        case_id="parent-empty-query-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="",
        output="parent-only-output",
        message=(
            {"role": "user", "content": "Check the child weather result"},
            assistant_calls(function_call("weather-step", "weather", '{"city":"Shenzhen"}')),
            {"role": "tool", "tool_call_id": "weather-step", "content": "child-only-output"},
            {"role": "user", "content": "unrelated next-round request"},
            {"role": "assistant", "content": "parent-final-assistant"},
        ),
    )

    metrics, llm = await judge(
        [AccuracyEvaluator()],
        '{"score": 1, "reason": "child evidence is sufficient"}',
        trace,
        ("accuracy",),
    )

    assert metrics[0].status == MetricStatus.PASS
    assert len(llm.messages) == 1
    prompt = llm.messages[0][0]["content"]
    assert "Check the child weather result" in prompt
    assert "child-only-output" in prompt
    assert "parent-final-assistant" not in prompt
    assert "parent-only-output" not in prompt
    assert "unrelated next-round request" not in prompt


@pytest.mark.asyncio
async def test_direct_suite_revalidates_model_copy_message() -> None:
    invalid_case = case().model_copy(update={"message": ({"role": "unknown", "content": "x"},)})

    with pytest.raises(BaseError) as exc_info:
        await EvaluationSuite.default().evaluate(fingerprint(), (invalid_case,))

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


def test_aggregate_revalidates_model_copy_metric_results() -> None:
    valid = MetricResult(
        metric_id="latency",
        capability_id="weather",
        capability_type="skill",
        status=MetricStatus.OBSERVED,
    )
    invalid = valid.model_copy(update={"score": 0.5})

    with pytest.raises(BaseError) as exc_info:
        EvaluationSuite.default().aggregate((invalid,))

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_async_llm_metrics_are_opt_in_and_receive_redacted_payload() -> None:
    llm = FakeLLM('{"score": 1, "reason": "The evidence supports the result."}')
    suite = EvaluationSuite.default(llm=llm, enable_llm=True)
    trace = case(
        case_id="private-session-like-case-id",
        query="Authorization: Bearer super-secret-token",
        expected_output="sunny",
        output="cloudy",
        message=(
            {"role": "user", "content": "Authorization: Bearer super-secret-token"},
            assistant_calls(function_call("first", "weather-api", '{"city":"Shenzhen"}')),
            {"role": "tool", "tool_call_id": "first", "content": "cloudy"},
            assistant_calls(function_call("second", "formatter", '{"value":"cloudy"}')),
            {"role": "tool", "tool_call_id": "second", "content": "cloudy"},
            {"role": "assistant", "content": "cloudy"},
        ),
    )

    result = await suite.evaluate(
        fingerprint(metadata={"api_key": "do-not-expose"}),
        [trace],
    )
    metrics = metric_map(result)

    assert metrics["description_quality"].status == "pass"
    assert metrics["classification_consistency"].status == "pass"
    assert metrics["accuracy"].status == "pass"
    assert metrics["completeness"].status == "pass"
    assert metrics["capability_selection"].status == "pass"
    assert metrics["composition_effectiveness"].status == "pass"
    assert len(llm.messages) == 6
    prompts = "\n".join(str(messages) for messages in llm.messages)
    assert "do-not-expose" not in prompts
    assert "private-session-like-case-id" not in prompts
    assert "super-secret-token" not in prompts
    assert "<redacted>" in prompts
    serialized = result.model_dump_json()
    assert "private-session-like-case-id" not in serialized
    assert "case:sha256:" in serialized


@pytest.mark.asyncio
async def test_evaluation_text_limit_is_applied_to_llm_reasons() -> None:
    llm = FakeLLM('{"score": 1, "reason": "abcdefghijklmnopqrstuvwxyz"}')

    result = await EvaluationSuite.default(
        llm=llm,
        enable_llm=True,
        evidence_text_limit=8,
    ).evaluate(fingerprint())

    assert metric_map(result)["description_quality"].reason == "abcdefgh..."


def test_evaluation_text_limit_must_be_a_positive_exact_integer() -> None:
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(BaseError) as exc_info:
            EvaluationSuite.default(evidence_text_limit=cast(Any, invalid))
        assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR


@pytest.mark.asyncio
async def test_exact_match_does_not_discard_significant_output_whitespace() -> None:
    result = await EvaluationSuite.default().evaluate(
        fingerprint(),
        [case(expected_output="sunny ", output="sunny")],
    )

    accuracy = metric_map(result)["accuracy"]
    assert accuracy.status == "not_applicable"
    assert accuracy.details.get("evaluation_method") != "exact_match"


@pytest.mark.asyncio
async def test_llm_failure_is_error_and_does_not_leak_exception_text() -> None:
    llm = FakeLLM(RuntimeError("token=super-secret-value"))
    result = await EvaluationSuite.default(llm=llm, enable_llm=True).evaluate(fingerprint())

    metric = metric_map(result)["description_quality"]
    serialized = metric.model_dump_json()
    assert metric.status == "error"
    assert metric.score is None
    assert metric.failures[0].severity == "error"
    assert metric.evidence
    assert metric.suggestions
    assert "super-secret-value" not in serialized
    assert "RuntimeError" in metric.reason


@pytest.mark.asyncio
async def test_llm_response_prefers_assistant_message_parser_content() -> None:
    class AssistantMessage:
        def __init__(self) -> None:
            self.parser_content = {"score": 1, "reason": "Parsed by the model output parser."}
            self.content = "not valid JSON"

    result = await EvaluationSuite.default(
        llm=FakeLLM(AssistantMessage()),
        enable_llm=True,
    ).evaluate(fingerprint())

    assert metric_map(result)["description_quality"].status == "pass"


@pytest.mark.asyncio
async def test_llm_response_accepts_assistant_message_content_parts() -> None:
    class AssistantMessage:
        def __init__(self) -> None:
            self.parser_content = None
            self.content = ['{"score": 1, ', '"reason": "Joined content parts."}']

    result = await EvaluationSuite.default(
        llm=FakeLLM(AssistantMessage()),
        enable_llm=True,
    ).evaluate(fingerprint())

    assert metric_map(result)["description_quality"].status == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ['```json\n{"score": 0, "reason": "存在事实性错误。"}\n```'],
        '  ```JSON\n{"score": 0, "reason": "存在事实性错误。"}\n```  ',
    ],
)
async def test_llm_response_accepts_complete_json_markdown_fence(response: Any) -> None:
    metrics, llm = await judge(
        [AccuracyEvaluator()], response, case(expected_output=None, output="cloudy"), ("accuracy",)
    )

    assert metrics[0].status == MetricStatus.FAIL
    assert metrics[0].score == 0.0
    assert metrics[0].reason == "存在事实性错误。"
    assert len(llm.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '```json\n{"score": 0, "reason": "broken"\n```',
        '说明文字\n```json\n{"score": 0, "reason": "valid JSON but surrounded"}\n```',
        '```\n{"score": 0, "reason": "untagged fence"}\n```',
        ('```json\n{"score": 0, "reason": "first"}\n```\n```json\n{"score": 1, "reason": "second"}\n```'),
    ],
)
async def test_llm_response_rejects_other_markdown_or_malformed_json(response: str) -> None:
    metrics, llm = await judge(
        [AccuracyEvaluator()], response, case(expected_output=None, output="cloudy"), ("accuracy",)
    )

    assert metrics[0].status == MetricStatus.ERROR
    assert metrics[0].score is None
    assert len(llm.messages) == 1


@pytest.mark.asyncio
async def test_registry_accepts_sync_and_async_structural_evaluators() -> None:
    class SyncEvaluator:
        metric_id = "custom_sync"
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        def evaluate(self, context: EvaluationContext) -> MetricResult:
            return MetricResult(
                metric_id=self.metric_id,
                capability_id=context.capability_id,
                capability_type=context.capability_type,
                score=1.0,
                status=MetricStatus.PASS,
            )

    class AsyncEvaluator:
        metric_id = "custom_async"
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        async def evaluate(self, context: EvaluationContext) -> MetricResult:
            return MetricResult(
                metric_id=self.metric_id,
                capability_id=context.capability_id,
                capability_type=context.capability_type,
                score=1.0,
                status=MetricStatus.PASS,
            )

    suite = EvaluationSuite([SyncEvaluator()])
    suite.register(AsyncEvaluator())
    suite.register(SyncEvaluator(), replace=True)

    result = await suite.evaluate(fingerprint())
    assert [metric.metric_id for metric in result.metrics] == ["custom_sync", "custom_async"]
    assert all(metric.status == "pass" for metric in result.metrics)
    assert result.score is None


def test_registry_validates_and_registers_the_same_dynamic_metric_id() -> None:
    class DynamicMetricEvaluator:
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        def __init__(self) -> None:
            self.metric_id_reads = 0

        @property
        def metric_id(self) -> str:
            self.metric_id_reads += 1
            return "dynamic_metric"

        def evaluate(self, context: EvaluationContext) -> MetricResult:
            return MetricResult(
                metric_id="dynamic_metric",
                capability_id=context.capability_id,
                capability_type=context.capability_type,
                score=1.0,
                status=MetricStatus.PASS,
            )

    evaluator = DynamicMetricEvaluator()
    EvaluationSuite([evaluator])

    assert evaluator.metric_id_reads == 1


@pytest.mark.asyncio
async def test_custom_evaluator_results_are_revalidated_and_bounded() -> None:
    class CopyBypassEvaluator:
        metric_id = "copy_bypass"
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        def evaluate(self, context: EvaluationContext) -> MetricResult:
            valid = MetricResult(
                metric_id=self.metric_id,
                capability_id=context.capability_id,
                capability_type=context.capability_type,
                status=MetricStatus.OBSERVED,
            )
            return valid.model_copy(
                update={"score": 0.5, "details": {"api_key": "evaluator-copy-secret"}},
            )

    class VerboseEvaluator:
        metric_id = "verbose"
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        def evaluate(self, context: EvaluationContext) -> MetricResult:
            return MetricResult(
                metric_id=self.metric_id,
                capability_id=context.capability_id,
                capability_type=context.capability_type,
                status=MetricStatus.OBSERVED,
                reason="abcdefghijklmnopqrstuvwxyz",
                details={"note": "abcdefghijklmnopqrstuvwxyz"},
                evidence=(
                    EvidenceRef(
                        evidence_type="custom",
                        reference="custom:abcdefghijklmnopqrstuvwxyz",
                        description="abcdefghijklmnopqrstuvwxyz",
                    ),
                ),
            )

    unsafe = await EvaluationSuite([CopyBypassEvaluator()]).evaluate_static(fingerprint())
    assert unsafe[0].status == MetricStatus.ERROR
    assert "evaluator-copy-secret" not in unsafe[0].model_dump_json()

    bounded = await EvaluationSuite([VerboseEvaluator()], evidence_text_limit=8).evaluate_static(fingerprint())
    assert bounded[0].reason == "abcdefgh..."
    assert bounded[0].details["note"] == "abcdefgh..."
    assert bounded[0].evidence[0].description == "abcdefgh..."


def test_multi_result_aggregation_preserves_na_codes_and_hashed_case_references() -> None:
    results = tuple(
        MetricResult(
            metric_id="accuracy",
            capability_id="weather",
            capability_type="skill",
            status=MetricStatus.NOT_APPLICABLE,
            details={
                "not_applicable_code": "missing_output",
                "case_reference": f"case:sha256:{suffix}",
            },
        )
        for suffix in ("a", "b")
    )

    aggregated = EvaluationSuite.default().aggregate(results)
    metric = aggregated.metrics[0]

    assert metric.details["not_applicable_codes"] == ["missing_output"]
    assert metric.details["case_references"] == ["case:sha256:a", "case:sha256:b"]


@pytest.mark.asyncio
async def test_unexpected_custom_evaluator_failure_is_sanitized_error() -> None:
    class BrokenEvaluator:
        metric_id = "broken"
        scope: Literal["static", "trace"] = "static"
        requires_llm = False

        def evaluate(self, context: EvaluationContext) -> MetricResult:
            raise RuntimeError("password=must-not-leak")

    result = await EvaluationSuite([BrokenEvaluator()]).evaluate(fingerprint())
    metric = result.metrics[0]

    assert metric.status == "error"
    assert metric.failures[0].code == "evaluator_failed"
    assert metric.evidence
    assert metric.suggestions
    assert "must-not-leak" not in metric.model_dump_json()
