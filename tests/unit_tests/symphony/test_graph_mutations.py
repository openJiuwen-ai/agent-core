# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Atomic batch mutation coverage for the Symphony capability graph."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.symphony import (
    CapabilityFingerprint,
    CapabilityIO,
    FingerprintArtifact,
    FingerprintService,
    GraphMutationResult,
    OrchestrationService,
    SkillGraphUpdater,
    SourceSnapshot,
    SymphonyGraphEngine,
)


def _fingerprint(
    capability_id: str,
    content_hash: str,
    *,
    inputs: tuple[CapabilityIO, ...] = (),
    outputs: tuple[CapabilityIO, ...] = (),
) -> CapabilityFingerprint:
    return CapabilityFingerprint(
        capability_id=capability_id,
        capability_type="skill",
        name=capability_id,
        description=f"Capability {capability_id}",
        version="1.0.0",
        inputs=inputs,
        outputs=outputs,
        content_hash=content_hash,
    )


def _snapshot(revision: str, fingerprints: tuple[CapabilityFingerprint, ...]) -> SourceSnapshot:
    identities = sorted((item.capability_type, item.capability_id, item.content_hash) for item in fingerprints)
    content_hash = hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()
    return SourceSnapshot(
        snapshot_id=revision,
        source="unit-test",
        content_hash=content_hash,
        capability_count=len(fingerprints),
    )


class _AtomicProvider:
    def __init__(self, revision: str, fingerprints: tuple[CapabilityFingerprint, ...]) -> None:
        self.inventory_calls = 0
        self.replace(revision, fingerprints)

    def replace(self, revision: str, fingerprints: tuple[CapabilityFingerprint, ...]) -> SourceSnapshot:
        self.fingerprints = fingerprints
        self.snapshot = _snapshot(revision, fingerprints)
        return self.snapshot

    async def inventory_snapshot(self):
        self.inventory_calls += 1
        return self.snapshot, self.fingerprints

    async def capabilities(self):
        raise AssertionError("atomic inventory_snapshot() must be used")

    async def source_snapshot(self):
        raise AssertionError("atomic inventory_snapshot() must be used")


class _MatcherLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.failure: Exception | None = None

    async def invoke(self, messages, **kwargs):
        del kwargs
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        payload = json.loads(messages[-1]["content"])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "matches": [
                        {
                            "id": item["id"],
                            "direction": "forward",
                            "confidence": 0.9,
                            "accepted": True,
                            "reason": "ports are compatible",
                        }
                        for item in payload["candidates"]
                    ]
                }
            )
        )


class _InternalFingerprintService:
    def __init__(self, provider: _AtomicProvider) -> None:
        self.provider = provider
        self.calls = 0

    async def build(self, *, force: bool = False) -> FingerprintArtifact:
        del force
        self.calls += 1
        snapshot, fingerprints = await self.provider.inventory_snapshot()
        return FingerprintArtifact(source_snapshot=snapshot, fingerprints=fingerprints)


def _service(
    tmp_path: Path,
    provider: _AtomicProvider,
    model: _MatcherLLM,
    *,
    fingerprint_service: FingerprintService | None = None,
) -> OrchestrationService:
    return OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=provider,
        model=model,
        graph_config={"require_consensus": False},
        fingerprint_service=fingerprint_service,
    )


def _engine(service: OrchestrationService) -> SymphonyGraphEngine:
    return SymphonyGraphEngine(service)


def _mutation_code(exc_info: pytest.ExceptionInfo[BaseError]) -> str:
    details = exc_info.value.details
    assert isinstance(details, dict)
    return str(details["mutation_code"])


