import asyncio
import json
from pathlib import Path

import pytest
import openjiuwen.symphony.orchestration.graph.matcher.cache as cache_module

from openjiuwen.symphony import (
    ArtifactSpec,
    CachedOntologyMatcher as PublicCachedOntologyMatcher,
    CapabilityFingerprint,
    Fingerprint,
    OntologyMatcher as PublicOntologyMatcher,
    OpenAICompatibleOntologyMatcher as PublicOpenAICompatibleOntologyMatcher,
    ParameterSpec,
)
from openjiuwen.symphony.orchestration.graph import (
    CachedOntologyMatcher,
    LLMMatch,
    RelationCacheStats,
    RelationCandidate,
    RelationMatchCache,
    SkillRegistry,
)
from openjiuwen.symphony.orchestration import (
    CachedOntologyMatcher as OrchestrationCachedOntologyMatcher,
    OntologyMatcher as OrchestrationOntologyMatcher,
    OpenAICompatibleOntologyMatcher as OrchestrationOpenAICompatibleOntologyMatcher,
)
from openjiuwen.symphony.shared.fingerprint import coerce_fingerprint


def test_ontology_matchers_are_exported_from_public_apis() -> None:
    from openjiuwen.symphony import __all__ as symphony_exports
    from openjiuwen.symphony.orchestration import __all__ as orchestration_exports
    from openjiuwen.symphony.orchestration.graph import (
        CachedOntologyMatcher as GraphCachedOntologyMatcher,
        OntologyMatcher as GraphOntologyMatcher,
        OpenAICompatibleOntologyMatcher as GraphOpenAICompatibleOntologyMatcher,
    )
    from openjiuwen.symphony.orchestration.graph.matcher import OntologyMatcher as MatcherOntologyMatcher

    assert PublicCachedOntologyMatcher is OrchestrationCachedOntologyMatcher is GraphCachedOntologyMatcher
    assert PublicOntologyMatcher is OrchestrationOntologyMatcher is GraphOntologyMatcher is MatcherOntologyMatcher
    assert (
        PublicOpenAICompatibleOntologyMatcher
        is OrchestrationOpenAICompatibleOntologyMatcher
        is GraphOpenAICompatibleOntologyMatcher
    )
    for matcher_name in (
        "CachedOntologyMatcher",
        "OntologyMatcher",
        "OpenAICompatibleOntologyMatcher",
    ):
        assert matcher_name in symphony_exports
        assert matcher_name in orchestration_exports


def _fingerprint(capability_id: str, *, description: str = "", static_value: str = "") -> Fingerprint:
    return Fingerprint(
        type="skill",
        id=capability_id,
        name=capability_id,
        description=description or capability_id,
        version="1.0.0",
        inputs=[ParameterSpec(name="input", type="text")],
        outputs=[ArtifactSpec(name="output", type="text")],
        static_data={"documentation": static_value},
    )


def _candidate(source_id: str, target_id: str) -> RelationCandidate:
    return RelationCandidate(
        source_id=source_id,
        target_id=target_id,
        relation_hints=["can_feed"],
        candidate_methods=["test"],
        priority="medium",
        evidence={"pair": f"{source_id}:{target_id}"},
    )


