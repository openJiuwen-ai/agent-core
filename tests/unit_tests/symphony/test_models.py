# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from openjiuwen.symphony import ArtifactSpec, ParameterSpec
from openjiuwen.symphony.models import (
    CapabilityCall,
    CapabilityDescriptor,
    CapabilityFingerprint,
    CapabilityIO,
    EvaluationCase,
    EvidenceRef,
    FailureReason,
    FingerprintArtifact,
    Latency,
    MetricResult,
    MetricStatus,
    QualityResult,
    SourceSnapshot,
)
from openjiuwen.symphony.models._message_trace import (
    matching_message_calls,
    message_has_assistant_or_tool_evidence,
    message_has_user_input,
    project_message_calls,
)
from openjiuwen.symphony.orchestration.graph.build import GraphBuildPipeline
from openjiuwen.symphony.orchestration.graph.models import GraphDiagnostic, SkillRegistry
from openjiuwen.symphony.shared.fingerprint import Fingerprint, capability_content_hash, coerce_fingerprint


def test_capability_contract_uses_canonical_identity_and_extensible_type() -> None:
    descriptor = CapabilityDescriptor.model_validate(
        {
            "capability_id": "researcher",
            "capability_type": "domain-agent",
            "name": "Researcher",
            "semantic_content": "api_key=descriptor-secret",
            "future_provider_field": "ignored",
        }
    )

    payload = descriptor.model_dump(mode="json")

    assert payload["capability_id"] == "researcher"
    assert payload["capability_type"] == "domain-agent"
    assert "id" not in payload
    assert "type" not in payload
    assert "semantic_content" not in payload


def test_descriptor_semantic_content_is_private_but_invalidates_content_hash() -> None:
    first = CapabilityDescriptor(
        capability_id="researcher",
        capability_type="agent",
        name="Researcher",
        semantic_content="first private definition",
    )
    second = first.model_copy(update={"semantic_content": "second private definition"})

    assert capability_content_hash(first) != capability_content_hash(second)


def test_fingerprint_excludes_semantic_source_content_from_artifact_payload() -> None:
    fingerprint = CapabilityFingerprint(
        capability_id="pdf-summary",
        capability_type="skill",
        name="PDF summary",
        description="Summarize a PDF document.",
        content_hash="b" * 64,
        semantic_content="private source definition",
    )

    assert "semantic_content" not in fingerprint.model_dump(mode="json")


def test_canonical_fingerprint_preserves_graph_constructor_compatibility() -> None:
    legacy = Fingerprint(
        type="skill",
        id="pdf-summary",
        name="PDF summary",
        description="Summarize a PDF document.",
        version="1.2.0",
        inputs=[ParameterSpec(name="document", type="markdown")],
        outputs=[ArtifactSpec(name="summary", type="text")],
    )
    canonical = CapabilityFingerprint.model_validate(
        {
            "capability_id": legacy.id,
            "capability_type": legacy.type,
            "name": legacy.name,
            "description": legacy.description,
            "version": legacy.version,
            "inputs": legacy.inputs,
            "outputs": legacy.outputs,
        }
    )

    assert canonical.id == "pdf-summary"
    assert canonical.type == "skill"
    assert canonical.graph_identity_dict() == legacy.graph_identity_dict()
    assert coerce_fingerprint(canonical).graph_identity_dict() == legacy.graph_identity_dict()

    with pytest.raises(ValidationError, match="content_hash"):
        FingerprintArtifact(
            source_snapshot=SourceSnapshot(snapshot_id="graph-only", capability_count=1),
            fingerprints=(canonical,),
        )


def test_rich_fingerprint_graph_bridge_preserves_version_and_required_default() -> None:
    canonical = CapabilityFingerprint(
        capability_id="research",
        capability_type="agent",
        name="Research",
        description="Research a topic.",
        version="2.1.0",
        content_hash="a" * 64,
        inputs=(CapabilityIO(name="query", type="text", required=None),),
    )

    graph_fingerprint = coerce_fingerprint(canonical)

    assert graph_fingerprint.version == "2.1.0"
    assert graph_fingerprint.inputs[0].required is True


def test_graph_compatibility_fields_round_trip_but_stay_out_of_artifact() -> None:
    fingerprint = CapabilityFingerprint(
        capability_id="research",
        capability_type="agent",
        name="Research",
        version="2.3.0",
        static_data={"documentation": "stable", "api_key": "do-not-retain"},
        content_hash="a" * 64,
    )

    model_payload = fingerprint.model_dump(mode="json")
    restored = CapabilityFingerprint.model_validate(model_payload)
    artifact_payload = FingerprintArtifact(
        source_snapshot=SourceSnapshot(snapshot_id="round-trip", capability_count=1),
        fingerprints=(fingerprint,),
    ).model_dump(mode="json")

    assert restored.version == "2.3.0"
    assert restored.static_data == {"documentation": "stable", "api_key": "<redacted>"}
    assert "version" not in artifact_payload["fingerprints"][0]
    assert "static_data" not in artifact_payload["fingerprints"][0]
    assert "id" not in artifact_payload["fingerprints"][0]
    assert "type" not in artifact_payload["fingerprints"][0]


