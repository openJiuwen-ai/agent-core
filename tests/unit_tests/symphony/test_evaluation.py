# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit coverage for Symphony's fail-soft evaluation suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.evaluation import EvaluationContext, EvaluationSuite, EvaluationWindow
from openjiuwen.symphony.models import (
    CapabilityCall,
    CapabilityFingerprint,
    EvaluationCase,
    EvidenceRef,
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
        "actual_output": "sunny",
        "success": True,
        "event_time": datetime(2026, 8, 3, tzinfo=UTC),
        "calls": (
            CapabilityCall(
                call_id="weather-call",
                capability_id="weather-api",
                capability_type="tool",
                success=True,
                latency_ms=100,
            ),
        ),
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
async def test_window_aggregates_success_and_raw_latency_without_threshold() -> None:
    cases = [
        case(
            case_id="inside-1",
            success=True,
            calls=(
                CapabilityCall(
                    call_id="call-1",
                    capability_id="weather-api",
                    capability_type="tool",
                    latency_ms=100,
                    success=True,
                ),
            ),
        ),
        case(
            case_id="inside-2",
            success=False,
            event_time=datetime(2026, 8, 3, 1, tzinfo=UTC),
            calls=(
                CapabilityCall(
                    call_id="call-2",
                    capability_id="weather-api",
                    capability_type="tool",
                    latency_ms=300,
                    success=False,
                ),
            ),
        ),
        case(
            case_id="outside",
            event_time=datetime(2026, 8, 4, tzinfo=UTC),
            calls=(
                CapabilityCall(
                    call_id="call-3",
                    capability_id="weather-api",
                    capability_type="tool",
                    latency_ms=9_999,
                    success=True,
                ),
            ),
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
        "avg_ms": 200.0,
        "p50_ms": 200.0,
        "p95_ms": 290.0,
        "max_ms": 300.0,
        "count": 2,
        "observation_bases": ["capability_calls"],
        "target_bases": [],
    }


@pytest.mark.asyncio
async def test_explicit_latency_target_is_the_only_latency_scoring_input() -> None:
    result = await EvaluationSuite.default().evaluate(
        fingerprint(),
        [case(metadata={"latency_target_ms": 90})],
    )

    latency = metric_map(result)["latency"]
    assert latency.status == "fail"
    assert latency.score == 0.0
    assert latency.details["target_ms"] == 90
    assert latency.details["observed_ms"] == 100
    assert latency.details["target_basis"] == "single_call_latency"


@pytest.mark.asyncio
async def test_trace_can_reference_evaluated_capability_through_a_call() -> None:
    trace = EvaluationCase(
        case_id="orchestrator-case",
        capability_id="orchestrator",
        capability_type="agent",
        calls=(
            CapabilityCall(
                call_id="weather-step",
                capability_id="weather",
                capability_type="skill",
                output="sunny",
                success=True,
                latency_ms=125,
            ),
        ),
    )

    result = await EvaluationSuite.default().evaluate(fingerprint(), [trace])
    metrics = metric_map(result)

    assert result.capability_id == "weather"
    assert metrics["success_rate"].status == "pass"
    assert metrics["latency"].status == "observed"
    assert metrics["latency"].details["avg_ms"] == 125


@pytest.mark.asyncio
async def test_indirect_child_output_metrics_ignore_parent_output() -> None:
    parent_trace = EvaluationCase(
        case_id="parent-output-case",
        capability_id="orchestrator",
        capability_type="agent",
        query="Return weather",
        expected_output="parent-expected-output",
        actual_output="parent-only-output",
        calls=(
            CapabilityCall(
                call_id="weather-step",
                capability_id="weather",
                capability_type="skill",
                output=None,
            ),
        ),
    )

    missing = await EvaluationSuite.default().evaluate(
        fingerprint(),
        (parent_trace,),
        metric_ids=("accuracy", "completeness"),
    )
    missing_metrics = metric_map(missing)
    assert missing_metrics["accuracy"].details["not_applicable_code"] == "missing_actual_output"
    assert missing_metrics["completeness"].status == MetricStatus.FAIL
    assert missing_metrics["completeness"].failures[0].code == "missing_actual_output"

    llm = FakeLLM('{"score": 1, "reason": "child evidence is sufficient"}')
    child_trace = parent_trace.model_copy(
        update={"calls": (parent_trace.calls[0].model_copy(update={"output": "child-only-output"}),)},
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


@pytest.mark.asyncio
async def test_child_call_uses_typed_identity_and_its_own_latency() -> None:
    trace = EvaluationCase(
        case_id="parent-case",
        capability_id="orchestrator",
        capability_type="agent",
        metadata={"duration_ms": 1_000, "latency_target_ms": 1_500},
        calls=(
            CapabilityCall(
                call_id="weather-step",
                capability_id="weather",
                capability_type="skill",
                latency_ms=100,
                success=True,
                metadata={"latency_target_ms": 150},
            ),
        ),
    )

    result = await EvaluationSuite.default().evaluate(fingerprint(), [trace])
    latency = metric_map(result)["latency"]

    assert latency.details["observed_ms"] == 100
    assert latency.details["target_ms"] == 150
    assert latency.details["target_basis"] == "single_call_latency"

    mismatched = trace.model_copy(update={"calls": (trace.calls[0].model_copy(update={"capability_type": "agent"}),)})
    with pytest.raises(BaseError) as exc_info:
        await EvaluationSuite.default().evaluate(fingerprint(), [mismatched])
    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_direct_suite_revalidates_model_copy_trace_timing() -> None:
    valid_call = CapabilityCall(
        capability_id="weather",
        capability_type="skill",
        started_at=datetime(2026, 8, 3, tzinfo=UTC),
        ended_at=datetime(2026, 8, 3, 1, tzinfo=UTC),
    )
    invalid_call = valid_call.model_copy(
        update={"started_at": datetime(2026, 8, 3)},  # noqa: DTZ001 -- deliberate bypass attempt.
    )
    invalid_case = case(calls=(valid_call,)).model_copy(update={"calls": (invalid_call,)})

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
        actual_output="cloudy",
        calls=(
            CapabilityCall(
                call_id="first",
                capability_id="weather-api",
                capability_type="tool",
                output="cloudy",
            ),
            CapabilityCall(
                call_id="second",
                capability_id="formatter",
                capability_type="tool",
                output="cloudy",
            ),
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
        [case(expected_output="sunny ", actual_output="sunny")],
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
                "not_applicable_code": "missing_actual_output",
                "case_reference": f"case:sha256:{suffix}",
            },
        )
        for suffix in ("a", "b")
    )

    aggregated = EvaluationSuite.default().aggregate(results)
    metric = aggregated.metrics[0]

    assert metric.details["not_applicable_codes"] == ["missing_actual_output"]
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
