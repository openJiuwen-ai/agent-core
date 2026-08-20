import asyncio
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

import openjiuwen.symphony.orchestration.graph.matcher.cache as cache_module
import openjiuwen.symphony.orchestration.graph.matcher.ontology as matcher_module
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.symphony import (
    ArtifactSpec,
    CapabilityFingerprint,
    Fingerprint,
    OrchestrationService,
    ParameterSpec,
)
from openjiuwen.symphony.orchestration.graph.matcher.cache import RelationCacheStats, RelationMatchCache
from openjiuwen.symphony.orchestration.graph.matcher.ontology import OntologyMatcher
from openjiuwen.symphony.orchestration.graph.models import (
    LLMMatch,
    RelationCandidate,
    SkillRegistry,
)
from openjiuwen.symphony.shared.fingerprint import coerce_fingerprint


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
    direction = f"{source_id}->{target_id}"
    return RelationCandidate(
        source_id=source_id,
        target_id=target_id,
        relation_hints=["can_feed"],
        candidate_methods=["test"],
        priority="medium",
        evidence={
            "directions": {
                direction: {
                    "source_outputs": [{"name": "output", "type": "text"}],
                    "target_inputs": [{"name": "input", "type": "text"}],
                    "port_mappings": [
                        {
                            "source_output": "output",
                            "source_type": "text",
                            "target_input": "input",
                            "target_type": "text",
                        }
                    ],
                }
            }
        },
    )


def _write_relation_cache_in_process(path: str, source_id: str, target_id: str, start, ready) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d")]
    candidate = _candidate(source_id, target_id)
    cache = RelationMatchCache(path, matcher_signature={"matcher": "process-test"}, fingerprints=fingerprints)
    cache.store(candidate, [_match(candidate, confidence=0.9)])
    ready.put(candidate.key)
    if not start.wait(timeout=10):
        raise RuntimeError("process cache test start barrier timed out")
    cache.flush()


class _CountingLLM:
    def __init__(self, *, model: str = "model-a", endpoint: str = "https://api.example.test/v1") -> None:
        self.model_config = SimpleNamespace(
            model_name=model,
            temperature=0,
            top_p=1,
            max_tokens=None,
            stop=None,
        )
        self.model_client_config = SimpleNamespace(
            client_provider="openai",
            api_base=endpoint,
            api_key="top-secret-key",
        )
        self.calls: list[dict] = []

    async def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
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
                        }
                        for item in payload["candidates"]
                    ]
                }
            )
        )


def _matcher(
    llm: _CountingLLM,
    path: Path,
    fingerprints: list[Fingerprint],
    *,
    prompt_version: str | None = None,
    schema_version: str | None = None,
) -> OntologyMatcher:
    return OntologyMatcher(
        llm,
        fingerprints=fingerprints,
        cache_path=path,
        require_consensus=False,
        prompt_version=prompt_version,
        schema_version=schema_version,
    )


def _service_inventory() -> list[Fingerprint]:
    return [
        Fingerprint(
            type="skill",
            id="source",
            name="source",
            description="source",
            version="1.0.0",
            outputs=[ArtifactSpec(name="text", type="text")],
        ),
        Fingerprint(
            type="skill",
            id="target",
            name="target",
            description="target",
            version="1.0.0",
            inputs=[ParameterSpec(name="text", type="text")],
        ),
    ]


def _real_model(model_name: str, calls: list[dict], **request_options) -> Model:
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_base="https://private.example.test/v1",
            api_key="adapter-secret-key",
            custom_headers={"AuthorizationHeader": "routing-secret", "X-Safe-Route": "blue"},
        ),
        model_config=ModelRequestConfig(model=model_name, temperature=0.2, **request_options),
    )

    async def invoke(messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        payload = json.loads(messages[-1]["content"])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "matches": [
                        {"id": item["id"], "direction": "forward", "confidence": 0.9, "accepted": True}
                        for item in payload["candidates"]
                    ]
                }
            )
        )

    model.invoke = invoke
    return model


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
async def test_internal_matcher_reuses_cache_and_preserves_order(tmp_path: Path) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c")]
    candidates = [_candidate("a", "c"), _candidate("a", "b")]
    path = tmp_path / "relation_matches.json"
    first_llm = _CountingLLM()
    first = _matcher(first_llm, path, fingerprints)
    first_matches = await first.match(SkillRegistry(skills={item.id: item for item in fingerprints}), candidates)
    second_llm = _CountingLLM()
    second = _matcher(second_llm, path, fingerprints)
    second_matches = await second.match(SkillRegistry(skills={item.id: item for item in fingerprints}), candidates)

    assert [item.candidate_id for item in first_matches] == [item.key for item in candidates]
    assert [item.candidate_id for item in second_matches] == [item.key for item in candidates]
    assert first.stats == RelationCacheStats(reused_count=0, resolved_count=2, stored_count=2)
    assert second.stats == RelationCacheStats(reused_count=2, resolved_count=0, stored_count=0)
    assert len(first_llm.calls) == 1
    assert second_llm.calls == []
    assert second.manifest_metadata()["relation_cache"]["reused_count"] == 2