def test_graph_bridge_revalidates_mutable_compatibility_metadata() -> None:
    fingerprint = CapabilityFingerprint(
        capability_id="research",
        capability_type="agent",
        name="Research",
        content_hash="a" * 64,
        static_data={"documentation": "stable"},
    )
    fingerprint.static_data["api_key"] = "late-secret"

    graph_fingerprint = coerce_fingerprint(fingerprint)

    assert fingerprint.to_dict()["static_data"]["api_key"] == "<redacted>"
    assert graph_fingerprint.static_data["api_key"] == "<redacted>"


@pytest.mark.asyncio
async def test_canonical_fingerprint_is_normalized_at_the_graph_registry_boundary() -> None:
    source = CapabilityFingerprint(
        capability_id="render",
        capability_type="skill",
        name="Render",
        content_hash="a" * 64,
        outputs=(CapabilityIO(name="image", type="jpeg"),),
    )
    target = CapabilityFingerprint(
        capability_id="inspect",
        capability_type="agent",
        name="Inspect",
        content_hash="b" * 64,
        inputs=(CapabilityIO(name="image", type="image"),),
    )

    class CapturingResolver:
        thresholds = {"can_feed": 0.7}
        diagnostics: list[GraphDiagnostic] = []

        def __init__(self) -> None:
            self.registry: SkillRegistry | None = None

        async def match(self, registry, candidates):
            self.registry = registry
            return []

        def manifest_metadata(self):
            return {}

    resolver = CapturingResolver()
    result = await GraphBuildPipeline(resolver=resolver).build((source, target))

    assert resolver.registry is not None
    assert all(isinstance(item, Fingerprint) for item in resolver.registry.skills.values())
    assert any(item.source_id == "render" and item.target_id == "inspect" for item in result.candidates)


def test_quality_metrics_preserve_observations_without_composite_score() -> None:
    latency = MetricResult(
        metric_id="latency",
        capability_id="pdf-summary",
        capability_type="skill",
        status=MetricStatus.OBSERVED,
        details={"p95_ms": 1250.0},
    )
    quality = QualityResult(
        capability_id="pdf-summary",
        capability_type="skill",
        metrics=(latency,),
    )

    assert quality.score is None
    assert quality.metrics[0].score is None


def test_observed_metric_rejects_an_arbitrary_normalized_score() -> None:
    with pytest.raises(ValidationError):
        MetricResult(
            metric_id="latency",
            capability_id="pdf-summary",
            capability_type="skill",
            status=MetricStatus.OBSERVED,
            score=0.5,
        )


def test_opaque_trace_and_io_values_preserve_significant_whitespace() -> None:
    trace = EvaluationCase(
        capability_id="exact-output",
        capability_type="skill",
        expected_output="value ",
        output="value",
        inputs={"prompt": "  keep both sides  "},
    )
    call = CapabilityCall(
        capability_id="exact-output",
        capability_type="skill",
        output="result ",
    )
    io = CapabilityIO(name="prompt", type="text", default="  default value  ")

    assert trace.expected_output == "value "
    assert trace.inputs == {"prompt": "  keep both sides  "}
    assert call.output == "result "
    assert io.default == "  default value  "


def test_evaluation_case_serializes_message_output_and_latency_without_legacy_fields() -> None:
    case = EvaluationCase(
        capability_id="weather",
        capability_type="skill",
        query="Weather in Shenzhen?",
        message=(
            {"role": "user", "content": "Weather in Shenzhen?"},
            {"role": "assistant", "content": "Sunny."},
        ),
        output="Sunny.",
        latency=Latency(ttft=125.0, e2e=500.0),
    )

    payload = case.model_dump(mode="json")

    assert payload["message"] == [
        {"role": "user", "content": "Weather in Shenzhen?"},
        {"role": "assistant", "content": "Sunny."},
    ]
    assert payload["output"] == "Sunny."
    assert payload["latency"] == {"ttft": 125.0, "e2e": 500.0}
    assert {"actual_output", "calls", "metadata"}.isdisjoint(payload)


