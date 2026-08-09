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


@pytest.mark.asyncio
async def test_accuracy_prompt_uses_event_time_and_migrated_rubric() -> None:
    llm = FakeLLM('{"score": 1, "reason": "工具结果支持该回答。"}')
    trace = case(
        expected_output=None,
        output="The event has happened.",
        message=(
            {"role": "user", "content": "Did the event happen?"},
            {"role": "assistant", "content": "The event has happened."},
        ),
        event_time=datetime(2026, 8, 3, 8, 30, tzinfo=UTC),
    )

    await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("accuracy",))

    prompt = llm.messages[0][0]["content"]
    payload = json.loads(prompt.split("Evaluation data:\n", maxsplit=1)[1])
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
async def test_accuracy_accepts_explicit_null_as_llm_not_applicable() -> None:
    llm = FakeLLM('{"score": null, "reason": "用户 query 与当前 Skill 能力范围明确无关。"}')

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        case(expected_output=None, output="面向用户的回答"),
        metric_ids=("accuracy",),
    )

    metric = metrics[0]
    assert metric.status == MetricStatus.NOT_APPLICABLE
    assert metric.score is None
    assert metric.reason == "用户 query 与当前 Skill 能力范围明确无关。"
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
    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=FakeLLM(response),
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        case(expected_output=None, output="面向用户的回答"),
        metric_ids=("accuracy",),
    )

    assert metrics[0].status == MetricStatus.ERROR
    assert metrics[0].score is None


@pytest.mark.asyncio
async def test_non_accuracy_llm_evaluator_rejects_null_and_keeps_binary_prompt() -> None:
    llm = FakeLLM('{"score": null, "reason": "不能用于完整性评分。"}')

    metrics = await EvaluationSuite(
        [CompletenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        case(expected_output=None, output="面向用户的回答"),
        metric_ids=("completeness",),
    )

    assert metrics[0].status == MetricStatus.ERROR
    prompt = llm.messages[0][0]["content"]
    assert "Return only JSON with score 0 or 1 and a concise reason." in prompt
    assert "0, 1, or null" not in prompt


@pytest.mark.asyncio
async def test_accuracy_can_let_model_mark_tool_call_only_trace_not_applicable() -> None:
    llm = FakeLLM('{"score": null, "reason": "只有工具调用参数，没有实质回答。"}')
    trace = case(
        expected_output=None,
        output=None,
        message=(
            {"role": "user", "content": "查一下附近的诊所"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-search",
                        "type": "function",
                        "function": {"name": "poi_search", "arguments": '{"keyword":"诊所"}'},
                    }
                ],
            },
        ),
    )

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("accuracy",))

    assert metrics[0].status == MetricStatus.NOT_APPLICABLE
    assert metrics[0].details["not_applicable_code"] == "llm_not_applicable"
    assert len(llm.messages) == 1


@pytest.mark.asyncio
async def test_accuracy_prompt_uses_current_utc_time_when_event_time_is_missing() -> None:
    llm = FakeLLM('{"score": 1, "reason": "回答没有明确错误。"}')
    trace = case(expected_output=None, output="Current answer.", event_time=None)
    before = datetime.now(UTC)

    await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("accuracy",))

    after = datetime.now(UTC)
    prompt = llm.messages[0][0]["content"]
    payload = json.loads(prompt.split("Evaluation data:\n", maxsplit=1)[1])
    reference_time = datetime.fromisoformat(payload["reference_time"])
    assert payload["reference_time_source"] == "evaluation_time"
    assert reference_time.tzinfo is not None
    assert before <= reference_time.astimezone(UTC) <= after


@pytest.mark.asyncio
async def test_reference_time_is_only_added_to_accuracy_prompt() -> None:
    llm = FakeLLM('{"score": 1, "reason": "ok"}')
    trace = case(expected_output=None, output="cloudy")

    await EvaluationSuite(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        trace,
        metric_ids=("accuracy", "completeness"),
    )

    accuracy_prompt = llm.messages[0][0]["content"]
    completeness_prompt = llm.messages[1][0]["content"]
    assert '"reference_time"' in accuracy_prompt
    assert '"reference_time_source"' in accuracy_prompt
    assert '"reference_time"' not in completeness_prompt
    assert '"reference_time_source"' not in completeness_prompt