@pytest.mark.asyncio
async def test_internal_matcher_reports_global_candidate_progress_across_cache_windows(tmp_path: Path) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d", "e", "f")]
    candidates = [
        _candidate("a", "b"),
        _candidate("a", "c"),
        _candidate("a", "d"),
        _candidate("a", "e"),
        _candidate("a", "f"),
    ]
    registry = SkillRegistry(skills={item.id: item for item in fingerprints})
    path = tmp_path / "relation_matches.json"
    await OntologyMatcher(
        _CountingLLM(),
        fingerprints=fingerprints,
        cache_path=path,
        batch_size=1,
        max_workers=2,
        require_consensus=False,
    ).match(registry, candidates[:1])
    progress: list[tuple[str, dict]] = []
    matcher = OntologyMatcher(
        _CountingLLM(),
        fingerprints=fingerprints,
        cache_path=path,
        batch_size=1,
        max_workers=2,
        require_consensus=False,
        progress=lambda event, _current, _total, details: progress.append((event, details)),
    )

    await matcher.match(registry, candidates)

    completed = [
        details["completed_candidate_count"]
        for event, details in progress
        if event in {"matching_start", "batch_done", "matching_done"}
    ]
    assert completed == [1, 2, 3, 3, 3, 4, 5, 5]
    assert all(details["total_candidate_count"] == 5 for _event, details in progress)
    assert all(details["reused_candidate_count"] == 1 for _event, details in progress)


@pytest.mark.asyncio
async def test_internal_matcher_limits_concurrent_llm_requests() -> None:
    class _ConcurrentLLM(_CountingLLM):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def invoke(self, messages, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return await super().invoke(messages, **kwargs)
            finally:
                self.active -= 1

    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d", "e", "f")]
    candidates = [_candidate("a", "b"), _candidate("c", "d"), _candidate("e", "f")]
    llm = _ConcurrentLLM()
    matcher = OntologyMatcher(
        llm,
        fingerprints=fingerprints,
        batch_size=1,
        max_workers=2,
        require_consensus=False,
    )

    matches = await matcher.match(SkillRegistry(skills={item.id: item for item in fingerprints}), candidates)

    assert llm.max_active == 2
    assert [item.candidate_id for item in matches] == [item.key for item in candidates]


@pytest.mark.asyncio
async def test_internal_matcher_cache_lock_does_not_block_cancellation(tmp_path: Path) -> None:
    fingerprints = [_fingerprint("a"), _fingerprint("b")]
    candidate = _candidate("a", "b")
    registry = SkillRegistry(skills={item.id: item for item in fingerprints})
    cache_path = tmp_path / "relation_matches.json"
    matcher = _matcher(_CountingLLM(), cache_path, fingerprints)
    process_lock = FileLock(str(cache_path.resolve()) + ".lock")
    process_lock.acquire()
    task = asyncio.create_task(matcher.match(registry, [candidate]))
    try:
        await asyncio.sleep(0.05)
        assert not task.done()
        started = asyncio.get_running_loop().time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)
        assert asyncio.get_running_loop().time() - started < 0.5
    finally:
        process_lock.release()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("model", "model-b"),
        ("prompt", "Orchestration-graph-match-v-next"),
        ("schema", "Symphony-ontology-match-schema-v-next"),
    ],
)
async def test_matcher_identity_changes_invalidate_cache(tmp_path: Path, change: str, value: str) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidate = _candidate("a", "b")
    path = tmp_path / "relation_matches.json"
    await _matcher(_CountingLLM(), path, fingerprints).match(
        SkillRegistry(skills={item.id: item for item in fingerprints}), [candidate]
    )
    llm = _CountingLLM(model=value if change == "model" else "model-a")
    changed = _matcher(
        llm,
        path,
        fingerprints,
        prompt_version=value if change == "prompt" else None,
        schema_version=value if change == "schema" else None,
    )

    await changed.match(SkillRegistry(skills={item.id: item for item in fingerprints}), [candidate])

    assert len(llm.calls) == 1
    assert changed.stats.resolved_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_name", ["CACHE_RECORD_SCHEMA", "CACHE_INDEX_SCHEMA"])