def _function_call(
    call_id: str,
    name: str = "weather",
    arguments: Any = "{}",
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.mark.parametrize(
    "message",
    [
        ({"role": "assistant", "content": "Sunny.", "tool_calls": []},),
        (
            {"role": "user", "content": [{"type": "input_text", "text": "Weather?"}]},
            {"role": "assistant", "content": [{"provider_extension": {"value": "Sunny."}}]},
        ),
    ],
)
def test_evaluation_case_accepts_supported_openai_message_content(
    message: tuple[dict[str, Any], ...],
) -> None:
    case = EvaluationCase(
        capability_id="weather",
        capability_type="skill",
        message=message,
    )

    assert case.message == message


@pytest.mark.parametrize("legacy_field", ["actual_output", "calls", "metadata"])
def test_evaluation_case_explicitly_rejects_legacy_trace_fields(legacy_field: str) -> None:
    with pytest.raises(ValidationError, match="legacy EvaluationCase fields"):
        EvaluationCase.model_validate(
            {
                "capability_id": "weather",
                "capability_type": "skill",
                legacy_field: None,
            }
        )


def test_openai_tool_calls_are_validated_and_projected_with_tool_responses() -> None:
    message = (
        {"role": "user", "content": "Weather in Shenzhen?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _function_call("call-weather", arguments='{"city":"Shenzhen"}'),
                _function_call("call-pending", name="pending"),
            ],
        },
        {"role": "tool", "tool_call_id": "call-weather", "content": '{"temperature":30}'},
        {"role": "assistant", "content": "It is 30 degrees."},
    )
    case = EvaluationCase(
        capability_id="parent-agent",
        capability_type="agent",
        message=message,
    )
    fingerprint = CapabilityFingerprint(
        capability_id="weather-v2",
        capability_type="skill",
        name="Weather",
    )
    id_fingerprint = CapabilityFingerprint(
        capability_id="weather",
        capability_type="skill",
        name="Forecast",
    )

    calls = project_message_calls(case.message)

    assert len(calls) == 2
    assert calls[0].tool_call_id == "call-weather"
    assert calls[0].function_name == "weather"
    assert calls[0].arguments == '{"city":"Shenzhen"}'
    assert calls[0].inputs == {"city": "Shenzhen"}
    assert calls[0].assistant_message_index == 1
    assert calls[0].tool_message_index == 2
    assert calls[0].output == '{"temperature":30}'
    assert calls[1].tool_message_index is None
    assert calls[1].output is None
    assert matching_message_calls(case.message, fingerprint) == (calls[0],)
    assert matching_message_calls(case.message, id_fingerprint) == (calls[0],)
    assert message_has_user_input(case.message) is True
    assert message_has_assistant_or_tool_evidence(case.message) is True


