from __future__ import annotations

from video_memory.llm.base import ModelClient
from video_memory.schemas import AnswerResult, FrameRecord, MemoryNode, QAItem, RetrievedContext


class Answerer:
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    def answer(
        self,
        qa: QAItem,
        selected_nodes: list[MemoryNode],
        frames: list[FrameRecord],
        context: RetrievedContext,
    ) -> AnswerResult:
        answer = self.client.answer(qa.question, selected_nodes, frames)
        return AnswerResult(
            qa_id=qa.qa_id,
            answer=answer,
            selected_node_ids=context.selected_node_ids,
            retrieved_frame_keys=context.retrieved_frame_keys,
        )