async def test_cache_schema_version_change_invalidates_relation_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schema_name: str,
) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidate = _candidate("a", "b")
    path = tmp_path / "relation_matches.json"
    await _matcher(_CountingLLM(), path, fingerprints).match(
        SkillRegistry(skills={item.id: item for item in fingerprints}), [candidate]
    )
    monkeypatch.setattr(matcher_module, schema_name, f"{getattr(matcher_module, schema_name)}-next")
    llm = _CountingLLM()
    changed = _matcher(llm, path, fingerprints)

    await changed.match(SkillRegistry(skills={item.id: item for item in fingerprints}), [candidate])

    assert len(llm.calls) == 1
    assert changed.stats.reused_count == 0


@pytest.mark.asyncio
async def test_cache_invalidates_only_relation_with_changed_capability(tmp_path: Path) -> None:
    path = tmp_path / "relation_matches.json"
    candidates = [_candidate("a", "b"), _candidate("a", "c")]
    first = [_fingerprint(item) for item in ("a", "b", "c")]
    await _matcher(_CountingLLM(), path, first).match(
        SkillRegistry(skills={item.id: item for item in first}), candidates
    )
    changed = [_fingerprint("a"), _fingerprint("b", description="changed"), _fingerprint("c")]
    llm = _CountingLLM()
    matcher = _matcher(llm, path, changed)

    await matcher.match(SkillRegistry(skills={item.id: item for item in changed}), candidates)

    assert matcher.stats == RelationCacheStats(reused_count=1, resolved_count=1, stored_count=1)
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_endpoint_is_hashed_and_secrets_never_reach_cache(tmp_path: Path) -> None:
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidate = _candidate("a", "b")
    path = tmp_path / "relation_matches.json"
    matcher = _matcher(_CountingLLM(endpoint=" HTTPS://API.EXAMPLE.TEST/v1/ "), path, fingerprints)
    await matcher.match(SkillRegistry(skills={item.id: item for item in fingerprints}), [candidate])

    serialized = path.read_text(encoding="utf-8") + json.dumps(matcher.identity_metadata(), sort_keys=True)
    assert "api.example.test" not in serialized.lower()
    assert "top-secret-key" not in serialized
    assert matcher.identity_metadata()["api_base_sha256"]


@pytest.mark.asyncio
async def test_service_reuses_internal_relation_cache_and_force_bypasses_it(tmp_path: Path) -> None:
    first_llm = _CountingLLM()
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=first_llm,
        source_snapshot={"revision": 1},
        graph_config={"require_consensus": False},
    )
    await first.build()
    assert len(first_llm.calls) == 1
    assert (tmp_path / "cache" / "relation_matches.json").is_file()
    reader = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=None,
        source_snapshot={"revision": 1},
        graph_config={"require_consensus": False},
    )
    assert reader.status().fresh is True
    assert reader.read()["nodes"]

    reused_llm = _CountingLLM()
    reused = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=reused_llm,
        source_snapshot={"revision": 2},
        graph_config={"require_consensus": False},
    )
    await reused.build()
    assert reused_llm.calls == []
    assert reused.read()["config"]["llm"]["relation_cache"]["reused_count"] == 1

    await reused.build(force=True)
    assert len(reused_llm.calls) == 1
    assert reused.read()["config"]["llm"]["relation_cache"] == {
        "schema_version": "Symphony-relation-match-cache-v1",
        "reused_count": 0,
        "resolved_count": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_change", ["model", "graph_config"])
async def test_service_build_identity_invalidates_artifact_and_cache(
    tmp_path: Path,
    identity_change: str,
) -> None:
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_CountingLLM(model="model-a"),
        graph_config={"batch_size": 1, "require_consensus": False},
    )
    built = await first.build()
    changed_llm = _CountingLLM(model="model-b" if identity_change == "model" else "model-a")
    changed = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=changed_llm,
        graph_config={
            "batch_size": 2 if identity_change == "graph_config" else 1,
            "require_consensus": False,
        },
    )

    assert changed.status().fresh is False
    rebuilt = await changed.build()

    assert rebuilt.version != built.version
    assert len(changed_llm.calls) == 1
    assert changed.read()["config"]["llm"]["relation_cache"]["resolved_count"] == 1


