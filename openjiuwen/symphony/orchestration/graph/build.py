"""Offline capability graph construction pipeline."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Protocol, Set

from openjiuwen.symphony.orchestration.graph.candidates import CandidateGenerator
from openjiuwen.symphony.orchestration.graph.lexicon import (
    DEFAULT_GRAPH_STOP_TERMS,
    ArtifactLexicon,
)
from openjiuwen.symphony.orchestration.graph.models import (
    BuildManifest,
    GraphConstructionResult,
    GraphDiagnostic,
    GraphEdge,
    GraphLookup,
    GraphNode,
    LLMMatch,
    RelationCandidate,
    SkillGraph,
    SkillRegistry,
)
from openjiuwen.symphony.shared.fingerprint import (
    CapabilityFingerprint,
    Fingerprint,
    FingerprintLike,
    coerce_fingerprint,
)

GraphProgress = Callable[..., None]

_IO_NAMES_EXCLUDED_FROM_EXACT_LOOKUP = frozenset({"dependencies", "path"})
_GRAPH_TERM_LEXICON = ArtifactLexicon.create(
    stop_terms=DEFAULT_GRAPH_STOP_TERMS,
    min_token_length=3,
)


class _RelationResolver(Protocol):
    thresholds: dict[str, float]
    diagnostics: list[GraphDiagnostic]

    async def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> list[LLMMatch]:
        """Resolve relation candidates for one graph construction run."""
        raise NotImplementedError

    def manifest_metadata(self) -> dict[str, Any]:
        """Return safe resolver metadata for the graph manifest."""
        raise NotImplementedError


class GraphBuildPipeline:
    """Run the complete internal graph construction pipeline."""

    def __init__(
        self,
        *,
        resolver: _RelationResolver,
        candidate_generator: CandidateGenerator | None = None,
    ) -> None:
        self.resolver = resolver
        self.candidate_generator = candidate_generator or CandidateGenerator()

    async def __call__(
        self,
        fingerprints: Iterable[Fingerprint | CapabilityFingerprint | dict[str, Any]],
        *,
        progress: GraphProgress | None = None,
    ) -> GraphConstructionResult:
        return await self.build(fingerprints, progress=progress)

    async def build(
        self,
        fingerprints: Iterable[Fingerprint | CapabilityFingerprint | dict[str, Any]],
        *,
        progress: GraphProgress | None = None,
    ) -> GraphConstructionResult:
        normalized = [coerce_fingerprint(item) for item in fingerprints]
        _emit(progress, "graph.registry.start", fingerprint_count=len(normalized))
        registry = _build_registry(normalized)
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
        matches = await self.resolver.match(registry, candidates)
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
        graph = _materialize_graph(registry, matches)
        _emit(
            progress,
            "graph.materialize.done",
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
        )
        _emit(progress, "graph.lookup.start", node_count=len(graph.nodes), edge_count=len(graph.edges))
        lookup = _build_lookup(registry, graph)
        _emit(progress, "graph.lookup.done")
        manifest_metadata = getattr(self.resolver, "manifest_metadata", None)
        metadata = manifest_metadata() if callable(manifest_metadata) else {"resolver": type(self.resolver).__name__}
        thresholds = dict(getattr(self.resolver, "thresholds", {"can_feed": 0.7}))
        return GraphConstructionResult(
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
        resolver_diagnostics = list(getattr(self.resolver, "diagnostics", []))
        if resolver_diagnostics:
            return resolver_diagnostics
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


def _build_registry(fingerprints: Iterable[FingerprintLike]) -> SkillRegistry:
    skills: dict[str, FingerprintLike] = {}
    diagnostics: list[GraphDiagnostic] = []
    for fingerprint in sorted(fingerprints, key=lambda item: item.id):
        if fingerprint.type not in {"skill", "subagent", "agent"}:
            diagnostics.append(
                GraphDiagnostic(
                    stage="registry",
                    severity="error",
                    code="unsupported_fingerprint_type",
                    message="Capability Graph accepts skill, subagent, or agent fingerprints.",
                    details={"type": fingerprint.type, "id": fingerprint.id},
                )
            )
            continue
        if not fingerprint.id:
            diagnostics.append(
                GraphDiagnostic(
                    stage="registry",
                    severity="error",
                    code="missing_skill_id",
                    message="Skill fingerprint is missing an id.",
                    details={"skill": fingerprint.to_dict()},
                )
            )
            continue
        if fingerprint.id in skills:
            diagnostics.append(
                GraphDiagnostic(
                    stage="registry",
                    severity="error",
                    code="duplicate_skill_id",
                    message=f"Duplicate Skill id: {fingerprint.id}",
                    skill_id=fingerprint.id,
                )
            )
            continue
        if not fingerprint.outputs:
            diagnostics.append(
                GraphDiagnostic(
                    stage="registry",
                    severity="warning",
                    code="missing_outputs",
                    message="Skill has no declared outputs.",
                    skill_id=fingerprint.id,
                )
            )
        skills[fingerprint.id] = fingerprint
    return SkillRegistry(skills=skills, diagnostics=diagnostics)


def _materialize_graph(registry: SkillRegistry, llm_matches: Iterable[LLMMatch]) -> SkillGraph:
    nodes: Dict[str, GraphNode] = {}
    edges: Dict[str, GraphEdge] = {}
    for skill in registry.ordered_skills():
        nodes.setdefault(
            f"capability:{skill.id}",
            GraphNode(
                id=f"capability:{skill.id}",
                type="capability",
                label=skill.name or skill.id,
                properties={
                    "capability_id": skill.id,
                    "capability_type": skill.type,
                    "name": skill.name,
                    "description": skill.description,
                    "version": skill.version,
                    "inputs": [item.to_dict() for item in skill.inputs],
                    "outputs": [item.to_dict() for item in skill.outputs],
                },
            ),
        )
    for match in llm_matches:
        if not match.accepted or match.relation_type != "can_feed":
            continue
        edge = GraphEdge(
            source=f"capability:{match.source_id}",
            target=f"capability:{match.target_id}",
            type=match.relation_type,
            confidence=match.confidence,
            method=match.method,
            evidence={
                "candidate_id": match.candidate_id,
                "reasons": match.reasons,
                "supporting_fields": match.supporting_fields,
            },
        )
        edges.setdefault(edge.key, edge)
    return SkillGraph(
        nodes=sorted(nodes.values(), key=lambda node: node.id),
        edges=sorted(edges.values(), key=lambda edge: edge.key),
    )


def _build_lookup(registry: SkillRegistry, graph: SkillGraph) -> GraphLookup:
    lexicon = ArtifactLexicon.create(
        stop_terms=DEFAULT_GRAPH_STOP_TERMS,
        min_token_length=3,
        generic_io_names=_IO_NAMES_EXCLUDED_FROM_EXACT_LOOKUP,
    )
    by_output: Dict[str, Set[str]] = defaultdict(set)
    by_input: Dict[str, Set[str]] = defaultdict(set)
    by_data_type: Dict[str, Set[str]] = defaultdict(set)
    by_text_term: Dict[str, Set[str]] = defaultdict(set)
    for skill in registry.ordered_skills():
        for output in skill.outputs:
            if not lexicon.is_generic_io_name(output.name):
                by_output[output.name].add(skill.id)
            by_data_type[output.type].add(skill.id)
        for parameter in skill.inputs:
            if not lexicon.is_generic_io_name(parameter.name):
                by_input[parameter.name].add(skill.id)
            by_data_type[parameter.type].add(skill.id)
        for term in _skill_terms(skill):
            by_text_term[term].add(skill.id)

    neighbors: Dict[str, Set[str]] = defaultdict(set)
    upstream_by_input: Dict[str, Set[str]] = defaultdict(set)
    downstream_by_output: Dict[str, Set[str]] = defaultdict(set)
    skill_inputs = {skill.id: {parameter.name for parameter in skill.inputs} for skill in registry.ordered_skills()}
    skill_outputs = {skill.id: {output.name for output in skill.outputs} for skill in registry.ordered_skills()}
    for edge in graph.edges:
        source_id = _node_capability_id(edge.source)
        target_id = _node_capability_id(edge.target)
        if source_id not in registry.skills or target_id not in registry.skills:
            continue
        neighbors[source_id].add(target_id)
        if edge.type != "can_feed":
            continue
        shared = sorted(skill_outputs.get(source_id, set()) & skill_inputs.get(target_id, set()))
        for name in shared:
            if lexicon.is_generic_io_name(name):
                continue
            upstream_by_input[name].add(source_id)
            downstream_by_output[name].add(target_id)

    return GraphLookup(
        by_output=_freeze_lookup(by_output, max_bucket_size=16),
        by_input=_freeze_lookup(by_input, max_bucket_size=16),
        by_data_type=_freeze_lookup(by_data_type),
        neighbors=_freeze_lookup(neighbors),
        upstream_by_input=_freeze_lookup(upstream_by_input, max_bucket_size=16),
        downstream_by_output=_freeze_lookup(downstream_by_output, max_bucket_size=16),
        by_text_term=_freeze_lookup(by_text_term, max_bucket_size=24),
    )


def _freeze_lookup(
    lookup: Dict[str, Set[str]],
    *,
    max_bucket_size: int | None = None,
) -> Dict[str, List[str]]:
    frozen = {}
    for key, values in sorted(lookup.items()):
        if max_bucket_size is not None and len(values) > max_bucket_size:
            continue
        frozen[key] = sorted(values)
    return frozen


def _node_capability_id(value: object) -> str:
    return str(value or "").removeprefix("skill:").removeprefix("capability:")


def _skill_terms(skill: FingerprintLike) -> Set[str]:
    chunks: List[str] = [skill.id, skill.name, skill.description]
    chunks.extend(item.name for item in skill.inputs)
    chunks.extend(item.description for item in skill.inputs)
    chunks.extend(item.name for item in skill.outputs)
    chunks.extend(item.description for item in skill.outputs)
    terms: Set[str] = set()
    for chunk in chunks:
        terms.update(_GRAPH_TERM_LEXICON.tokenize(chunk))
    return terms


def _emit(progress: GraphProgress | None, stage: str, **details: Any) -> None:
    if progress is not None:
        progress(stage, **details)


def _candidate_generator_metadata(candidate_generator: CandidateGenerator) -> dict[str, int]:
    return {
        "max_candidates_per_skill_relation": int(candidate_generator.max_candidates_per_skill_relation),
        "max_port_mappings_per_candidate": int(candidate_generator.max_port_mappings_per_candidate),
        "max_exact_io_pair_fanout": int(candidate_generator.max_exact_io_pair_fanout),
    }
