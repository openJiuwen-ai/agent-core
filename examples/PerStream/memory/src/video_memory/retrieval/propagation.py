from __future__ import annotations

import re
from collections import defaultdict

from video_memory.schemas import Entity, NodeEntityEdge, PropagationResult, PropagationStep, RankedNode


class GraphPropagator:
    def __init__(
        self,
        max_hops: int,
        entity_score_threshold: float,
        propagation_score_threshold: float,
        decay: float,
    ) -> None:
        self.max_hops = max_hops
        self.entity_score_threshold = entity_score_threshold
        self.propagation_score_threshold = propagation_score_threshold
        self.decay = decay

    def propagate(
        self,
        ranked_nodes: list[RankedNode],
        allowed_node_ids: set[str],
        node_entity_edges: list[NodeEntityEdge],
        entities: list[Entity],
        query_entities: list[str],
    ) -> PropagationResult:
        node_to_entities: dict[str, set[str]] = defaultdict(set)
        entity_to_nodes: dict[str, set[str]] = defaultdict(set)
        for edge in node_entity_edges:
            if edge.node_id not in allowed_node_ids:
                continue
            node_to_entities[edge.node_id].add(edge.entity_id)
            entity_to_nodes[edge.entity_id].add(edge.node_id)

        entity_by_id = {entity.entity_id: entity for entity in entities}
        node_scores = {ranked.node_id: ranked.score for ranked in ranked_nodes if ranked.node_id in allowed_node_ids}
        frontier = set(node_scores)
        steps: list[PropagationStep] = []

        for hop in range(1, self.max_hops + 1):
            updates: dict[str, float] = {}
            for source_node_id in frontier:
                source_score = node_scores.get(source_node_id, 0.0)
                for entity_id in node_to_entities.get(source_node_id, set()):
                    entity = entity_by_id.get(entity_id)
                    if entity is None:
                        continue
                    entity_score = _entity_relevance(entity, query_entities)
                    if entity_score < self.entity_score_threshold:
                        continue
                    for target_node_id in entity_to_nodes.get(entity_id, set()):
                        if target_node_id == source_node_id:
                            continue
                        propagated = source_score * entity_score * self.decay
                        if propagated < self.propagation_score_threshold:
                            continue
                        if propagated > node_scores.get(target_node_id, 0.0) and propagated > updates.get(target_node_id, 0.0):
                            updates[target_node_id] = propagated
                            steps.append(
                                PropagationStep(
                                    hop=hop,
                                    source_node_id=source_node_id,
                                    entity_id=entity_id,
                                    target_node_id=target_node_id,
                                    source_score=source_score,
                                    entity_score=entity_score,
                                    propagated_score=propagated,
                                )
                            )

            if not updates:
                break
            node_scores.update(updates)
            frontier = set(updates)

        return PropagationResult(node_scores=node_scores, steps=steps)


def _entity_relevance(entity: Entity, query_entities: list[str]) -> float:
    if not query_entities:
        return 0.6
    entity_tokens = _tokens(entity.canonical_name)
    best = 0.0
    for query_entity in query_entities:
        best = max(best, _jaccard(entity_tokens, _tokens(query_entity)))
    return best


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9]+", text) if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)

