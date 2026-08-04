"""Offline graph construction from an explicitly supplied capability inventory."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from openjiuwen.symphony.orchestration.graph.builders import GraphLookupBuilder, SkillGraphBuilder
from openjiuwen.symphony.orchestration.graph.candidates import CandidateGenerator
from openjiuwen.symphony.orchestration.graph.models import (
    BuildManifest,
    GraphBuildResult,
    GraphDiagnostic,
    LLMMatch,
)
from openjiuwen.symphony.orchestration.graph.protocols import OntologyMatcher
from openjiuwen.symphony.orchestration.graph.registry import SkillRegistryBuilder
from openjiuwen.symphony.shared.fingerprint import CapabilityFingerprint, Fingerprint, coerce_fingerprint

GraphProgress = Callable[..., None]


class GraphBuilder:
    def __init__(
        self,
        *,
        matcher: OntologyMatcher,
        registry_builder: SkillRegistryBuilder | None = None,
        candidate_generator: CandidateGenerator | None = None,
        graph_builder: SkillGraphBuilder | None = None,
        lookup_builder: GraphLookupBuilder | None = None,
    ) -> None:
        self.matcher = matcher
        self.registry_builder = registry_builder or SkillRegistryBuilder()
        self.candidate_generator = candidate_generator or CandidateGenerator()
        self.graph_builder = graph_builder or SkillGraphBuilder()
        self.lookup_builder = lookup_builder or GraphLookupBuilder()

    async def __call__(
        self,
        fingerprints: Iterable[Fingerprint | CapabilityFingerprint | dict[str, Any]],
        *,
        progress: GraphProgress | None = None,
    ) -> GraphBuildResult:
        return await self.build(fingerprints, progress=progress)

    async def build(
        self,
        fingerprints: Iterable[Fingerprint | CapabilityFingerprint | dict[str, Any]],
        *,
        progress: GraphProgress | None = None,
    ) -> GraphBuildResult:
        normalized = [coerce_fingerprint(item) for item in fingerprints]
        _emit(progress, "graph.registry.start", fingerprint_count=len(normalized))
        registry = self.registry_builder.register(normalized)
        diagnostics: list[GraphDiagnostic] = list(registry.diagnostics)
        _emit(
            progress,
            "graph.registry.done",
            capability_count=len(registry.skills),
            diagnostics_count=len(registry.diagnostics),
        )
        _emit(progress, "graph.candidates.start", capability_count=len(registry.skills))
        candidates = self.candidate_generator.generate(registry)
        _emit(progress, "graph.candidates.done", candidate_count=len(candidates))
        _emit(progress, "graph.resolve.start", candidate_count=len(candidates))
        matches = await self.matcher.match(registry, candidates)
        relation_diagnostics = self._relation_diagnostics(matches)
        diagnostics.extend(relation_diagnostics)
        _emit(
            progress,
            "graph.resolve.done",
            candidate_count=len(candidates),
            match_count=len(matches),
            accepted_match_count=sum(1 for item in matches if item.accepted),
            diagnostics_count=len(relation_diagnostics),
        )
        _emit(progress, "graph.materialize.start", match_count=len(matches))
        graph = self.graph_builder.build(registry, matches)
        _emit(progress, "graph.materialize.done", node_count=len(graph.nodes), edge_count=len(graph.edges))
        _emit(progress, "graph.lookup.start", node_count=len(graph.nodes), edge_count=len(graph.edges))
        lookup = self.lookup_builder.build(registry, graph)
        _emit(progress, "graph.lookup.done")
        manifest_metadata = getattr(self.matcher, "manifest_metadata", None)
        metadata = manifest_metadata() if callable(manifest_metadata) else {"matcher": type(self.matcher).__name__}
        thresholds = dict(getattr(self.matcher, "thresholds", {"can_feed": 0.7}))
        return GraphBuildResult(
            manifest=BuildManifest(
                thresholds=thresholds,
                candidate_generation=_candidate_generator_metadata(self.candidate_generator),
                llm=metadata,
            ),
            skills=registry.ordered_skills(),
            candidates=candidates,
            llm_matches=matches,
            graph=graph,
            lookup=lookup,
            diagnostics=diagnostics,
        )

    def _relation_diagnostics(self, matches: list[LLMMatch]) -> list[GraphDiagnostic]:
        matcher_diagnostics = list(getattr(self.matcher, "diagnostics", []))
        if matcher_diagnostics:
            return matcher_diagnostics
        diagnostics: list[GraphDiagnostic] = []
        for match in matches:
            for message in match.diagnostics:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="llm_match",
                        severity="warning",
                        code="match_diagnostic",
                        message=message,
                        skill_id=match.source_id,
                        details={"match": match.to_dict()},
                    )
                )
        return diagnostics


def _emit(progress: GraphProgress | None, stage: str, **details: Any) -> None:
    if progress is not None:
        progress(stage, **details)


def _candidate_generator_metadata(candidate_generator: CandidateGenerator) -> dict[str, int]:
    return {
        "max_candidates_per_skill_relation": int(candidate_generator.max_candidates_per_skill_relation),
        "max_port_mappings_per_candidate": int(candidate_generator.max_port_mappings_per_candidate),
        "max_exact_io_pair_fanout": int(candidate_generator.max_exact_io_pair_fanout),
    }
