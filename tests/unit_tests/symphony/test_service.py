# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.symphony.models import (
    CapabilityDescriptor,
    CapabilityFingerprint,
    CapabilityIO,
    EvaluationCase,
    MetricStatus,
    QualityResult,
    SourceSnapshot,
)
from openjiuwen.symphony.shared.fingerprint import (
    CapabilityFingerprintExtractor,
    FingerprintService,
    FingerprintSettings,
    build_io_name_vocabulary,
    normalize_io_specs,
)


class FakeProvider:
    def __init__(self, snapshot_id: str, descriptors) -> None:
        self.snapshot_id = snapshot_id
        self.descriptors = descriptors

    async def source_snapshot(self) -> SourceSnapshot:
        return SourceSnapshot(snapshot_id=self.snapshot_id, capability_count=len(self.descriptors))

    async def capabilities(self):
        return self.descriptors


class ChangingSnapshotProvider(FakeProvider):
    def __init__(self, descriptors) -> None:
        super().__init__("snapshot", descriptors)
        self.snapshot_reads = 0

    async def source_snapshot(self) -> SourceSnapshot:
        self.snapshot_reads += 1
        return SourceSnapshot(
            snapshot_id=f"snapshot-{self.snapshot_reads}",
            capability_count=len(self.descriptors),
        )


class AtomicProvider(FakeProvider):
    def __init__(self, snapshot_id: str, descriptors) -> None:
        super().__init__(snapshot_id, descriptors)
        self.atomic_reads = 0

    async def inventory_snapshot(self):
        self.atomic_reads += 1
        return (
            SourceSnapshot(snapshot_id=self.snapshot_id, capability_count=len(self.descriptors)),
            tuple(self.descriptors),
        )

    async def source_snapshot(self) -> SourceSnapshot:
        raise AssertionError("separate snapshot reads must not be used")

    async def capabilities(self):
        raise AssertionError("separate capability reads must not be used")


class MalformedAtomicProvider(FakeProvider):
    async def inventory_snapshot(self):
        return (SourceSnapshot(snapshot_id=self.snapshot_id),)


class TimestampChangingProvider(FakeProvider):
    def __init__(self, descriptors) -> None:
        super().__init__("stable-snapshot", descriptors)
        self.snapshot_reads = 0

    async def source_snapshot(self) -> SourceSnapshot:
        captured_at = datetime(2026, 8, 3, tzinfo=UTC) + timedelta(seconds=self.snapshot_reads)
        self.snapshot_reads += 1
        return SourceSnapshot(
            snapshot_id=self.snapshot_id,
            capability_count=len(self.descriptors),
            captured_at=captured_at,
        )


class InvalidSnapshotProvider(FakeProvider):
    async def source_snapshot(self) -> SourceSnapshot:
        valid = SourceSnapshot(snapshot_id=self.snapshot_id, capability_count=len(self.descriptors))
        return valid.model_copy(update={"capability_count": -1})


class NoopEvaluationSuite:
    cache_signature = "noop-evaluation-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, fingerprint: CapabilityFingerprint, cases=()) -> QualityResult:
        self.calls += 1
        return QualityResult(
            capability_id=fingerprint.capability_id,
            capability_type=fingerprint.capability_type,
            sample_count=len(cases),
        )


class CountingLLM:
    def __init__(self, label: str = "") -> None:
        self.calls = 0
        self.label = label
        self.cache_signature = f"counting-llm-v1:{label or 'default'}"

    async def invoke(self, messages, **_kwargs) -> str:
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        return json.dumps(
            {
                "description": context["description"] or f"Extracted {context['name']}",
                "semantic_profile": {
                    "summary": f"Profile {self.label + ' ' if self.label else ''}for {context['name']}"
                },
                "inputs": [{"name": "query", "type": "text"}],
                "outputs": [{"name": "result", "type": "text"}],
                "classification": "General",
                "tags": ["generated"],
            }
        )


class NamedInputLLM(CountingLLM):
    def __init__(self, input_name: str) -> None:
        super().__init__(input_name)
        self.input_name = input_name

    async def invoke(self, messages, **_kwargs) -> str:
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        return json.dumps(
            {
                "description": context["description"] or f"Extracted {context['name']}",
                "semantic_profile": {"summary": f"Profile for {context['name']}"},
                "inputs": [{"name": self.input_name, "type": "text"}],
                "outputs": [{"name": "result", "type": "text"}],
            }
        )


class DelayedVocabularyLLM(CountingLLM):
    async def invoke(self, messages, **_kwargs) -> str:
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        await asyncio.sleep(0.01 if context["capability_id"] == "summarize" else 0)
        input_name = "query" if context["capability_id"] == "summarize" else "queries"
        return json.dumps(
            {
                "semantic_profile": {"summary": f"Profile for {context['name']}"},
                "inputs": [{"name": input_name, "type": "text"}],
                "outputs": [{"name": "result", "type": "text"}],
            }
        )