@pytest.mark.asyncio
async def test_accuracy_prompt_omits_empty_case_fields_without_changing_public_case() -> None:
    llm = FakeLLM('{"score": 1, "reason": "ok"}')
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

    await EvaluationSuite(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        trace,
        metric_ids=("accuracy", "completeness"),
    )

    accuracy_payload = json.loads(llm.messages[0][0]["content"].split("Evaluation data:\n", maxsplit=1)[1])
    completeness_payload = json.loads(llm.messages[1][0]["content"].split("Evaluation data:\n", maxsplit=1)[1])
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
@pytest.mark.parametrize("output", [None, "NO_REPLY"])
async def test_accuracy_uses_full_message_when_output_is_missing_or_no_reply(output: Any) -> None:
    llm = FakeLLM('{"score": 1, "reason": "the message contains sufficient evidence"}')
    trace = case(
        expected_output=None,
        output=output,
        message=(
            {"role": "user", "content": "What is the weather?"},
            {"role": "assistant", "content": "The weather is sunny."},
        ),
    )

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("accuracy",))

    assert metrics[0].status == MetricStatus.PASS
    assert len(llm.messages) == 1
    prompt = llm.messages[0][0]["content"]
    assert "What is the weather?" in prompt
    assert "The weather is sunny." in prompt


@pytest.mark.asyncio
async def test_completeness_uses_message_when_output_is_missing() -> None:
    llm = FakeLLM('{"score": 1, "reason": "the message completes the task"}')
    trace = case(expected_output=None, output=None)

    metrics = await EvaluationSuite(
        [CompletenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("completeness",))

    assert metrics[0].status == MetricStatus.PASS
    assert "sunny" in llm.messages[0][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "output"),
    [
        (({"role": "assistant", "content": "assistant-only evidence"},), None),
        ((), "standalone output evidence"),
    ],
)
async def test_direct_output_metrics_accept_query_with_assistant_trace_or_output(
    message: tuple[dict[str, Any], ...],
    output: Any,
) -> None:
    llm = FakeLLM('{"score": 1, "reason": "the supplied evidence is sufficient"}')
    trace = case(expected_output=None, message=message, output=output)

    metrics = await EvaluationSuite(
        [AccuracyEvaluator(), CompletenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        trace,
        metric_ids=("accuracy", "completeness"),
    )

    assert all(metric.status == MetricStatus.PASS for metric in metrics)
    assert len(llm.messages) == 2


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
async def test_window_aggregates_success_and_scenario_latency() -> None:
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
            latency=Latency(ttft=50, e2e=60_000),
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
    assert metrics["latency"].status == "pass"
    assert metrics["latency"].score == pytest.approx(0.9)
    latency_details = dict(metrics["latency"].details)
    case_references = latency_details.pop("case_references")
    assert isinstance(case_references, list)
    assert len(case_references) == 2
    assert all(isinstance(reference, str) and reference.startswith("case:sha256:") for reference in case_references)
    assert latency_details == {
        "window_result_count": 2,
        "pass_count": 2,
        "fail_count": 0,
        "not_applicable_count": 0,
        "error_count": 0,
        "observed_count": 0,
        "avg_ms": 45_000.0,
        "p50_ms": 45_000.0,
        "p95_ms": 58_500.0,
        "max_ms": 60_000.0,
        "count": 2,
        "scenarios": ["short_task"],
        "levels": ["excellent", "good"],
    }


@pytest.mark.parametrize(
    ("scenario", "latency", "expected_level", "expected_score"),
    [
        ("realtime_interaction", Latency(ttft=5_000, e2e=999_999), "excellent", 1.0),
        ("realtime_interaction", Latency(ttft=10_000, e2e=1), "good", 0.8),
        ("realtime_interaction", Latency(ttft=15_000, e2e=1), "pass", 0.6),
        ("realtime_interaction", Latency(ttft=15_001, e2e=1), "fail", 0.0),
        ("short_task", Latency(ttft=999_999, e2e=30_000), "excellent", 1.0),
        ("short_task", Latency(ttft=1, e2e=60_000), "good", 0.8),
        ("short_task", Latency(ttft=1, e2e=90_000), "pass", 0.6),
        ("short_task", Latency(ttft=1, e2e=90_001), "fail", 0.0),
        ("long", Latency(ttft=999_999, e2e=999_999), "excellent", 1.0),
    ],
)
def test_latency_scenario_thresholds(
    scenario: Any,
    latency: Latency,
    expected_level: str,
    expected_score: float,
) -> None:
    context = EvaluationContext(fingerprint=fingerprint(), case=case(latency=latency))
    metric = LatencyEvaluator(scenario=scenario).evaluate(context)

    assert metric.score == expected_score
    assert metric.details["level"] == expected_level
    assert metric.details["scenario"] == scenario
    assert metric.status == (MetricStatus.FAIL if expected_level == "fail" else MetricStatus.PASS)


def test_latency_uses_required_field_and_missing_value_is_not_applicable() -> None:
    realtime = LatencyEvaluator("realtime_interaction").evaluate(
        EvaluationContext(fingerprint=fingerprint(), case=case(latency=Latency(e2e=1)))
    )
    short = LatencyEvaluator("short_task").evaluate(
        EvaluationContext(fingerprint=fingerprint(), case=case(latency=Latency(ttft=1)))
    )

    assert realtime.status == MetricStatus.NOT_APPLICABLE
    assert short.status == MetricStatus.NOT_APPLICABLE


def test_latency_scenario_none_uses_fingerprint_static_configuration() -> None:
    configured = fingerprint(static_data={"evaluation": {"latency_scenario": "realtime_interaction"}})
    metric = LatencyEvaluator(scenario=None).evaluate(
        EvaluationContext(fingerprint=configured, case=case(latency=Latency(ttft=6_000, e2e=1)))
    )

    assert metric.score == 0.8
    assert metric.details["scenario"] == "realtime_interaction"


@pytest.mark.parametrize(
    ("scenario", "thresholds"),
    [
        ("unknown", None),
        (None, (1, 2, 3)),
        ("short_task", (1, 2)),
        ("short_task", (-1, 2, 3)),
        ("short_task", (1, float("inf"), 3)),
        ("short_task", (1, 1, 3)),
    ],
)
def test_latency_rejects_invalid_configuration(scenario: Any, thresholds: Any) -> None:
    with pytest.raises(ValueError):
        LatencyEvaluator(scenario=scenario, thresholds_ms=thresholds)


@pytest.mark.asyncio
async def test_trace_can_reference_evaluated_capability_through_a_call() -> None:
    trace = EvaluationCase(
        case_id="orchestrator-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="What is the weather?",
        message=(
            {"role": "user", "content": "What is the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Shenzhen"}'},
                    }
                ],
            },
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