class _CountingMatcher:
    def __init__(
        self,
        *,
        model: str = "model-a",
        endpoint: str = "https://api.example.test/v1",
        batch_size: int = 12,
        max_workers: int = 1,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.thresholds = {"can_feed": 0.7}
        self.calls: list[list[str]] = []
        self.diagnostics: list = []

    async def match(self, registry, candidates):
        del registry
        candidate_list = list(candidates)
        self.calls.append([item.key for item in candidate_list])
        return [
            LLMMatch(
                source_id=item.source_id,
                target_id=item.target_id,
                relation_type="can_feed",
                confidence=0.9,
                candidate_id=item.key,
                accepted=True,
            )
            for item in reversed(candidate_list)
        ]

    def manifest_metadata(self):
        return {
            "matcher": "counting",
            "model": self.model,
            "api_key": "top-secret-key",
            "base_url": self.endpoint,
            "client": {
                "api_base": self.endpoint,
                "password": "nested-secret-password",
            },
        }


def test_graph_identity_is_stable_across_public_fingerprint_shapes() -> None:
    legacy = _fingerprint("source", static_value="first")
    public = CapabilityFingerprint(
        capability_id=legacy.id,
        capability_type=legacy.type,
        name=legacy.name,
        description=legacy.description,
        version=legacy.version,
        inputs=legacy.inputs,
        outputs=legacy.outputs,
        static_data={"documentation": "second"},
    )

    assert legacy.graph_identity_dict() == public.graph_identity_dict()
    assert legacy.graph_identity_dict() == coerce_fingerprint(public).graph_identity_dict()
    assert "static_data" not in legacy.graph_identity_dict()


@pytest.mark.asyncio
async def test_relation_cache_hits_preserve_candidate_order_and_manifest(tmp_path: Path) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c")]
    candidates = [_candidate("a", "c"), _candidate("a", "b")]
    path = tmp_path / "relations.json"
    first_matcher = _CountingMatcher()
    first = CachedOntologyMatcher(first_matcher, path, fingerprints=fingerprints)

    first_matches = await first.match(SkillRegistry(skills={}), candidates)
    second_matcher = _CountingMatcher()
    second = CachedOntologyMatcher(second_matcher, path, fingerprints=fingerprints)
    second_matches = await second.match(SkillRegistry(skills={}), candidates)

    assert [item.candidate_id for item in first_matches] == [item.key for item in candidates]
    assert [item.candidate_id for item in second_matches] == [item.key for item in candidates]
    assert first.stats == RelationCacheStats(reused_count=0, resolved_count=2, stored_count=2)
    assert second.stats == RelationCacheStats(reused_count=2, resolved_count=0, stored_count=0)
    assert second_matcher.calls == []
    assert second.manifest_metadata()["relation_cache"] == {
        "schema_version": "Symphony-relation-match-cache-v1",
        "reused_count": 2,
        "resolved_count": 0,
    }
    assert "api_key" not in second.cache.matcher_signature
    assert "base_url" not in second.cache.matcher_signature


@pytest.mark.asyncio
async def test_relation_cache_hashes_endpoint_and_invalidates_on_endpoint_switch(tmp_path: Path) -> None:
    path = tmp_path / "relations.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidates = [_candidate("a", "b")]
    first = CachedOntologyMatcher(
        _CountingMatcher(endpoint=" HTTPS://API.EXAMPLE.TEST/v1/ "),
        path,
        fingerprints=fingerprints,
    )
    await first.match(SkillRegistry(skills={}), candidates)
    equivalent_matcher = _CountingMatcher(endpoint="https://api.example.test/v1")
    equivalent = CachedOntologyMatcher(equivalent_matcher, path, fingerprints=fingerprints)

    await equivalent.match(SkillRegistry(skills={}), candidates)

    assert equivalent.stats.reused_count == 1
    assert equivalent_matcher.calls == []
    switched_matcher = _CountingMatcher(endpoint="https://api.example.test/v2")
    switched = CachedOntologyMatcher(switched_matcher, path, fingerprints=fingerprints)
    await switched.match(SkillRegistry(skills={}), candidates)
    assert switched.stats.resolved_count == 1
    assert switched_matcher.calls == [["a->b"]]

    serialized_signature = json.dumps(switched.cache.matcher_signature, sort_keys=True)
    serialized_manifest = json.dumps(switched.manifest_metadata(), sort_keys=True)
    serialized_cache_artifact = path.read_text(encoding="utf-8")
    for forbidden in (
        "api.example.test",
        "top-secret-key",
        "nested-secret-password",
    ):
        assert forbidden not in serialized_signature
        assert forbidden not in serialized_manifest
        assert forbidden not in serialized_cache_artifact
    assert switched.cache.matcher_signature["base_url_sha256"]
    assert switched.cache.matcher_signature["client"]["api_base_sha256"]


@pytest.mark.asyncio
async def test_relation_cache_invalidates_only_candidates_with_changed_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "relations.json"
    candidates = [_candidate("a", "b"), _candidate("a", "c")]
    first_fingerprints = [_fingerprint(item) for item in ("a", "b", "c")]
    await CachedOntologyMatcher(_CountingMatcher(), path, fingerprints=first_fingerprints).match(
        SkillRegistry(skills={}),
        candidates,
    )
    changed = [_fingerprint("a"), _fingerprint("b", description="changed"), _fingerprint("c")]
    matcher = _CountingMatcher()
    cached = CachedOntologyMatcher(matcher, path, fingerprints=changed)

    matches = await cached.match(SkillRegistry(skills={}), candidates)

    assert [item.candidate_id for item in matches] == [item.key for item in candidates]
    assert matcher.calls == [["a->b"]]
    assert cached.stats == RelationCacheStats(reused_count=1, resolved_count=1, stored_count=1)


@pytest.mark.asyncio
async def test_relation_cache_invalidates_when_matcher_configuration_changes(tmp_path: Path) -> None:
    path = tmp_path / "relations.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidates = [_candidate("a", "b")]
    await CachedOntologyMatcher(_CountingMatcher(model="model-a"), path, fingerprints=fingerprints).match(
        SkillRegistry(skills={}),
        candidates,
    )
    matcher = _CountingMatcher(model="model-b")
    cached = CachedOntologyMatcher(matcher, path, fingerprints=fingerprints)

    await cached.match(SkillRegistry(skills={}), candidates)

    assert matcher.calls == [["a->b"]]
    assert cached.stats.resolved_count == 1
    assert cached.stats.reused_count == 0


@pytest.mark.asyncio
async def test_relation_cache_flushes_each_worker_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    matcher = _CountingMatcher(batch_size=2, max_workers=3)
    cached = CachedOntologyMatcher(matcher, tmp_path / "relations.json", fingerprints=[])
    candidates = [_candidate(f"source-{index}", f"target-{index}") for index in range(8)]
    flush_count = 0
    original_flush = cached.cache.flush

    def count_flush() -> None:
        nonlocal flush_count
        flush_count += 1
        original_flush()

    monkeypatch.setattr(cached.cache, "flush", count_flush)

    matches = await cached.match(SkillRegistry(skills={}), candidates)

    assert [len(call) for call in matcher.calls] == [6, 2]
    assert [item.candidate_id for item in matches] == [item.key for item in candidates]
    assert flush_count == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_relation_match_cache_flush_is_readable_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "relations.json"
    fingerprint = [_fingerprint(item) for item in ("a", "b")]
    candidate = _candidate("a", "b")
    match = LLMMatch(
        source_id="a",
        target_id="b",
        relation_type="can_feed",
        confidence=0.9,
        candidate_id=candidate.key,
        accepted=True,
    )
    cache = RelationMatchCache(path, matcher_signature={"matcher": "test"}, fingerprints=fingerprint)
    cache.store(candidate, [match])
    cache.flush()
    content = path.read_bytes()
    cache.flush()

    reloaded = RelationMatchCache(path, matcher_signature={"matcher": "test"}, fingerprints=fingerprint)
    assert reloaded.load(candidate) == [match]
    assert path.read_bytes() == content
    assert json.loads(content)["schema_version"] == "Symphony-relation-match-cache-index-v1"


@pytest.mark.asyncio
async def test_relation_cache_concurrent_instances_merge_different_keys(tmp_path: Path) -> None:
    path = tmp_path / "relations.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d")]
    signature = {"matcher": "test"}
    first = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    second = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    first_candidate = _candidate("a", "b")
    second_candidate = _candidate("c", "d")
    first_match = _match(first_candidate, confidence=0.8)
    second_match = _match(second_candidate, confidence=0.9)
    first.store(first_candidate, [first_match])
    second.store(second_candidate, [second_match])

    await asyncio.gather(asyncio.to_thread(first.flush), asyncio.to_thread(second.flush))

    reloaded = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    assert reloaded.load(first_candidate) == [first_match]
    assert reloaded.load(second_candidate) == [second_match]


def test_relation_cache_same_key_uses_newest_store_not_last_flush(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    timestamps = iter(("2026-08-03T00:00:00+00:00", "2026-08-03T00:00:01+00:00"))
    monkeypatch.setattr(cache_module, "_utc_now", lambda: next(timestamps))
    path = tmp_path / "relations.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    signature = {"matcher": "test"}
    older = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    newer = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    candidate = _candidate("a", "b")
    older_match = _match(candidate, confidence=0.7)
    newer_match = _match(candidate, confidence=0.95)
    older.store(candidate, [older_match])
    newer.store(candidate, [newer_match])

    newer.flush()
    older.flush()

    reloaded = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    assert reloaded.load(candidate) == [newer_match]


def _match(candidate: RelationCandidate, *, confidence: float) -> LLMMatch:
    return LLMMatch(
        source_id=candidate.source_id,
        target_id=candidate.target_id,
        relation_type="can_feed",
        confidence=confidence,
        candidate_id=candidate.key,
        accepted=True,
    )
