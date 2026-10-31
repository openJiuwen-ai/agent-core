from __future__ import annotations

import re

from video_memory.llm.base import ModelClient
from video_memory.schemas import MemoryNode, RankedNode


class RuleBasedNodeRanker:
    def rank(self, question: str, qa_entities: list[str], candidate_nodes: list[MemoryNode]) -> list[RankedNode]:
        question_tokens = _tokens(question)
        entity_tokens = set().union(*(_tokens(entity) for entity in qa_entities)) if qa_entities else set()
        ranked: list[RankedNode] = []

        for node in candidate_nodes:
            node_tokens = _tokens(node.description_text)
            lexical = _jaccard(question_tokens, node_tokens)
            entity_overlap = _jaccard(entity_tokens, node_tokens) if entity_tokens else 0.0
            score = min(1.0, 0.2 + lexical * 0.6 + entity_overlap * 0.3)
            ranked.append(RankedNode(node.node_id, score, "rule_based_overlap"))

        return sorted(ranked, key=lambda item: item.score, reverse=True)


class APINodeRanker:
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    def rank(self, question: str, qa_entities: list[str], candidate_nodes: list[MemoryNode]) -> list[RankedNode]:
        scores = self.client.rank_nodes(question, qa_entities, candidate_nodes)
        return sorted(
            [RankedNode(node.node_id, float(scores.get(node.node_id, 0.0)), "api_rank") for node in candidate_nodes],
            key=lambda item: item.score,
            reverse=True,
        )


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9]+", text) if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)