@pytest.mark.asyncio
async def test_real_openjiuwen_adapter_model_switch_invalidates_and_rebuilds(tmp_path: Path) -> None:
    first_calls: list[dict] = []
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_real_model("model-a", first_calls),
        graph_config={"require_consensus": False},
    )
    first_build = await first.build()
    assert len(first_calls) == 1
    assert first.read()["config"]["llm"]["model"] == "model-a"
    assert first.read()["config"]["llm"]["backend"] == "OpenAI"
    assert first.read()["config"]["llm"]["temperature"] == 0.2
    assert first.read()["config"]["llm"]["api_base_sha256"]

    second_calls: list[dict] = []
    second = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_real_model("model-b", second_calls),
        graph_config={"require_consensus": False},
    )

    assert second.status().fresh is False
    second_build = await second.build()
    serialized = json.dumps(second.read(), sort_keys=True)

    assert second_build.version != first_build.version
    assert len(second_calls) == 1
    assert second.read()["config"]["llm"]["model"] == "model-b"
    assert "adapter-secret-key" not in serialized
    assert "routing-secret" not in serialized
    assert "private.example.test" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_field", "first_value", "second_value"),
    [
        ("reasoning_token_budget", 1024, 2048),
        ("tokenizer_name", "o200k_base", "cl100k_base"),
    ],
)
async def test_real_model_token_request_config_invalidates_without_leaking_credentials(
    tmp_path: Path,
    request_field: str,
    first_value: object,
    second_value: object,
) -> None:
    first_calls: list[dict] = []
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_real_model(
            "model-a",
            first_calls,
            **{
                request_field: first_value,
                "access_token": "request-access-secret",
                "refresh_token": "request-refresh-secret",
                "bearer_token": "request-bearer-secret",
                "auth_token": "request-auth-secret",
                "id_token": "request-id-secret",
                "session_token": "request-session-secret",
                "api_token": "request-api-secret",
                "vendorToken": "request-vendor-secret",
            },
        ),
        graph_config={"require_consensus": False},
    )
    first_build = await first.build()
    first_request_signature = first.read()["config"]["llm"]["request_config_sha256"]
    second_calls: list[dict] = []
    second = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_real_model(
            "model-a",
            second_calls,
            **{
                request_field: second_value,
                "access_token": "request-access-secret",
                "refresh_token": "request-refresh-secret",
                "bearer_token": "request-bearer-secret",
                "auth_token": "request-auth-secret",
                "id_token": "request-id-secret",
                "session_token": "request-session-secret",
                "api_token": "request-api-secret",
                "vendorToken": "request-vendor-secret",
            },
        ),
        graph_config={"require_consensus": False},
    )

    assert second.status().fresh is False
    second_build = await second.build()

    assert second_build.version != first_build.version
    assert len(second_calls) == 1
    assert second.read()["config"]["llm"]["request_config_sha256"] != first_request_signature
    serialized = json.dumps(second.read(), sort_keys=True) + (tmp_path / "cache" / "relation_matches.json").read_text(
        encoding="utf-8"
    )
    for credential in (
        "request-access-secret",
        "request-refresh-secret",
        "request-bearer-secret",
        "request-auth-secret",
        "request-id-secret",
        "request-session-secret",
        "request-api-secret",
        "request-vendor-secret",
    ):
        assert credential not in serialized


@pytest.mark.asyncio
async def test_candidate_graph_config_and_both_cache_schemas_invalidate_relation_cache(tmp_path: Path) -> None:
    first_llm = _CountingLLM()
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=first_llm,
        graph_config={"require_consensus": False, "max_port_mappings_per_candidate": 12},
    )
    await first.build()
    second_llm = _CountingLLM()
    second = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=second_llm,
        graph_config={"require_consensus": False, "max_port_mappings_per_candidate": 7},
    )

    await second.build()
    identity = second.read()["source_snapshot"]["symphony_graph_build"]["matcher"]

    assert len(second_llm.calls) == 1
    assert identity["cache_record_schema_version"] == "Symphony-relation-match-cache-v1"
    assert identity["cache_index_schema_version"] == "Symphony-relation-match-cache-index-v1"
    assert identity["graph_config"] == {
        "batch_size": 12,
        "max_workers": 1,
        "require_consensus": False,
        "max_candidates_per_skill_relation": 32,
        "max_port_mappings_per_candidate": 7,
        "max_exact_io_pair_fanout": 64,
    }


