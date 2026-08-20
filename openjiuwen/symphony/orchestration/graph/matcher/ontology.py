"""Internal ontology relation matcher with persistent candidate caching."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Protocol

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.orchestration.graph.matcher.cache import (
    CACHE_INDEX_SCHEMA,
    CACHE_RECORD_SCHEMA,
    RelationCacheStats,
    RelationMatchCache,
    chunked,
    dedupe_diagnostics,
    diagnostics_by_candidate,
    matcher_window_size,
    matches_by_candidate,
    sanitize_matcher_metadata,
)
from openjiuwen.symphony.orchestration.graph.matcher.consensus import consensus_matches
from openjiuwen.symphony.orchestration.graph.matcher.protocol import (
    DEFAULT_THRESHOLDS,
    SYSTEM_PROMPT,
    build_match_request,
    parse_match_response,
)
from openjiuwen.symphony.orchestration.graph.models import GraphDiagnostic, LLMMatch, RelationCandidate, SkillRegistry
from openjiuwen.symphony.orchestration.model import (
    ModelResponseObserver,
    invoke_json,
    model_identity,
    model_usage_context,
    thinking_disabled_request_overrides,
)
from openjiuwen.symphony.shared.fingerprint import FingerprintLike

MATCHER_VERSION = "Symphony-ontology-matcher-v2"
DEFAULT_PROMPT_VERSION = "Orchestration-graph-match-v2"
MATCH_SCHEMA_VERSION = "Symphony-ontology-match-schema-v1"


class MatchProgress(Protocol):
    """Progress callback for LLM candidate matching."""

    def __call__(
        self,
        event: str,
        current: int,
        total: int,
        details: Dict[str, Any],
    ) -> None:
        """Report one relation-matching progress event."""
        raise NotImplementedError


class OntologyMatcher:
    """Resolve ontology relations with the injected LLM and optional persistent cache."""

    def __init__(
        self,
        model: Model,
        *,
        model_response_observer: ModelResponseObserver | None = None,
        fingerprints: Iterable[FingerprintLike],
        cache_path: str | Path | None = None,
        batch_size: int = 12,
        max_workers: int = 1,
        require_consensus: bool = True,
        prompt_version: str | None = None,
        schema_version: str | None = None,
        graph_config: Mapping[str, Any] | None = None,
        thresholds: Optional[Dict[str, float]] = None,
        progress: Optional[MatchProgress] = None,
    ) -> None:
        self.batch_size = batch_size
        self.max_workers = max(1, max_workers)
        self.require_consensus = require_consensus
        self.prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
        self.schema_version = schema_version or MATCH_SCHEMA_VERSION
        self.graph_config = dict(graph_config or {})
        self.thresholds = thresholds if thresholds is not None else DEFAULT_THRESHOLDS
        self.progress = progress
        self.model = model
        self.model_response_observer = model_response_observer
        self.diagnostics: List[GraphDiagnostic] = []
        self.stats = RelationCacheStats()
        self.cache = (
            RelationMatchCache(
                cache_path,
                matcher_signature=self.identity_metadata(),
                fingerprints=fingerprints,
            )
            if cache_path is not None
            else None
        )

    async def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> List[LLMMatch]:
        candidate_list = list(candidates)
        self.diagnostics = []
        self.stats = RelationCacheStats()
        if self.cache is None:
            matches = await self._resolve(
                registry,
                candidate_list,
                total_candidate_count=len(candidate_list),
            )
            self.stats = RelationCacheStats(resolved_count=len(candidate_list))
            return matches

        cached_matches: list[tuple[int, list[LLMMatch]]] = []
        diagnostics_by_index: dict[int, list[GraphDiagnostic]] = {}
        misses: list[tuple[int, RelationCandidate]] = []
        cached_records = await asyncio.to_thread(self.cache.load_many, candidate_list)
        for index, (candidate, cached_record) in enumerate(zip(candidate_list, cached_records)):
            if cached_record is None:
                misses.append((index, candidate))
            else:
                cached, cached_diagnostics = cached_record
                cached_matches.append((index, cached))
                diagnostics_by_index[index] = cached_diagnostics

        resolved_by_index: dict[int, list[LLMMatch]] = {}
        reused_candidate_count = len(cached_matches)
        completed_candidate_count = reused_candidate_count
        for window in chunked(misses, matcher_window_size(self)):
            miss_candidates = [candidate for _, candidate in window]
            resolved = await self._resolve(
                registry,
                miss_candidates,
                completed_candidate_count=completed_candidate_count,
                total_candidate_count=len(candidate_list),
                reused_candidate_count=reused_candidate_count,
            )
            completed_candidate_count += len(miss_candidates)
            resolved_diagnostics = list(self.diagnostics)
            grouped_diagnostics = diagnostics_by_candidate(miss_candidates, resolved_diagnostics)
            resolved_by_candidate = matches_by_candidate(miss_candidates, resolved)
            for index, candidate in window:
                candidate_matches = resolved_by_candidate.get(candidate.key, [])
                candidate_diagnostics = grouped_diagnostics.get(candidate.key, [])
                resolved_by_index[index] = candidate_matches
                diagnostics_by_index[index] = candidate_diagnostics
                self.cache.store(candidate, candidate_matches, candidate_diagnostics)
            await asyncio.to_thread(self.cache.flush)

        combined: list[tuple[int, LLMMatch]] = []
        for index, cached in cached_matches:
            combined.extend((index, match) for match in cached)
        for index, resolved in resolved_by_index.items():
            combined.extend((index, match) for match in resolved)
        combined.sort(key=lambda item: (item[0], item[1].source_id, item[1].target_id))
        self.diagnostics = dedupe_diagnostics(
            diagnostic for index in range(len(candidate_list)) for diagnostic in diagnostics_by_index.get(index, [])
        )
        self.stats = RelationCacheStats(
            reused_count=len(cached_matches),
            resolved_count=len(misses),
            stored_count=len(misses),
        )
        return [match for _index, match in combined]

    async def _resolve(
        self,
        registry: SkillRegistry,
        candidate_list: List[RelationCandidate],
        *,
        completed_candidate_count: int = 0,
        total_candidate_count: int | None = None,
        reused_candidate_count: int = 0,
    ) -> List[LLMMatch]:
        matches: List[LLMMatch] = []
        self.diagnostics = []
        global_total = len(candidate_list) if total_candidate_count is None else total_candidate_count
        total_batches = (len(candidate_list) + self.batch_size - 1) // self.batch_size if candidate_list else 0
        self._emit_progress(
            "matching_start",
            0,
            total_batches,
            {
                "candidate_count": len(candidate_list),
                "batch_size": self.batch_size,
                "max_workers": self.max_workers,
                "consensus_runs": 2 if self.require_consensus else 1,
                "completed_candidate_count": completed_candidate_count,
                "total_candidate_count": global_total,
                "reused_candidate_count": reused_candidate_count,
            },
        )
        batches = []
        for batch_index, start in enumerate(
            range(0, len(candidate_list), self.batch_size),
            start=1,
        ):
            stop = start + self.batch_size
            batches.append((batch_index, candidate_list[start:stop]))
        batch_sizes = {batch_index: len(batch) for batch_index, batch in batches}
        request_semaphore = asyncio.Semaphore(self.max_workers)
        results = await _gather_cancel_on_error(
            *(
                self._match_batch(
                    registry,
                    batch,
                    batch_index,
                    total_batches,
                    request_semaphore=request_semaphore,
                    completed_candidate_count=completed_candidate_count,
                    total_candidate_count=global_total,
                    reused_candidate_count=reused_candidate_count,
                )
                for batch_index, batch in batches
            )
        )

        completed_in_window = 0
        for batch_index, batch_matches, batch_diagnostics in sorted(
            results,
            key=lambda item: item[0],
        ):
            self.diagnostics.extend(batch_diagnostics)
            matches.extend(batch_matches)
            completed_in_window += batch_sizes[batch_index]
            self._emit_progress(
                "batch_done",
                batch_index,
                total_batches,
                {
                    "candidate_count": batch_sizes[batch_index],
                    "match_count": len(batch_matches),
                    "accepted_count": len([match for match in batch_matches if match.accepted]),
                    "diagnostics_count": len(batch_diagnostics),
                    "completed_candidate_count": completed_candidate_count + completed_in_window,
                    "total_candidate_count": global_total,
                    "reused_candidate_count": reused_candidate_count,
                },
            )
        self._emit_progress(
            "matching_done",
            total_batches,
            total_batches,
            {
                "match_count": len(matches),
                "accepted_count": len([match for match in matches if match.accepted]),
                "diagnostics_count": len(self.diagnostics),
                "completed_candidate_count": completed_candidate_count + len(candidate_list),
                "total_candidate_count": global_total,
                "reused_candidate_count": reused_candidate_count,
            },
        )
        return matches

    def identity_metadata(self) -> Dict[str, Any]:
        metadata = {
            **model_identity(self.model),
            "matcher_version": MATCHER_VERSION,
            "prompt_version": self.prompt_version,
            "match_schema_version": self.schema_version,
            "cache_record_schema_version": CACHE_RECORD_SCHEMA,
            "cache_index_schema_version": CACHE_INDEX_SCHEMA,
            "graph_config": self.graph_config,
            "batch_size": self.batch_size,
            "max_workers": self.max_workers,
            "require_consensus": self.require_consensus,
            "consensus_runs": 2 if self.require_consensus else 1,
            "thresholds": dict(self.thresholds),
        }
        return sanitize_matcher_metadata(metadata)

    def manifest_metadata(self) -> Dict[str, Any]:
        metadata = self.identity_metadata()
        metadata["relation_cache"] = {
            "schema_version": CACHE_RECORD_SCHEMA,
            "reused_count": self.stats.reused_count,
            "resolved_count": self.stats.resolved_count,
        }
        return metadata

    async def _match_batch(
        self,
        registry: SkillRegistry,
        batch: List[RelationCandidate],
        batch_index: int,
        total_batches: int,
        *,
        request_semaphore: asyncio.Semaphore,
        completed_candidate_count: int,
        total_candidate_count: int,
        reused_candidate_count: int,
    ) -> tuple[int, List[LLMMatch], List[GraphDiagnostic]]:
        self._emit_progress(
            "batch_start",
            batch_index,
            total_batches,
            {
                "candidate_count": len(batch),
                "candidate_ids": [candidate.key for candidate in batch],
                "consensus_runs": 2 if self.require_consensus else 1,
                "completed_candidate_count": completed_candidate_count,
                "total_candidate_count": total_candidate_count,
                "reused_candidate_count": reused_candidate_count,
            },
        )

        async def request(
            *,
            reverse_skill_order: bool,
        ) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
            async with request_semaphore:
                return await self._request_and_validate_batch(
                    registry,
                    batch,
                    reverse_skill_order=reverse_skill_order,
                )

        if not self.require_consensus:
            first_matches, first_diagnostics = await request(
                reverse_skill_order=False,
            )
            return batch_index, first_matches, first_diagnostics

        first_result, second_result = await _gather_cancel_on_error(
            request(reverse_skill_order=False),
            request(reverse_skill_order=True),
        )
        first_matches, first_diagnostics = first_result
        second_matches, second_diagnostics = second_result
        agreed_matches, consensus_diagnostics = consensus_matches(
            first_matches,
            second_matches,
        )
        return (
            batch_index,
            agreed_matches,
            first_diagnostics + second_diagnostics + consensus_diagnostics,
        )

    async def _request_and_validate_batch(
        self,
        registry: SkillRegistry,
        batch: List[RelationCandidate],
        *,
        reverse_skill_order: bool,
    ) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
        payload = build_match_request(
            registry,
            batch,
            reverse_skill_order=reverse_skill_order,
        )
        operation = "ontology_matching_reverse" if reverse_skill_order else "ontology_matching_forward"
        with model_usage_context("graph_construction", operation):
            content = await invoke_json(
                self.model,
                system_prompt=SYSTEM_PROMPT,
                user_content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                timeout=200,
                error_context="LLM graph matching",
                request_overrides=thinking_disabled_request_overrides(),
                response_observer=self.model_response_observer,
            )
        try:
            raw_payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"LLM graph matching response is not valid JSON. content_prefix={content[:1000]!r}"
            ) from exc
        batch_matches, batch_diagnostics = parse_match_response(
            raw_payload,
            registry,
            batch,
            thresholds=self.thresholds,
        )
        return batch_matches, batch_diagnostics

    def _emit_progress(
        self,
        event: str,
        current: int,
        total: int,
        details: Dict[str, Any],
    ) -> None:
        if self.progress is None:
            return
        self.progress(event, current, total, details)


async def _gather_cancel_on_error(*awaitables: Awaitable[Any]) -> List[Any]:
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
