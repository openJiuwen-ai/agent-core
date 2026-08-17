# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Atomic batch mutation coverage for the Symphony capability graph."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony import (
    CapabilityFingerprint,
    CapabilityIO,
    OrchestrationService,
    SkillGraphAdd,
    SkillGraphDelete,
    SkillGraphUpdate,
    SkillGraphUpdater,
    SourceSnapshot,
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
        self.replace(revision, fingerprints)

    def replace(self, revision: str, fingerprints: tuple[CapabilityFingerprint, ...]) -> SourceSnapshot:
        self.fingerprints = fingerprints
        self.snapshot = _snapshot(revision, fingerprints)
        return self.snapshot

    async def inventory_snapshot(self):
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


def _service(tmp_path: Path, provider: _AtomicProvider, model: _MatcherLLM) -> OrchestrationService:
    return OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=provider,
        model=model,
        graph_config={"require_consensus": False},
    )


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
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (extract, summarize))

    result = await service.add_skills(
        [SkillGraphAdd(summarize)],
        request_id="add-summarize",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
    )
    replay = await service.add_skills(
        [SkillGraphAdd(summarize)],
        request_id="add-summarize",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
    )

    assert isinstance(service, SkillGraphUpdater)
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
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (raw, translate, publish))

    result = await service.add_skills(
        [SkillGraphAdd(translate), SkillGraphAdd(publish)],
        request_id="add-translation-pipeline",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
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
    initial = await service.build()
    assert len(service.read()["edges"]) == 1

    changed = _fingerprint(
        "extract",
        "extract-v2",
        outputs=(CapabilityIO(name="image", type="image"),),
    )
    target_snapshot = provider.replace("snapshot-2", (changed, summarize))
    result = await service.update_skills(
        [SkillGraphUpdate(changed, expected_content_hash="extract-v1")],
        request_id="update-extract",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
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
    initial = await service.build()
    calls_after_build = model.calls
    changed = extract.model_copy(
        update={
            "content_hash": "extract-quality-v2",
            "static_data": {"quality_label": "verified"},
        }
    )
    target_snapshot = provider.replace("snapshot-2", (changed, summarize))

    result = await service.update_skills(
        [SkillGraphUpdate(changed, expected_content_hash="extract-v1")],
        request_id="quality-only-update",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
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
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (extract,))

    result = await service.delete_skills(
        [SkillGraphDelete("summarize", expected_content_hash="summarize-v1")],
        request_id="delete-summarize",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
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

    result = await service.update_skills(
        [SkillGraphUpdate(extract, expected_content_hash="extract-v1")],
        request_id="noop-extract",
        expected_graph_version=initial.version,
        source_snapshot=provider.snapshot,
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
    initial = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    target_snapshot = provider.replace("snapshot-2", (first, second, third))

    with pytest.raises(BaseError) as exc_info:
        await service.add_skills(
            [SkillGraphAdd(second)],
            request_id="incomplete-add",
            expected_graph_version=initial.version,
            source_snapshot=target_snapshot,
        )

    assert _mutation_code(exc_info) == "change_set_mismatch"
    assert (tmp_path / "current.json").read_bytes() == current_before


@pytest.mark.asyncio
async def test_stale_version_and_content_hash_are_structured_conflicts(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    changed = _fingerprint("first", "first-v2")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (changed,))

    with pytest.raises(BaseError) as stale_version:
        await service.update_skills(
            [SkillGraphUpdate(changed, expected_content_hash="first-v1")],
            request_id="stale-version",
            expected_graph_version="missing-version",
            source_snapshot=target_snapshot,
        )
    assert _mutation_code(stale_version) == "stale_graph_version"

    with pytest.raises(BaseError) as stale_hash:
        await service.update_skills(
            [SkillGraphUpdate(changed, expected_content_hash="wrong-hash")],
            request_id="stale-hash",
            expected_graph_version=initial.version,
            source_snapshot=target_snapshot,
        )
    assert _mutation_code(stale_hash) == "stale_content_hash"


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
        await service.add_skills(
            [SkillGraphAdd(summarize)],
            request_id="matcher-failure",
            expected_graph_version=initial.version,
            source_snapshot=target_snapshot,
        )

    assert _mutation_code(exc_info) == "matcher_failed"
    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status(expected_snapshot=service.read()["source_snapshot"]).version == initial.version


@pytest.mark.asyncio
async def test_request_id_cannot_be_reused_for_a_different_mutation(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()
    target_snapshot = provider.replace("snapshot-2", (first, second))
    await service.add_skills(
        [SkillGraphAdd(second)],
        request_id="shared-request-id",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
    )

    with pytest.raises(BaseError) as exc_info:
        await service.add_skills(
            [SkillGraphAdd(_fingerprint("different", "different-v1"))],
            request_id="shared-request-id",
            expected_graph_version=initial.version,
            source_snapshot=target_snapshot,
        )

    assert _mutation_code(exc_info) == "request_id_conflict"


@pytest.mark.asyncio
async def test_idempotent_replay_survives_service_restart(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    first_service = _service(tmp_path, provider, _MatcherLLM())
    initial = await first_service.build()
    target_snapshot = provider.replace("snapshot-2", (first, second))
    published = await first_service.add_skills(
        [SkillGraphAdd(second)],
        request_id="restart-safe-request",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
    )

    restarted_model = _MatcherLLM()
    restarted_service = _service(tmp_path, provider, restarted_model)
    replay = await restarted_service.add_skills(
        [SkillGraphAdd(second)],
        request_id="restart-safe-request",
        expected_graph_version=initial.version,
        source_snapshot=target_snapshot,
    )

    assert replay == published
    assert restarted_model.calls == 0


@pytest.mark.asyncio
async def test_invalid_batches_are_rejected_before_inventory_mutation(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()

    with pytest.raises(BaseError) as empty_batch:
        await service.add_skills(
            [],
            request_id="empty-batch",
            expected_graph_version=initial.version,
            source_snapshot=provider.snapshot,
        )
    assert _mutation_code(empty_batch) == "empty_batch"

    with pytest.raises(BaseError) as duplicate:
        await service.add_skills(
            [SkillGraphAdd(second), SkillGraphAdd(second)],
            request_id="duplicate-batch",
            expected_graph_version=initial.version,
            source_snapshot=provider.snapshot,
        )
    assert _mutation_code(duplicate) == "duplicate_capability"


@pytest.mark.asyncio
async def test_source_snapshot_mismatch_keeps_active_version(tmp_path: Path) -> None:
    first = _fingerprint("first", "first-v1")
    second = _fingerprint("second", "second-v1")
    provider = _AtomicProvider("snapshot-1", (first,))
    service = _service(tmp_path, provider, _MatcherLLM())
    initial = await service.build()
    provider.replace("snapshot-2", (first, second))
    mismatched = SourceSnapshot(
        snapshot_id="different-snapshot",
        source="unit-test",
        content_hash="different-content",
        capability_count=2,
    )

    with pytest.raises(BaseError) as exc_info:
        await service.add_skills(
            [SkillGraphAdd(second)],
            request_id="snapshot-mismatch",
            expected_graph_version=initial.version,
            source_snapshot=mismatched,
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
    initial = await service.build()
    service.capability_provider = [first, second]

    with pytest.raises(BaseError) as exc_info:
        await service.add_skills(
            [SkillGraphAdd(second)],
            request_id="legacy-provider-add",
            expected_graph_version=initial.version,
            source_snapshot=_snapshot("snapshot-2", (first, second)),
        )

    assert _mutation_code(exc_info) == "full_rebuild_required"