@pytest.mark.asyncio
async def test_cached_rebuild_restores_batch_diagnostics_without_duplicates(tmp_path: Path) -> None:
    inventory = [
        Fingerprint(
            type="skill",
            id="source",
            name="source",
            description="source",
            version="1.0.0",
            outputs=[ArtifactSpec(name="text", type="text")],
        ),
        *[
            Fingerprint(
                type="skill",
                id=f"target-{index}",
                name=f"target-{index}",
                description=f"target-{index}",
                version="1.0.0",
                inputs=[ParameterSpec(name="text", type="text")],
            )
            for index in range(2)
        ],
    ]

    class _DiagnosticLLM(_CountingLLM):
        async def invoke(self, messages, **kwargs):
            self.calls.append({"messages": messages, "kwargs": kwargs})
            payload = json.loads(messages[-1]["content"])
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "matches": [
                            *[
                                {"id": item["id"], "direction": "forward", "confidence": 0.9, "accepted": True}
                                for item in payload["candidates"]
                            ],
                            {"id": "unknown", "direction": "forward", "confidence": 0.9, "accepted": True},
                        ]
                    }
                )
            )

    first_llm = _DiagnosticLLM()
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=inventory,
        model=first_llm,
        source_snapshot={"revision": 1},
        graph_config={"require_consensus": False},
    )
    await first.build()
    first_diagnostics = first.read()["diagnostics"]
    second_llm = _DiagnosticLLM()
    second = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=inventory,
        model=second_llm,
        source_snapshot={"revision": 2},
        graph_config={"require_consensus": False},
    )

    await second.build()

    assert second_llm.calls == []
    assert first_diagnostics == second.read()["diagnostics"]
    assert [item["code"] for item in first_diagnostics].count("unknown_candidate_id") == 1


@pytest.mark.asyncio
async def test_prompt_version_change_invalidates_artifact_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_CountingLLM(),
        graph_config={"require_consensus": False},
    )
    built = await first.build()
    monkeypatch.setattr(
        "openjiuwen.symphony.orchestration.graph.matcher.ontology.DEFAULT_PROMPT_VERSION",
        "Orchestration-graph-match-v-next",
    )
    changed_llm = _CountingLLM()
    changed = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=changed_llm,
        graph_config={"require_consensus": False},
    )

    assert changed.status().fresh is False
    rebuilt = await changed.build()

    assert rebuilt.version != built.version
    assert len(changed_llm.calls) == 1


@pytest.mark.asyncio
async def test_source_snapshot_is_wrapped_without_leaking_secrets(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_CountingLLM(),
        source_snapshot={
            "revision": "one",
            "api_key": "source-secret",
            "base_url": "https://private.example.test/v1",
            "apiKey": "camel-secret",
            "access-token": "punctuated-token",
            "subscription_key": "subscription-secret",
            "encryption_key": "encryption-secret",
            "consumer_key": "consumer-secret",
            "vendorKey": "vendor-source-value",
            "nested": [
                {
                    "accessToken": "list-token",
                    "AuthorizationHeader": "list-authorization",
                    "endpointUrl": "https://nested.private.example.test/v1",
                }
            ],
        },
        graph_config={"require_consensus": False, "apiKey": "graph-config-secret"},
    )
    await service.build()

    serialized = "\n".join(
        (
            json.dumps(service.read(), sort_keys=True),
            json.dumps(service.read()["source_snapshot"], sort_keys=True),
            (tmp_path / "cache" / "relation_matches.json").read_text(encoding="utf-8"),
        )
    )
    for forbidden in (
        "source-secret",
        "camel-secret",
        "list-token",
        "punctuated-token",
        "list-authorization",
        "AuthorizationHeader",
        "accessToken",
        "apiKey",
        "graph-config-secret",
        "subscription-secret",
        "encryption-secret",
        "consumer-secret",
        "vendor-source-value",
        "private.example.test",
        "nested.private.example.test",
    ):
        assert forbidden not in serialized
    assert service.read()["source_snapshot"]["revision"] == "one"
    assert service.read()["source_snapshot"]["vendorKey_sha256"]
    assert service.read()["source_snapshot"]["symphony_graph_build"]["matcher"]["prompt_version"]