class InvalidEntryVocabularyLLM(DelayedVocabularyLLM):
    async def invoke(self, messages, **_kwargs) -> str:
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        await asyncio.sleep(0.01 if context["capability_id"] == "summarize" else 0)
        input_name = "query" if context["capability_id"] == "summarize" else "queries"
        inputs: list[object] = [{"name": input_name, "type": "text"}]
        if context["capability_id"] == "summarize":
            inputs.insert(0, 42)
        return json.dumps(
            {
                "semantic_profile": {"summary": f"Profile for {context['name']}"},
                "inputs": inputs,
                "outputs": [{"name": "result", "type": "text"}],
            }
        )


class BlockingLLM(CountingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, messages, **kwargs) -> str:
        self.started.set()
        await self.release.wait()
        return await super().invoke(messages, **kwargs)


class ControlledCompletionLLM(CountingLLM):
    def __init__(self, capability_ids: tuple[str, ...]) -> None:
        super().__init__()
        self.started = {capability_id: asyncio.Event() for capability_id in capability_ids}
        self.release = {capability_id: asyncio.Event() for capability_id in capability_ids}

    async def invoke(self, messages, **kwargs) -> str:
        context = json.loads(messages[-1]["content"])
        capability_id = context["capability_id"]
        self.started[capability_id].set()
        await self.release[capability_id].wait()
        return await super().invoke(messages, **kwargs)


class PartiallyInvalidLLM(CountingLLM):
    async def invoke(self, messages, **kwargs) -> str:
        context = json.loads(messages[-1]["content"])
        if context["capability_id"] == "summarize":
            self.calls += 1
            return "[]"
        return await super().invoke(messages, **kwargs)


class FrameworkFailingLLM:
    cache_signature = "framework-failure-v1"

    async def invoke(self, messages, **kwargs):
        del messages, kwargs
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg="model unavailable",
        )


class CoreMessageLLM(CountingLLM):
    async def invoke(self, messages, **kwargs):
        content = await super().invoke(messages, **kwargs)

        class AssistantMessage:
            parser_content = None

            def __init__(self, text: str) -> None:
                self.content = text

        return AssistantMessage(content)


class OpaqueLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, messages, **_kwargs) -> str:
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        return json.dumps(
            {
                "semantic_profile": {"summary": f"Opaque profile for {context['name']}"},
            }
        )


class CapturingLLM(CountingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def invoke(self, messages, **kwargs) -> str:
        self.prompts.append(messages[-1]["content"])
        return await super().invoke(messages, **kwargs)


def _descriptors() -> list[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            capability_id="summarize",
            capability_type="skill",
            name="Summarize",
            description="Summarize supplied text into a concise response.",
            content_hash="1" * 64,
        ),
        CapabilityDescriptor(
            capability_id="researcher",
            capability_type="agent",
            name="Researcher",
            description="Research a topic and return supported findings.",
            content_hash="2" * 64,
        ),
    ]


def _three_descriptors() -> list[CapabilityDescriptor]:
    return [
        *_descriptors(),
        CapabilityDescriptor(
            capability_id="writer",
            capability_type="skill",
            name="Writer",
            description="Write a concise response from supplied findings.",
            content_hash="3" * 64,
        ),
    ]


@pytest.mark.asyncio
async def test_build_and_read_skill_and_agent_without_jiuwenswarm(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )

    artifact = await service.build()

    assert [(item.capability_type, item.capability_id) for item in artifact.fingerprints] == [
        ("agent", "researcher"),
        ("skill", "summarize"),
    ]
    assert service.read() == artifact
    assert (tmp_path / "fingerprint.json").is_file()