@pytest.mark.asyncio
async def test_add_skills_publishes_one_version_and_replay_is_idempotent(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract,))
    model = _MatcherLLM()
    service = _service(tmp_path, provider, model)
    graph_engine = SymphonyGraphEngine(service)
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (extract, summarize))

    result = await graph_engine.add_skills(
        ["summarize"],
        request_id="add-summarize",
        source_revision=target_snapshot.snapshot_id,
    )
    replay = await graph_engine.add_skills(
        ["summarize"],
        request_id="add-summarize",
        source_revision=target_snapshot.snapshot_id,
    )

    assert isinstance(graph_engine, SkillGraphUpdater)
    assert not isinstance(service, SkillGraphUpdater)
    assert result == replay
    assert result.status == "published"
    assert result.previous_version == initial.version
    assert result.delta.added_node_count == 1
    assert result.delta.added_edge_count == 1
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == result.version
    assert service.read()["lookup"]["neighbors"] == {"extract": ["summarize"]}
    assert service.read()["mutation"]["request_id"] == "add-summarize"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_add_skills_builds_old_to_new_and_new_to_new_edges_in_one_version(tmp_path: Path) -> None:
    raw = _fingerprint(
        "raw",
        "raw-v1",
        outputs=(CapabilityIO(name="text", type="text"),),
    )
    translate = _fingerprint(
        "translate",
        "translate-v1",
        inputs=(CapabilityIO(name="text", type="text"),),
        outputs=(CapabilityIO(name="translated", type="markdown"),),
    )
    publish = _fingerprint(
        "publish",
        "publish-v1",
        inputs=(CapabilityIO(name="translated", type="markdown"),),
    )
    provider = _AtomicProvider("snapshot-1", (raw,))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()
    target_snapshot = provider.replace("snapshot-2", (raw, translate, publish))

    result = await _engine(service).add_skills(
        ["translate", "publish"],
        request_id="add-translation-pipeline",
        source_revision=target_snapshot.snapshot_id,
    )

    edges = {(item["source"], item["target"]) for item in service.read()["edges"]}
    assert result.changed_capability_ids == ("publish", "translate")
    assert result.delta.added_node_count == 2
    assert edges == {
        ("capability:raw", "capability:translate"),
        ("capability:translate", "capability:publish"),
    }


@pytest.mark.asyncio
async def test_update_skills_recomputes_incident_edges_and_preserves_other_cache(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract, summarize))
    model = _MatcherLLM()
    service = _service(tmp_path, provider, model)
    await service.build()
    assert len(service.read()["edges"]) == 1

    changed = _fingerprint(
        "extract",
        "extract-v2",
        outputs=(CapabilityIO(name="image", type="image"),),
    )
    target_snapshot = provider.replace("snapshot-2", (changed, summarize))
    result = await _engine(service).update_skills(
        ["extract"],
        request_id="update-extract",
        source_revision=target_snapshot.snapshot_id,
    )

    assert result.delta.updated_node_count == 1
    assert result.delta.removed_edge_count == 1
    assert service.read()["edges"] == []
    assert service.read()["lookup"]["neighbors"] == {}


@pytest.mark.asyncio
async def test_non_graph_fingerprint_update_reuses_relation_cache(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract, summarize))
    model = _MatcherLLM()
    service = _service(tmp_path, provider, model)
    await service.build()
    calls_after_build = model.calls
    changed = extract.model_copy(
        update={
            "content_hash": "extract-quality-v2",
            "static_data": {"quality_label": "verified"},
        }
    )
    target_snapshot = provider.replace("snapshot-2", (changed, summarize))

    result = await _engine(service).update_skills(
        ["extract"],
        request_id="quality-only-update",
        source_revision=target_snapshot.snapshot_id,
    )

    assert result.delta.updated_node_count == 0
    assert result.delta.added_edge_count == 0
    assert result.delta.removed_edge_count == 0
    assert model.calls == calls_after_build


@pytest.mark.asyncio
async def test_delete_skills_removes_node_edges_and_lookup_references(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract, summarize))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()
    target_snapshot = provider.replace("snapshot-2", (extract,))

    result = await _engine(service).delete_skills(
        ["summarize"],
        request_id="delete-summarize",
        source_revision=target_snapshot.snapshot_id,
    )

    graph = service.read()
    assert result.delta.removed_node_count == 1
    assert result.delta.removed_edge_count == 1
    assert [item["id"] for item in graph["nodes"]] == ["capability:extract"]
    assert "summarize" not in json.dumps(graph["lookup"])