def test_message_evidence_helpers_reject_blank_content() -> None:
    blank_message = (
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "", "tool_calls": []},
    )
    case = EvaluationCase(
        capability_id="weather",
        capability_type="skill",
        message=blank_message,
    )

    assert message_has_user_input(case.message) is False
    assert message_has_assistant_or_tool_evidence(case.message) is False
    assert (
        message_has_assistant_or_tool_evidence(({"role": "tool", "tool_call_id": "call-weather", "content": "\t"},))
        is False
    )


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (({"role": "system", "content": 1},), "content"),
        (({"role": "developer", "content": False},), "content"),
        (({"role": "user", "content": {"type": "input_text", "text": "Weather?"}},), "content"),
        (({"role": "assistant", "content": 1.5},), "content"),
        (({"role": "tool", "content": None},), "content"),
        (({"role": "user", "content": []},), "content"),
        (({"role": "observer", "content": "x"},), "standard OpenAI role"),
        (
            (
                {
                    "role": "assistant",
                    "tool_calls": [
                        _function_call("duplicate", name="first"),
                        _function_call("duplicate", name="second"),
                    ],
                },
            ),
            "duplicate tool call id",
        ),
        (
            ({"role": "tool", "tool_call_id": "unknown", "content": "result"},),
            "preceding assistant tool call",
        ),
        (
            ({"role": "assistant", "tool_calls": [_function_call("missing-name", name="")]},),
            "function.name",
        ),
        (
            (
                {
                    "role": "assistant",
                    "tool_calls": [_function_call("wrong-arguments", arguments={})],
                },
            ),
            "function.arguments",
        ),
    ],
)
def test_evaluation_case_rejects_invalid_openai_messages(
    message: tuple[dict[str, Any], ...],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        EvaluationCase(
            capability_id="weather",
            capability_type="skill",
            message=message,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"ttft": -0.1},
        {"e2e": -1.0},
        {"ttft": float("nan")},
        {"e2e": float("inf")},
    ],
)
def test_latency_rejects_negative_and_non_finite_values(payload: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Latency.model_validate(payload)


def test_sensitive_io_defaults_are_redacted_at_the_public_model_boundary() -> None:
    io = CapabilityIO(name="authorization", type="text", default="Bearer private-value")

    assert io.default is None
    assert io.metadata["default_redacted"] is True
    assert "private-value" not in io.model_dump_json()


@pytest.mark.parametrize(
    "name",
    ["apiKey", "accessKeyId", "AWSAccessKeyId", "aws_access_key_id", "client_secret", "access-token"],
)
def test_sensitive_io_name_conventions_all_redact_defaults(name: str) -> None:
    io = CapabilityIO(name=name, type="text", default="must-not-survive")

    assert io.default is None
    assert io.metadata["default_redacted"] is True
    assert "must-not-survive" not in io.model_dump_json()


@pytest.mark.parametrize(
    "name",
    [
        "authorization_required",
        "cache_key",
        "cookie_count",
        "ordinary_key",
        "password_policy",
        "primary_key",
        "routing_key",
        "token_count",
    ],
)
def test_non_secret_key_and_token_names_preserve_defaults(name: str) -> None:
    io = CapabilityIO(name=name, type="text", default="public-default")

    assert io.default == "public-default"
    assert "default_redacted" not in io.metadata


def test_nested_sensitive_values_are_redacted_inside_non_secret_io_default() -> None:
    io = CapabilityIO(
        name="config",
        type="json",
        default={
            "api_key": "nested-default-secret",
            "safe": "  preserve whitespace  ",
            "items": [{"access_token": "list-secret", "count": 2}],
        },
    )

    assert io.default == {
        "api_key": "<redacted>",
        "safe": "  preserve whitespace  ",
        "items": [{"access_token": "<redacted>", "count": 2}],
    }
    assert "nested-default-secret" not in io.model_dump_json()
    assert "list-secret" not in io.model_dump_json()


def test_public_narrative_and_extension_fields_redact_nested_credentials() -> None:
    evidence = EvidenceRef(
        evidence_type="diagnostic",
        reference="local:test",
        description='Observed "client_secret": "description-secret"',
        metadata={
            "apiKey": "metadata-secret",
            "nested": {"refresh_token": "nested-secret"},
        },
    )
    failure = FailureReason(
        code="unsafe_input",
        message="accessToken=message-secret",
        evidence=(evidence,),
        details={"aws_access_key_id": "detail-secret"},
    )

    serialized = failure.model_dump_json()
    assert "description-secret" not in serialized
    assert "metadata-secret" not in serialized
    assert "nested-secret" not in serialized
    assert "message-secret" not in serialized
    assert "detail-secret" not in serialized
    assert serialized.count("<redacted>") >= 5


def test_recursive_extension_metadata_is_replaced_with_a_safe_marker() -> None:
    metadata: dict[str, Any] = {"ordinary_key": "kept"}
    metadata["self"] = metadata

    descriptor = CapabilityDescriptor(
        capability_id="recursive",
        capability_type="skill",
        name="Recursive",
        metadata=metadata,
    )

    assert descriptor.metadata["ordinary_key"] == "kept"
    assert descriptor.metadata["self"] == "<redacted:recursive>"


def test_deep_provider_extensions_and_defaults_are_bounded_before_validation() -> None:
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(1_500):
        child: dict[str, Any] = {}
        cursor["child"] = child
        cursor = child

    descriptor = CapabilityDescriptor(
        capability_id="deep",
        capability_type="skill",
        name="Deep",
        metadata=deep,
        inputs=(CapabilityIO(name="config", type="json", default=deep),),
    )

    serialized = descriptor.model_dump_json()
    assert "<redacted:depth-limit>" in serialized


def test_capability_call_rejects_mixed_timezone_awareness() -> None:
    with pytest.raises(ValidationError, match="timezone awareness"):
        CapabilityCall(
            capability_id="clock",
            capability_type="skill",
            started_at=datetime(2026, 8, 3),  # noqa: DTZ001 -- verifies mixed-awareness rejection.
            ended_at=datetime(2026, 8, 3, 1, tzinfo=UTC),
        )


def test_artifact_generated_at_is_normalized_to_utc_and_rejects_naive_values() -> None:
    artifact = FingerprintArtifact(
        generated_at=datetime(2026, 8, 3, 16, tzinfo=timezone(timedelta(hours=8))),
        source_snapshot=SourceSnapshot(snapshot_id="snapshot"),
    )

    assert artifact.generated_at == datetime(2026, 8, 3, 8, tzinfo=UTC)
    with pytest.raises(ValidationError):
        FingerprintArtifact(
            generated_at=datetime(2026, 8, 3, 8),  # noqa: DTZ001 -- verifies rejection of naive values.
            source_snapshot=SourceSnapshot(snapshot_id="snapshot"),
        )