@pytest.mark.asyncio
async def test_atomic_provider_inventory_snapshot_is_preferred(tmp_path) -> None:
    provider = AtomicProvider("snapshot-atomic", _descriptors())

    artifact = await FingerprintService(
        provider,
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    assert artifact.source_snapshot.snapshot_id == "snapshot-atomic"
    assert provider.atomic_reads == 1


@pytest.mark.asyncio
async def test_non_atomic_provider_change_rejects_inconsistent_inventory(tmp_path) -> None:
    service = FingerprintService(
        ChangingSnapshotProvider(_descriptors()),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID
    assert not (tmp_path / "fingerprint.json").exists()


@pytest.mark.asyncio
async def test_malformed_atomic_snapshot_is_an_inventory_error(tmp_path) -> None:
    service = FingerprintService(
        MalformedAtomicProvider("snapshot-1", _descriptors()),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


@pytest.mark.asyncio
async def test_fallback_provider_allows_observation_timestamp_to_advance(tmp_path) -> None:
    provider = TimestampChangingProvider(_descriptors())

    artifact = await FingerprintService(
        provider,
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    assert artifact.source_snapshot.snapshot_id == "stable-snapshot"
    assert provider.snapshot_reads == 2


@pytest.mark.asyncio
async def test_missing_evaluation_inputs_are_skipped_not_top_level_failures(tmp_path) -> None:
    descriptor = _descriptors()[0].model_copy(
        update={"classification": "general", "tags": ("summary",)},
    )

    artifact = await FingerprintService(FakeProvider("snapshot-1", [descriptor]), tmp_path).build()
    fingerprint = artifact.fingerprints[0]

    assert fingerprint.quality is not None
    skipped = [metric for metric in fingerprint.quality.metrics if metric.status == "not_applicable"]
    assert skipped
    assert all(not metric.failures for metric in skipped)
    assert all(metric.details.get("not_applicable_code") for metric in skipped)
    assert fingerprint.failures == ()


@pytest.mark.asyncio
async def test_extraction_prompt_redacts_common_secret_name_conventions(tmp_path) -> None:
    llm = CapturingLLM()
    descriptor = _descriptors()[0].model_copy(
        update={
            "semantic_content": (
                "---\ndescription: |\n  A block with an indented delimiter.\n  ---\n"
                "inputs:\n  - name: client_secret\n    type: string\n"
                "    default: yaml-secret\n---\n"
                'Example: {"client_secret": "json-secret"}\n'
                "apiKey=camel-secret\naws_access_key_id=aws-secret"
            )
        },
    )

    await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    prompt = "\n".join(llm.prompts)
    assert "yaml-secret" not in prompt
    assert "json-secret" not in prompt
    assert "camel-secret" not in prompt
    assert "aws-secret" not in prompt
    assert "<redacted>" in prompt


@pytest.mark.asyncio
async def test_provider_model_copy_cannot_bypass_nested_io_redaction(tmp_path) -> None:
    llm = CapturingLLM()
    safe_io = CapabilityIO(name="query", type="text")
    unsafe_io = safe_io.model_copy(
        update={
            "name": "apiKey",
            "default": "copy-bypass-secret",
            "metadata": {"client_secret": "nested-copy-secret"},
        }
    )
    nested_default = CapabilityIO(
        name="config",
        type="json",
        default={"api_key": "nested-default-secret", "safe": "kept"},
    )
    descriptor = _descriptors()[0].model_copy(update={"inputs": (unsafe_io, nested_default)})

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    serialized = artifact.model_dump_json()
    prompt = "\n".join(llm.prompts)
    assert "copy-bypass-secret" not in prompt
    assert "nested-copy-secret" not in prompt
    assert "nested-default-secret" not in prompt
    assert "copy-bypass-secret" not in serialized
    assert "nested-copy-secret" not in serialized
    assert "nested-default-secret" not in serialized
    assert artifact.fingerprints[0].inputs[0].default is None
    assert artifact.fingerprints[0].inputs[1].default == {"api_key": "<redacted>", "safe": "kept"}


@pytest.mark.asyncio
async def test_direct_extractor_revalidates_descriptor_before_llm_input() -> None:
    llm = CapturingLLM()
    unsafe_io = CapabilityIO(name="query", type="text").model_copy(
        update={"name": "apiKey", "default": "direct-extractor-secret"},
    )
    descriptor = _descriptors()[0].model_copy(update={"inputs": (unsafe_io,)})

    outcome = await CapabilityFingerprintExtractor(
        FingerprintSettings(enable_llm_extraction=True),
        llm,
    ).extract(descriptor)

    assert "direct-extractor-secret" not in "\n".join(llm.prompts)
    assert outcome.fingerprint.inputs[0].default is None


@pytest.mark.asyncio
async def test_indented_markdown_rule_is_not_mistaken_for_frontmatter(tmp_path) -> None:
    llm = CapturingLLM()
    descriptor = _descriptors()[0].model_copy(
        update={"semantic_content": "  ---\nIndented Markdown rule remains model context."},
    )

    await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    context = json.loads(llm.prompts[0])
    assert context["definition"].startswith("  ---\nIndented Markdown")


@pytest.mark.asyncio
async def test_incremental_cache_reuses_complete_fingerprint(tmp_path) -> None:
    llm = CountingLLM()
    evaluation = NoopEvaluationSuite()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=evaluation,
    )

    first = await service.build()
    progress = []
    second = await service.build(progress_callback=progress.append)

    assert llm.calls == 2
    assert evaluation.calls == 2
    assert second.fingerprints[0].evidence == first.fingerprints[0].evidence
    assert second.fingerprints[0].semantic_profile == first.fingerprints[0].semantic_profile
    assert progress == []
    cache_payload = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    assert cache_payload["entries"]["researcher"]["normalization"]["decisions"]


@pytest.mark.asyncio
async def test_extraction_progress_advances_only_after_concurrent_completion(tmp_path) -> None:
    descriptors = _three_descriptors()
    capability_ids = tuple(item.capability_id for item in descriptors)
    llm = ControlledCompletionLLM(capability_ids)
    progress = []
    progress_reached = {current: asyncio.Event() for current in range(1, 4)}

    async def record_progress(event) -> None:
        progress.append(event)
        current = event["current"]
        if current:
            progress_reached[current].set()

    service = FingerprintService(
        FakeProvider("snapshot-1", descriptors),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(
            enable_llm_extraction=True,
            batch_size=3,
            max_concurrency=3,
        ),
        evaluation_suite=NoopEvaluationSuite(),
    )

    task = asyncio.create_task(service.build(progress_callback=record_progress))
    await asyncio.gather(*(event.wait() for event in llm.started.values()))
    assert [(item["current"], item["total"]) for item in progress] == [(0, 3)]

    completion_order = ("researcher", "writer", "summarize")
    for current, capability_id in enumerate(completion_order, start=1):
        llm.release[capability_id].set()
        await progress_reached[current].wait()

    await task

    assert [(item["current"], item["total"]) for item in progress] == [
        (0, 3),
        (1, 3),
        (2, 3),
        (3, 3),
    ]
    assert [item.get("capability_id") for item in progress[1:]] == list(completion_order)


@pytest.mark.asyncio
async def test_incremental_progress_counts_only_descriptors_requiring_extraction(tmp_path) -> None:
    provider = FakeProvider("snapshot-1", _descriptors())
    service = FingerprintService(
        provider,
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    await service.build()
    provider.descriptors = [
        provider.descriptors[0],
        provider.descriptors[1].model_copy(update={"content_hash": "4" * 64}),
    ]
    progress = []

    await service.build(progress_callback=progress.append)

    assert [(item["current"], item["total"]) for item in progress] == [(0, 1), (1, 1)]


@pytest.mark.asyncio
async def test_progress_callback_failure_does_not_abort_fingerprint_build(tmp_path) -> None:
    attempts = 0

    async def failing_progress(_event) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("progress transport failed")

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build(progress_callback=failing_progress)

    assert artifact.fingerprints
    assert attempts == 2


@pytest.mark.asyncio
async def test_llm_unseen_io_name_is_persisted_and_restored_without_explicit_io(tmp_path) -> None:
    llm = NamedInputLLM("customer_prompt")
    provider = FakeProvider("snapshot-1", _descriptors()[:1])
    settings = FingerprintSettings(enable_llm_extraction=True)

    first = await FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    cache_payload = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    second = await FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    assert first.fingerprints[0].inputs[0].name == "customer_prompt"
    assert second.fingerprints[0].inputs[0].name == "customer_prompt"
    assert llm.calls == 1
    assert "customer_prompt" in {term["name"] for term in cache_payload["io_name_vocabulary"]["terms"]}


@pytest.mark.asyncio
async def test_llm_unseen_plural_alias_is_persisted_and_used(tmp_path) -> None:
    llm = NamedInputLLM("queries")
    provider = FakeProvider(
        "snapshot-1",
        [
            _descriptors()[0].model_copy(update={"inputs": (CapabilityIO(name="query", type="text"),)}),
            _descriptors()[1],
        ],
    )
    settings = FingerprintSettings(enable_llm_extraction=True)
    service = FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    )

    first = await service.build()
    cache_payload = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    second = await FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    terms = {term["name"]: term for term in cache_payload["io_name_vocabulary"]["terms"]}
    first_generated = next(item for item in first.fingerprints if item.capability_id == "researcher")
    second_generated = next(item for item in second.fingerprints if item.capability_id == "researcher")

    assert first_generated.inputs[0].name == "query"
    assert second_generated.inputs[0].name == "query"
    assert terms["query"]["aliases"] == ["queries"]
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_dynamic_vocabulary_is_deterministic_across_concurrency(tmp_path) -> None:
    serial_root = tmp_path / "serial"
    concurrent_root = tmp_path / "concurrent"
    provider = FakeProvider("snapshot-1", _descriptors())
    serial = await FingerprintService(
        provider,
        serial_root,
        llm=DelayedVocabularyLLM(),
        settings=FingerprintSettings(enable_llm_extraction=True, max_concurrency=1),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    concurrent = await FingerprintService(
        provider,
        concurrent_root,
        llm=DelayedVocabularyLLM(),
        settings=FingerprintSettings(enable_llm_extraction=True, max_concurrency=4),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    serial_cache = json.loads((serial_root / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    concurrent_cache = json.loads((concurrent_root / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    serial_fingerprints = [item.model_dump(mode="json") for item in serial.fingerprints]
    concurrent_fingerprints = [item.model_dump(mode="json") for item in concurrent.fingerprints]
    for payload in (*serial_fingerprints, *concurrent_fingerprints):
        payload["quality"].pop("evaluated_at")

    assert serial_fingerprints == concurrent_fingerprints
    assert serial_cache["io_name_vocabulary"] == concurrent_cache["io_name_vocabulary"]


@pytest.mark.asyncio
async def test_final_vocabulary_reconciliation_preserves_rejected_io_diagnostics(tmp_path) -> None:
    await FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        llm=InvalidEntryVocabularyLLM(),
        settings=FingerprintSettings(enable_llm_extraction=True, max_concurrency=4),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    cache = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    normalization = cache["entries"]["summarize"]["normalization"]
    invalid = [issue for issue in normalization["issues"] if issue["code"] == "invalid_io_entry"]

    assert len(invalid) == 1
    assert invalid[0]["direction"] == "input"
    assert invalid[0]["index"] == 0
    assert normalization["inputs"][0] == 0


@pytest.mark.asyncio
async def test_provider_explicit_io_preserves_unspecified_required_state(tmp_path) -> None:
    descriptor = _descriptors()[0].model_copy(
        update={"inputs": (CapabilityIO(name="query", type="text", required=None),)},
    )

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    assert artifact.fingerprints[0].inputs[0].required is None


@pytest.mark.asyncio
async def test_normalization_audit_is_private_and_redacts_credential_assignments(tmp_path) -> None:
    secret = "cache-audit-secret"
    unsafe_io = CapabilityIO(name="query", type="text").model_copy(
        update={"name": f"api_key={secret}", "type": f"access_token={secret}"},
    )
    descriptor = _descriptors()[0].model_copy(update={"inputs": (unsafe_io,)})

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    artifact_payload = json.loads((tmp_path / "fingerprint.json").read_text(encoding="utf-8"))
    cache_text = (tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8")
    public_fingerprint = artifact_payload["fingerprints"][0]

    assert (
        FingerprintService(
            FakeProvider("snapshot-1", [descriptor]),
            tmp_path,
            evaluation_suite=NoopEvaluationSuite(),
        ).read()
        == artifact
    )
    assert (
        not {
            "normalization_decisions",
            "normalization_issues",
            "io_name_vocabulary_version",
            "io_name_vocabulary_hash",
        }
        & public_fingerprint.keys()
    )
    assert "normalization" in cache_text
    assert secret not in json.dumps(artifact_payload)
    assert secret not in cache_text


@pytest.mark.asyncio
async def test_trace_change_invalidates_quality_without_repeating_extraction(tmp_path) -> None:
    llm = CountingLLM()
    evaluation = NoopEvaluationSuite()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=evaluation,
    )
    first_trace = EvaluationCase(
        case_id="case-1",
        capability_id="summarize",
        capability_type="skill",
        success=False,
    )
    changed_trace = first_trace.model_copy(update={"success": True})

    await service.build(traces=(first_trace,))
    await service.build(traces=(first_trace,))
    await service.build(traces=(changed_trace,))

    assert llm.calls == 1
    assert evaluation.calls == 2


@pytest.mark.asyncio
async def test_trace_change_does_not_retain_stale_quality_failures(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
    )
    failed = EvaluationCase(
        case_id="case-1",
        capability_id="summarize",
        capability_type="skill",
        success=False,
    )
    recovered = failed.model_copy(update={"success": True})

    first = await service.build(traces=(failed,))
    second = await service.build(traces=(recovered,))

    assert "execution_unsuccessful" in {failure.code for failure in first.fingerprints[0].failures}
    assert "execution_unsuccessful" not in {failure.code for failure in second.fingerprints[0].failures}


@pytest.mark.asyncio
async def test_descriptor_change_is_not_hidden_by_provider_content_hash(tmp_path) -> None:
    llm = CountingLLM()
    provider = FakeProvider("snapshot-1", _descriptors()[:1])
    service = FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    await service.build()
    provider.descriptors = [provider.descriptors[0].model_copy(update={"name": "Renamed", "available": False})]

    artifact = await service.build()

    assert llm.calls == 2
    assert artifact.fingerprints[0].name == "Renamed"
    assert artifact.fingerprints[0].available is False
    assert artifact.fingerprints[0].semantic_profile.summary == "Profile for Renamed"


@pytest.mark.asyncio
async def test_llm_identity_change_invalidates_extraction_cache(tmp_path) -> None:
    provider = FakeProvider("snapshot-1", _descriptors()[:1])
    first_llm = CountingLLM("model-a")
    await FingerprintService(
        provider,
        tmp_path,
        llm=first_llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    second_llm = CountingLLM("model-b")

    artifact = await FingerprintService(
        provider,
        tmp_path,
        llm=second_llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    ).build()

    assert first_llm.calls == 1
    assert second_llm.calls == 1
    assert artifact.fingerprints[0].semantic_profile.summary == "Profile model-b for Summarize"


@pytest.mark.asyncio
async def test_core_assistant_message_content_is_used_for_extraction(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=CoreMessageLLM(),
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    artifact = await service.build()

    assert artifact.fingerprints[0].semantic_profile.summary == "Profile for Summarize"
    assert artifact.fingerprints[0].classification == "General"


@pytest.mark.asyncio
async def test_opaque_llm_disables_extraction_reuse_by_default(tmp_path) -> None:
    llm = OpaqueLLM()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    await service.build()
    await service.build()

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_one_llm_extraction_failure_does_not_abort_other_capabilities(tmp_path) -> None:
    llm = PartiallyInvalidLLM()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    progress = []
    artifact = await service.build(progress_callback=progress.append)
    by_id = {item.capability_id: item for item in artifact.fingerprints}

    assert [failure.code for failure in by_id["summarize"].failures] == ["LLM_RESPONSE_INVALID"]
    assert by_id["researcher"].semantic_profile.summary == "Profile for Researcher"
    assert llm.calls == 2
    assert [(item["current"], item["total"]) for item in progress] == [(0, 2), (1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_framework_model_failure_aborts_and_preserves_published_artifact(tmp_path) -> None:
    settings = FingerprintSettings(enable_llm_extraction=True)
    provider = FakeProvider("snapshot-1", _descriptors()[:1])
    initial = await FingerprintService(
        provider,
        tmp_path,
        llm=CountingLLM("initial"),
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    ).build()
    artifact_path = tmp_path / "fingerprint.json"
    previous_bytes = artifact_path.read_bytes()
    assert initial.fingerprints

    service = FingerprintService(
        provider,
        tmp_path,
        llm=FrameworkFailingLLM(),
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    )
    progress = []
    with pytest.raises(BaseError) as exc_info:
        await service.build(force=True, progress_callback=progress.append)

    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED
    assert artifact_path.read_bytes() == previous_bytes
    assert [(item["current"], item["total"]) for item in progress] == [(0, 1)]


@pytest.mark.asyncio
async def test_force_build_invalidates_incremental_reuse(tmp_path) -> None:
    llm = CountingLLM()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    await service.build()

    await service.build(force=True)

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_protocol_signature_change_invalidates_incremental_reuse(tmp_path) -> None:
    llm = CountingLLM()
    provider = FakeProvider("snapshot-1", _descriptors()[:1])
    first_service = FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    await first_service.build()
    changed_service = FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(
            enable_llm_extraction=True,
            extraction_protocol_version="symphony-capability-extraction-v3",
        ),
        evaluation_suite=NoopEvaluationSuite(),
    )

    await changed_service.build()

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_missing_required_llm_is_an_explicit_configuration_error(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR


@pytest.mark.asyncio
async def test_legacy_trace_calls_are_a_schema_error(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    trace = {
        "capability_id": "summarize",
        "capability_type": "skill",
        "calls": [],
    }

    with pytest.raises(BaseError) as exc_info:
        await service.build(traces=(trace,))

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_trace_model_copy_cannot_bypass_message_validation(tmp_path) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    trace = EvaluationCase(
        capability_id="summarize",
        capability_type="skill",
    ).model_copy(update={"message": ({"role": "unknown", "content": "invalid"},)})

    with pytest.raises(BaseError) as exc_info:
        await service.build(traces=(trace,))

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        FingerprintSettings(batch_size=True),
        FingerprintSettings(max_concurrency=0),
        FingerprintSettings(body_limit=-1),
        FingerprintSettings(llm_timeout=float("inf")),
        FingerprintSettings(cache_enabled=cast(Any, 1)),
    ],
)
async def test_runtime_settings_reject_wrong_types_and_bounds(tmp_path, settings) -> None:
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()),
        tmp_path,
        settings=settings,
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR


@pytest.mark.asyncio
async def test_duplicate_capability_id_rejects_the_inventory(tmp_path) -> None:
    duplicate = _descriptors()[0].model_copy(update={"capability_type": "agent"})
    service = FingerprintService(
        FakeProvider("snapshot-1", [_descriptors()[0], duplicate]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


@pytest.mark.asyncio
async def test_snapshot_model_copy_cannot_bypass_count_validation(tmp_path) -> None:
    service = FingerprintService(
        InvalidSnapshotProvider("snapshot-1", _descriptors()),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


@pytest.mark.asyncio
async def test_cancelled_build_preserves_last_successful_artifact(tmp_path) -> None:
    first_service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    await first_service.build()
    llm = BlockingLLM()
    second_service = FingerprintService(
        FakeProvider("snapshot-2", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    progress = []
    task = asyncio.create_task(second_service.build(force=True, progress_callback=progress.append))
    await llm.started.wait()
    assert second_service.cancel_build()
    llm.release.set()
    with pytest.raises(BaseError) as exc_info:
        await task

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_BUILD_INTERRUPTED
    assert first_service.read().source_snapshot.snapshot_id == "snapshot-1"
    assert [(item["current"], item["total"]) for item in progress] == [(0, 1)]


@pytest.mark.asyncio
async def test_concurrent_build_is_rejected(tmp_path) -> None:
    llm = BlockingLLM()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    active = asyncio.create_task(service.build())
    await llm.started.wait()

    with pytest.raises(BaseError) as exc_info:
        await service.build()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_BUILD_STATE_INVALID
    service.cancel_build()
    llm.release.set()
    with pytest.raises(BaseError):
        await active


@pytest.mark.asyncio
async def test_task_cancellation_keeps_process_lock_until_atomic_publish_finishes(tmp_path, monkeypatch) -> None:
    first = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    second = FingerprintService(
        FakeProvider("snapshot-2", _descriptors()[:1]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    )
    publish_started = threading.Event()
    release_publish = threading.Event()
    store = first._store
    original_publish = store.publish

    def blocking_publish(artifact) -> None:
        publish_started.set()
        release_publish.wait(timeout=5)
        original_publish(artifact)

    monkeypatch.setattr(store, "publish", blocking_publish)
    active = asyncio.create_task(first.build())
    assert await asyncio.to_thread(publish_started.wait, 2)
    active.cancel()
    await asyncio.sleep(0)

    with pytest.raises(BaseError) as exc_info:
        await second.build()
    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_BUILD_STATE_INVALID
    assert not active.done()

    release_publish.set()
    with pytest.raises(asyncio.CancelledError):
        await active
    await second.build()

    assert second.read().source_snapshot.snapshot_id == "snapshot-2"


def test_normalize_io_specs_uses_corpus_vocabulary_and_returns_audit() -> None:
    corpus = (
        CapabilityDescriptor(
            capability_id="declared",
            capability_type="skill",
            name="Declared",
            inputs=(CapabilityIO(name="user_query", type="text"),),
        ),
    )
    vocabulary = build_io_name_vocabulary(corpus)

    result = normalize_io_specs(
        [
            {"name": "User Query", "type": "string"},
            {"name": "Vendor Payload", "type": "vendor/binary"},
        ],
        direction="input",
        io_name_vocabulary=vocabulary,
        include_audit=True,
    )

    assert [(item.name, item.type) for item in result.items] == [
        ("user_query", "text"),
        ("vendor_payload", "unknown"),
    ]
    name_decisions = [decision for decision in result.decisions if decision.field == "name"]
    assert [decision.method for decision in name_decisions] == ["vocab_exact", "vocab_fallback"]
    assert all(decision.details.get("vocabulary_hash") == vocabulary.content_hash for decision in name_decisions)
    assert {issue.code for issue in result.issues} == {
        "io_name_not_in_vocabulary",
        "unknown_data_type",
    }


@pytest.mark.asyncio
async def test_corpus_vocabulary_normalization_audit_is_reused_from_cache(tmp_path) -> None:
    llm = CountingLLM()
    descriptors = [
        _descriptors()[0].model_copy(update={"inputs": (CapabilityIO(name="query", type="text"),)}),
        _descriptors()[1],
    ]
    service = FingerprintService(
        FakeProvider("snapshot-1", descriptors),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    first = await service.build()
    first_cache = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    second = await service.build()
    second_cache = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    first_audit = first_cache["entries"]["researcher"]["normalization"]
    second_audit = second_cache["entries"]["researcher"]["normalization"]
    query_decision = next(
        decision
        for decision in first_audit["decisions"]
        if decision["direction"] == "input" and decision["field"] == "name"
    )

    assert llm.calls == 2
    assert first == second.model_copy(update={"generated_at": first.generated_at})
    assert query_decision["normalized_value"] == "query"
    assert query_decision["method"] == "vocab_exact"
    assert first_audit["io_name_vocabulary_hash"]
    assert second_audit == first_audit


@pytest.mark.asyncio
async def test_corpus_vocabulary_change_invalidates_other_capability_cache(tmp_path) -> None:
    llm = CountingLLM()
    provider = FakeProvider(
        "snapshot-1",
        [
            _descriptors()[0].model_copy(update={"inputs": (CapabilityIO(name="query", type="text"),)}),
            _descriptors()[1],
        ],
    )
    service = FingerprintService(
        provider,
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    first = await service.build()
    first_cache = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    provider.descriptors = [
        provider.descriptors[0].model_copy(update={"inputs": (CapabilityIO(name="prompt", type="text"),)}),
        provider.descriptors[1],
    ]

    second = await service.build()
    second_cache = json.loads((tmp_path / ".fingerprint-cache.json").read_text(encoding="utf-8"))
    first_audit = first_cache["entries"]["researcher"]["normalization"]
    second_audit = second_cache["entries"]["researcher"]["normalization"]

    assert llm.calls == 4
    assert first != second
    assert first_audit["io_name_vocabulary_hash"] != second_audit["io_name_vocabulary_hash"]
    assert any(
        decision["field"] == "name" and decision["method"] == "vocab_exact" for decision in second_audit["decisions"]
    )


@pytest.mark.asyncio
async def test_cache_entry_missing_normalization_audit_is_not_reused(tmp_path) -> None:
    llm = CountingLLM()
    service = FingerprintService(
        FakeProvider("snapshot-1", _descriptors()[:1]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )
    await service.build()
    cache_path = tmp_path / ".fingerprint-cache.json"
    cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    for entry in cache_payload["entries"].values():
        entry["normalization"].pop("decisions")
        entry["normalization"].pop("issues")
    cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")

    await service.build()

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_known_capability_type_alias_is_canonicalized_before_cache_identity(tmp_path) -> None:
    llm = CountingLLM()
    descriptor = _descriptors()[0].model_copy(update={"capability_type": "Skill"})
    service = FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        llm=llm,
        settings=FingerprintSettings(enable_llm_extraction=True),
        evaluation_suite=NoopEvaluationSuite(),
    )

    first = await service.build()
    second = await service.build()

    assert first.fingerprints[0].capability_type == "skill"
    assert second.fingerprints[0].capability_type == "skill"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_trace_case_type_alias_is_canonicalized_before_matching(tmp_path) -> None:
    descriptor = _descriptors()[1].model_copy(update={"capability_type": "SubAgent"})
    trace = EvaluationCase(
        capability_id="researcher",
        capability_type="SUB-AGENT",
        success=True,
    )

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build(traces=(trace,))

    assert artifact.fingerprints[0].capability_type == "agent"
    assert artifact.fingerprints[0].quality is not None
    assert artifact.fingerprints[0].quality.sample_count == 1


@pytest.mark.asyncio
async def test_trace_message_function_name_references_child_fingerprint(tmp_path) -> None:
    descriptor = _descriptors()[1]
    trace = EvaluationCase(
        capability_id="parent",
        capability_type="workflow",
        message=(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "research-step",
                        "type": "function",
                        "function": {"name": "researcher", "arguments": '{"topic":"weather"}'},
                    }
                ],
            },
        ),
    )

    artifact = await FingerprintService(
        FakeProvider("snapshot-1", [descriptor]),
        tmp_path,
        evaluation_suite=NoopEvaluationSuite(),
    ).build(traces=(trace,))

    assert artifact.fingerprints[0].quality is not None
    assert artifact.fingerprints[0].quality.sample_count == 1


@pytest.mark.asyncio
async def test_empty_provider_description_remains_empty_for_quality_evaluation(tmp_path) -> None:
    descriptor = _descriptors()[0].model_copy(update={"description": ""})

    artifact = await FingerprintService(FakeProvider("snapshot-1", [descriptor]), tmp_path).build()
    fingerprint = artifact.fingerprints[0]
    description_metric = next(
        metric for metric in fingerprint.quality.metrics if metric.metric_id == "description_quality"
    )

    assert fingerprint.description == ""
    assert fingerprint.semantic_profile.summary == fingerprint.name
    assert description_metric.status is MetricStatus.FAIL
    assert description_metric.failures[0].code == "missing_description"


@pytest.mark.asyncio
async def test_failed_extraction_does_not_invent_a_description(monkeypatch) -> None:
    descriptor = _descriptors()[0].model_copy(update={"description": ""})
    extractor = CapabilityFingerprintExtractor(FingerprintSettings())

    async def fail_extract(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(extractor, "extract", fail_extract)
    outcome = (await extractor.extract_many((descriptor,)))[0]

    assert outcome.fingerprint.description == ""
    assert outcome.fingerprint.semantic_profile.summary == descriptor.name
    assert outcome.fingerprint.failures[0].code == "FINGERPRINT_EXTRACTION_FAILED"