@pytest.mark.asyncio
async def test_update_noop_does_not_create_a_graph_version(tmp_path: Path) -> None:
    extract = _fingerprint("extract", "extract-v1")
    provider = _AtomicProvider("snapshot-1", (extract,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()

    result = await _engine(service).update_skills(
        ["extract"],
        request_id="noop-extract",
        source_revision=provider.snapshot.snapshot_id,
    )

    assert result.status == "noop"
    assert result.version == initial.version
    assert result.delta.added_node_count == 0
    assert [item.name for item in (tmp_path / "versions").iterdir()] == [initial.version]


@pytest.mark.asyncio
async def test_unrelated_provider_change_rejects_whole_batch(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    third = _fingerprint("third", "third-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    target_snapshot = provider.replace("snapshot-2", (first, second, third))

    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["second"],
            request_id="incomplete-add",
            source_revision=target_snapshot.snapshot_id,
        )

    assert _mutation_code(exc_info) == "change_set_mismatch"
    assert (tmp_path / "current.json").read_bytes() == current_before


@pytest.mark.asyncio
async def test_operation_preconditions_are_derived_from_internal_graph_state(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()

    with pytest.raises(BaseError) as already_exists:
        await _engine(service).add_skills(
            ["first"],
            request_id="add-existing",
            source_revision=provider.snapshot.snapshot_id,
        )
    assert _mutation_code(already_exists) == "already_exists"

    with pytest.raises(BaseError) as not_found:
        await _engine(service).update_skills(
            ["missing"],
            request_id="update-missing",
            source_revision=provider.snapshot.snapshot_id,
        )
    assert _mutation_code(not_found) == "not_found"


@pytest.mark.asyncio
async def test_matcher_failure_leaves_active_graph_unchanged(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract,))
    model = _MatcherLLM()
    service = _service(tmp_path, provider, model)
    initial = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    target_snapshot = provider.replace("snapshot-2", (extract, summarize))
    model.failure = RuntimeError("matcher unavailable")

    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["summarize"],
            request_id="matcher-failure",
            source_revision=target_snapshot.snapshot_id,
        )

    assert _mutation_code(exc_info) == "matcher_failed"
    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == initial.version


@pytest.mark.asyncio
async def test_framework_matcher_failure_preserves_original_error(tmp_path: Path) -> None:
    text_output = CapabilityIO(name="text", type="text")
    text_input = CapabilityIO(name="text", type="text", required=True)
    extract = _fingerprint("extract", "extract-v1", outputs=(text_output,))
    summarize = _fingerprint("summarize", "summarize-v1", inputs=(text_input,))
    provider = _AtomicProvider("snapshot-1", (extract,))
    model = _MatcherLLM()
    service = _service(tmp_path, provider, model)
    initial = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    target_snapshot = provider.replace("snapshot-2", (extract, summarize))
    error = build_error(StatusCode.MODEL_CALL_FAILED, error_msg="model unavailable")
    model.failure = error

    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["summarize"],
            request_id="framework-matcher-failure",
            source_revision=target_snapshot.snapshot_id,
        )

    assert exc_info.value is error
    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED
    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == initial.version


@pytest.mark.asyncio
async def test_request_id_cannot_be_reused_for_a_different_mutation(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()
    target_snapshot = provider.replace("snapshot-2", (first, second))
    await _engine(service).add_skills(
        ["second"],
        request_id="shared-request-id",
        source_revision=target_snapshot.snapshot_id,
    )

    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["different"],
            request_id="shared-request-id",
            source_revision=target_snapshot.snapshot_id,
        )

    assert _mutation_code(exc_info) == "request_id_conflict"


@pytest.mark.asyncio
async def test_idempotent_replay_survives_service_restart(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    first_service = _service(tmp_path, provider, _MatcherLLM())
    await first_service.build()
    target_snapshot = provider.replace("snapshot-2", (first, second))
    published = await _engine(first_service).add_skills(
        ["second"],
        request_id="restart-safe-request",
        source_revision=target_snapshot.snapshot_id,
    )

    restarted_model = _MatcherLLM()
    restarted_service = _service(tmp_path, provider, restarted_model)
    replay = await _engine(restarted_service).add_skills(
        ["second"],
        request_id="restart-safe-request",
        source_revision=target_snapshot.snapshot_id,
    )

    assert replay == published
    assert restarted_model.calls == 0


@pytest.mark.asyncio
async def test_invalid_batches_are_rejected_before_inventory_mutation(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    await service.build()
    inventory_calls_after_build = provider.inventory_calls

    with pytest.raises(BaseError) as empty_batch:
        await _engine(service).add_skills(
            [],
            request_id="empty-batch",
            source_revision=provider.snapshot.snapshot_id,
        )
    assert _mutation_code(empty_batch) == "empty_batch"

    with pytest.raises(BaseError) as duplicate:
        await _engine(service).add_skills(
            ["second", "second"],
            request_id="duplicate-batch",
            source_revision=provider.snapshot.snapshot_id,
        )
    assert _mutation_code(duplicate) == "duplicate_capability"

    with pytest.raises(BaseError) as scalar_ids:
        await _engine(service).add_skills(
            "second",  # type: ignore[arg-type]
            request_id="scalar-ids",
            source_revision=provider.snapshot.snapshot_id,
        )
    assert _mutation_code(scalar_ids) == "invalid_request"

    with pytest.raises(BaseError) as blank_revision:
        await _engine(service).add_skills(
            ["second"],
            request_id="blank-revision",
            source_revision=" ",
        )
    assert _mutation_code(blank_revision) == "invalid_request"
    assert provider.inventory_calls == inventory_calls_after_build


@pytest.mark.asyncio
async def test_source_snapshot_mismatch_keeps_active_version(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()
    provider.replace("snapshot-2", (first, second))
    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["second"],
            request_id="snapshot-mismatch",
            source_revision="different-snapshot",
        )

    assert _mutation_code(exc_info) == "source_snapshot_mismatch"
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == initial.version


@pytest.mark.asyncio
async def test_legacy_provider_requires_full_rebuild_for_mutations(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=[first],
        model=_MatcherLLM(),
        graph_config={"require_consensus": False},
    )
    await service.build()
    service.capability_provider = [first, second]

    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["second"],
            request_id="legacy-provider-add",
            source_revision="snapshot-2",
        )

    assert _mutation_code(exc_info) == "full_rebuild_required"


@pytest.mark.asyncio
async def test_service_resolves_target_fingerprints_internally(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    fingerprint_service = _InternalFingerprintService(provider)
    service = _service(
        tmp_path,
        provider,
        _MatcherLLM(),
        fingerprint_service=cast(FingerprintService, fingerprint_service),
    )
    await service.build()
    target_snapshot = provider.replace("snapshot-2", (first, second))

    result = await _engine(service).add_skills(
        ["second"],
        request_id="internal-fingerprint-build",
        source_revision=target_snapshot.snapshot_id,
    )

    assert result.status == "published"
    assert fingerprint_service.calls == 2
    assert provider.inventory_calls == 2


@pytest.mark.asyncio
async def test_publish_failure_keeps_active_graph_unchanged(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    target_snapshot = provider.replace("snapshot-2", (first, second))

    def fail_before_activate(_version_dir: Path) -> None:
        raise OSError("simulated publish failure")

    service.prepare_artifact = fail_before_activate
    with pytest.raises(BaseError) as exc_info:
        await _engine(service).add_skills(
            ["second"],
            request_id="publish-failure",
            source_revision=target_snapshot.snapshot_id,
        )

    assert _mutation_code(exc_info) == "publish_failed"
    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == initial.version


@pytest.mark.asyncio
async def test_concurrent_services_publish_at_most_one_mutation(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    first_service = _service(tmp_path, provider, _MatcherLLM())
    await first_service.build()
    second_service = _service(tmp_path, provider, _MatcherLLM())
    target_snapshot = provider.replace("snapshot-2", (first, second))

    both_staged = asyncio.Event()
    stage_lock = asyncio.Lock()
    staged_count = 0

    async def wait_for_competing_publish(_version_dir: Path) -> None:
        nonlocal staged_count
        async with stage_lock:
            staged_count += 1
            if staged_count == 2:
                both_staged.set()
        await asyncio.wait_for(both_staged.wait(), timeout=5)

    first_service.prepare_artifact = wait_for_competing_publish
    second_service.prepare_artifact = wait_for_competing_publish
    outcomes = await asyncio.gather(
        _engine(first_service).add_skills(
            ["second"],
            request_id="concurrent-add-1",
            source_revision=target_snapshot.snapshot_id,
        ),
        _engine(second_service).add_skills(
            ["second"],
            request_id="concurrent-add-2",
            source_revision=target_snapshot.snapshot_id,
        ),
        return_exceptions=True,
    )

    published = [item for item in outcomes if isinstance(item, GraphMutationResult)]
    rejected = [item for item in outcomes if isinstance(item, BaseError)]
    assert len(published) == 1
    assert len(rejected) == 1
    assert len(published) + len(rejected) == len(outcomes)
    assert rejected[0].details["mutation_code"] == "stale_graph_version"
    current = first_service.status(expected_snapshot=first_service.read()["source_snapshot"])
    assert current.version == published[0].version
