from __future__ import annotations

from typing import TYPE_CHECKING

from video_memory.embedding.api_embedder import APIEmbedder, cosine_similarity
from video_memory.retrieval.field_ranker import field_bonus_score, split_multiple_choice
from video_memory.schemas import MemoryNode, QAItem, QAParseResult, RankedNode

if TYPE_CHECKING:  # torch is only needed by the Qwen ranker below.
    import torch

    from video_memory.embedding.qwen_encoder import QwenTextEncoder


class EmbeddingNodeRanker:
    def __init__(self, embedder: APIEmbedder, field_bonus_weight: float = 0.15) -> None:
        self.embedder = embedder
        self.field_bonus_weight = field_bonus_weight

    def rank(self, qa: QAItem, parsed: QAParseResult, candidate_nodes: list[MemoryNode]) -> list[RankedNode]:
        question_main, _ = split_multiple_choice(qa.question)
        query_text = " | ".join(
            part
            for part in [
                question_main,
                parsed.intent,
                " ".join(parsed.entities),
                " ".join(qa.raw_type),
            ]
            if part
        )
        node_texts = [node.description_text for node in candidate_nodes]
        query_vector = self.embedder.embed_text(query_text)
        node_vectors = self.embedder.embed_texts(node_texts)

        ranked: list[RankedNode] = []
        for node, vector in zip(candidate_nodes, node_vectors, strict=True):
            cosine = (cosine_similarity(query_vector, vector) + 1.0) / 2.0
            bonus = field_bonus_score(question_main, node.description_text, qa.raw_type)
            score = min(1.0, cosine * (1.0 - self.field_bonus_weight) + bonus * self.field_bonus_weight)
            ranked.append(RankedNode(node.node_id, score, f"embedding_cosine cosine={cosine:.3f} bonus={bonus:.2f}"))

        return sorted(ranked, key=lambda item: item.score, reverse=True)


class QwenEmbeddingNodeRanker:
    def __init__(self, encoder: QwenTextEncoder, batch_size: int = 1) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.encoder = encoder
        self.batch_size = batch_size
        self._node_cache: dict[str, torch.Tensor] = {}

    def rank(self, qa: QAItem, parsed: QAParseResult, candidate_nodes: list[MemoryNode]) -> list[RankedNode]:
        import torch

        from video_memory.embedding.qwen_encoder import format_node_text, format_query_text

        if not candidate_nodes:
            return []
        qa_type = parsed.qa_types[0] if parsed.qa_types else candidate_nodes[0].node_type
        query_text = format_query_text(qa.question, qa_type)
        node_texts = [format_node_text(node.node_type, node.description_text) for node in candidate_nodes]
        query_vector = self.encoder.encode([query_text], batch_size=1)[0]
        node_vectors = self._encode_cached(node_texts)
        raw_scores = torch.mv(node_vectors, query_vector)

        ranked = [
            RankedNode(
                node.node_id,
                float((raw_scores[index] + 1.0) / 2.0),
                f"qwen_embedding_cosine cosine={float(raw_scores[index]):.3f}",
            )
            for index, node in enumerate(candidate_nodes)
        ]
        return sorted(ranked, key=lambda item: item.score, reverse=True)

    def _encode_cached(self, texts: list[str]) -> torch.Tensor:
        import torch

        missing = list(dict.fromkeys(text for text in texts if text not in self._node_cache))
        if missing:
            vectors = self.encoder.encode(missing, batch_size=self.batch_size)
            self._node_cache.update(zip(missing, vectors, strict=True))
        return torch.stack([self._node_cache[text] for text in texts])
