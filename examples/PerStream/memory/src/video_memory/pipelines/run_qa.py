from __future__ import annotations

from collections import defaultdict

from video_memory.answer.answerer import Answerer
from video_memory.config import AppConfig
from video_memory.data.frame_index import FrameIndex, build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.llm.api_client import make_model_client
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.retrieval.filter import filter_nodes
from video_memory.retrieval.propagation import GraphPropagator
from video_memory.retrieval.qa_parser import QAParser
from video_memory.retrieval.ranker import APINodeRanker, RuleBasedNodeRanker
from video_memory.retrieval.selector import select_nodes
from video_memory.schemas import AnswerResult, QAItem, RankedNode
from video_memory.tracing.trace_logger import TraceLogger


class RunQAPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run_one(self, qa_id: str | None = None, qa_index: int = 0) -> AnswerResult:
        frame_index = build_frame_index(self.config.paths.frames_dir)
        qa_items = load_qa_items(self.config.paths.qa_path, frame_index)
        qa = _select_qa(qa_items, qa_id, qa_index)
        return self.run_item(qa, frame_index)

    def run_item(self, qa: QAItem, frame_index: FrameIndex) -> AnswerResult:
        client = make_model_client(self.config.llm)
        store = SQLiteMemoryStore(self.config.paths.memory_db)
        trace = TraceLogger(self.config.tracing.traces_dir, self.config.tracing.enabled)

        parser = QAParser(client, recently_window_size=self.config.retrieval.recently_window_size)
        parsed = parser.parse(qa, (frame_index.min_time_id(), frame_index.max_time_id()))
        all_nodes = store.list_nodes()
        filtered_nodes = filter_nodes(all_nodes, parsed)

        if self.config.llm.provider == "openai":
            ranker = APINodeRanker(client)
        else:
            ranker = RuleBasedNodeRanker()
        ranked = ranker.rank(qa.question, parsed.entities, filtered_nodes)
        seed_ranked = [item for item in ranked if item.score >= self.config.retrieval.node_score_threshold]
        if not seed_ranked:
            seed_ranked = ranked[: self.config.retrieval.min_k]

        allowed_node_ids = {node.node_id for node in filtered_nodes}
        node_entity_edges = store.node_entity_edges(allowed_node_ids)
        propagator = GraphPropagator(
            max_hops=self.config.retrieval.max_hops,
            entity_score_threshold=self.config.retrieval.entity_score_threshold,
            propagation_score_threshold=self.config.retrieval.propagation_score_threshold,
            decay=self.config.retrieval.decay,
        )
        propagation = propagator.propagate(
            ranked_nodes=seed_ranked,
            allowed_node_ids=allowed_node_ids,
            node_entity_edges=node_entity_edges,
            entities=store.list_entities(),
            query_entities=parsed.entities,
        )

        node_to_frames = _node_to_frames(store, allowed_node_ids)
        context = select_nodes(
            propagation.node_scores,
            node_to_frames,
            final_node_threshold=self.config.retrieval.final_node_threshold,
            min_k=self.config.retrieval.min_k,
            max_k=self.config.retrieval.max_k,
        )
        selected_nodes = store.get_nodes(context.selected_node_ids)
        selected_frames = frame_index.subset(context.retrieved_frame_keys)
        answer = Answerer(client).answer(qa, selected_nodes, selected_frames, context)

        trace.write_json(
            f"qa_runs/{qa.qa_id}.json",
            {
                "qa": qa.to_dict(),
                "parsed": parsed.to_dict(),
                "filtered_node_ids": [node.node_id for node in filtered_nodes],
                "ranked": [rank.to_dict() for rank in ranked],
                "seed_ranked": [rank.to_dict() for rank in seed_ranked],
                "propagation": propagation.to_dict(),
                "selected_context": context.to_dict(),
                "answer": answer.to_dict(),
            },
        )

        store.close()
        return answer


def _select_qa(items: list[QAItem], qa_id: str | None, qa_index: int) -> QAItem:
    if qa_id is not None:
        for item in items:
            if item.qa_id == qa_id:
                return item
        raise KeyError(f"Unknown qa_id: {qa_id}")
    return items[qa_index]


def _node_to_frames(store: SQLiteMemoryStore, allowed_node_ids: set[str]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = defaultdict(list)
    for edge in store.node_frame_edges(allowed_node_ids):
        mapping[edge.node_id].append(edge.frame_key)
    return dict(mapping)