@pytest.mark.asyncio
async def test_unknown_source_key_value_changes_build_identity_without_leaking(tmp_path: Path) -> None:
    first = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_CountingLLM(),
        source_snapshot={"revision": "same", "vendorKey": "vendor-one"},
        graph_config={"require_consensus": False},
    )
    first_build = await first.build()
    first_hash = first.read()["source_snapshot"]["vendorKey_sha256"]
    second = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_service_inventory,
        model=_CountingLLM(),
        source_snapshot={"revision": "same", "vendorKey": "vendor-two"},
        graph_config={"require_consensus": False},
    )

    assert second.status().fresh is False
    second_build = await second.build()
    serialized = json.dumps(second.read(), sort_keys=True) + (tmp_path / "cache" / "relation_matches.json").read_text(
        encoding="utf-8"
    )

    assert second_build.version != first_build.version
    assert second.read()["source_snapshot"]["vendorKey_sha256"] != first_hash
    assert "vendor-one" not in serialized
    assert "vendor-two" not in serialized


def test_relation_match_cache_flush_is_readable_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "relation_matches.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    candidate = _candidate("a", "b")
    match = _match(candidate, confidence=0.9)
    cache = RelationMatchCache(path, matcher_signature={"matcher": "test"}, fingerprints=fingerprints)
    cache.store(candidate, [match])
    cache.flush()
    content = path.read_bytes()
    cache.flush()

    reloaded = RelationMatchCache(path, matcher_signature={"matcher": "test"}, fingerprints=fingerprints)
    assert reloaded.load(candidate) == ([match], [])
    assert path.read_bytes() == content
    assert json.loads(content)["schema_version"] == "Symphony-relation-match-cache-index-v1"


@pytest.mark.asyncio
async def test_relation_cache_concurrent_instances_merge_different_keys(tmp_path: Path) -> None:
    path = tmp_path / "relation_matches.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d")]
    signature = {"matcher": "test"}
    first = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    second = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    first_candidate = _candidate("a", "b")
    second_candidate = _candidate("c", "d")
    first.store(first_candidate, [_match(first_candidate, confidence=0.8)])
    second.store(second_candidate, [_match(second_candidate, confidence=0.9)])

    await asyncio.gather(asyncio.to_thread(first.flush), asyncio.to_thread(second.flush))

    reloaded = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    assert reloaded.load(first_candidate) == ([_match(first_candidate, confidence=0.8)], [])
    assert reloaded.load(second_candidate) == ([_match(second_candidate, confidence=0.9)], [])


def test_relation_cache_merges_different_keys_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Queue()
    path = tmp_path / "relation_matches.json"
    processes = [
        context.Process(target=_write_relation_cache_in_process, args=(str(path), *pair, start, ready))
        for pair in (("a", "b"), ("c", "d"))
    ]
    for process in processes:
        process.start()
    assert {ready.get(timeout=10), ready.get(timeout=10)} == {"a->b", "c->d"}
    start.set()
    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        assert process.exitcode == 0

    fingerprints = [_fingerprint(item) for item in ("a", "b", "c", "d")]
    cache = RelationMatchCache(path, matcher_signature={"matcher": "process-test"}, fingerprints=fingerprints)
    assert cache.load(_candidate("a", "b")) is not None
    assert cache.load(_candidate("c", "d")) is not None


def test_relation_cache_same_key_uses_newest_store_not_last_flush(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    timestamps = iter(("2026-08-03T00:00:00+00:00", "2026-08-03T00:00:01+00:00"))
    monkeypatch.setattr(cache_module, "_utc_now", lambda: next(timestamps))
    path = tmp_path / "relation_matches.json"
    fingerprints = [_fingerprint(item) for item in ("a", "b")]
    signature = {"matcher": "test"}
    older = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    newer = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    candidate = _candidate("a", "b")
    older.store(candidate, [_match(candidate, confidence=0.7)])
    newer.store(candidate, [_match(candidate, confidence=0.95)])

    newer.flush()
    older.flush()

    reloaded = RelationMatchCache(path, matcher_signature=signature, fingerprints=fingerprints)
    assert reloaded.load(candidate) == ([_match(candidate, confidence=0.95)], [])


def _match(candidate: RelationCandidate, *, confidence: float) -> LLMMatch:
    return LLMMatch(
        source_id=candidate.source_id,
        target_id=candidate.target_id,
        relation_type="can_feed",
        confidence=confidence,
        candidate_id=candidate.key,
        accepted=True,
    )