@pytest.mark.asyncio
async def test_success_rate_does_not_infer_success_from_tool_messages() -> None:
    trace = case(
        success=None,
        message=(
            {"role": "user", "content": "What is the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "weather-api", "arguments": "{}"},
                    }
                ],
            },
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
    llm = FakeLLM('{"score": 1, "reason": "calls are well composed"}')
    trace = case(
        expected_output=None,
        message=(
            {"role": "user", "content": "Format the weather"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "weather-api", "arguments": '{"city":"Shenzhen"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "weather-step", "content": "sunny"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "format-step",
                        "type": "function",
                        "function": {"name": "formatter", "arguments": '{"weather":"sunny"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "format-step", "content": "It is sunny."},
        ),
    )

    metrics = await EvaluationSuite(
        [CapabilitySelectionEvaluator(), CompositionEffectivenessEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        trace,
        metric_ids=("capability_selection", "composition_effectiveness"),
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
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Shenzhen"}'},
                    },
                    {
                        "id": "private-step",
                        "type": "function",
                        "function": {"name": "private_tool", "arguments": '{"secret":true}'},
                    },
                ],
            },
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
    llm = FakeLLM('{"score": 1, "reason": "child evidence is sufficient"}')
    trace = EvaluationCase(
        case_id="parent-empty-query-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="",
        output="parent-only-output",
        message=(
            {"role": "user", "content": "Check the child weather result"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Shenzhen"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "weather-step", "content": "child-only-output"},
            {"role": "user", "content": "unrelated next-round request"},
            {"role": "assistant", "content": "parent-final-assistant"},
        ),
    )

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(fingerprint(), trace, metric_ids=("accuracy",))

    assert metrics[0].status == MetricStatus.PASS
    assert len(llm.messages) == 1
    prompt = llm.messages[0][0]["content"]
    assert "Check the child weather result" in prompt
    assert "child-only-output" in prompt
    assert "parent-final-assistant" not in prompt
    assert "parent-only-output" not in prompt
    assert "unrelated next-round request" not in prompt


@pytest.mark.asyncio
async def test_indirect_identity_uses_tool_function_name() -> None:
    trace = EvaluationCase(
        case_id="parent-case",
        capability_id="orchestrator",
        capability_type="agent",
        message=(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "weather-step",
                        "type": "function",
                        "function": {"name": "Weather", "arguments": "{}"},
                    }
                ],
            },
        ),
    )

    result = await EvaluationSuite.default().evaluate(fingerprint(), [trace])
    assert result.capability_id == "weather"

    mismatched = trace.model_copy(
        update={
            "message": (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "weather-step",
                            "type": "function",
                            "function": {"name": "other", "arguments": "{}"},
                        }
                    ],
                },
            )
        }
    )
    with pytest.raises(BaseError) as exc_info:
        await EvaluationSuite.default().evaluate(fingerprint(), [mismatched])
    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


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
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "first",
                        "type": "function",
                        "function": {"name": "weather-api", "arguments": '{"city":"Shenzhen"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "first", "content": "cloudy"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "second",
                        "type": "function",
                        "function": {"name": "formatter", "arguments": '{"value":"cloudy"}'},
                    }
                ],
            },
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
    llm = FakeLLM(response)

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        case(expected_output=None, output="cloudy"),
        metric_ids=("accuracy",),
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
    llm = FakeLLM(response)

    metrics = await EvaluationSuite(
        [AccuracyEvaluator()],
        llm=llm,
        enable_llm=True,
    ).evaluate_case(
        fingerprint(),
        case(expected_output=None, output="cloudy"),
        metric_ids=("accuracy",),
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
