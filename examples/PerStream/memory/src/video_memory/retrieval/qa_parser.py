from __future__ import annotations

from video_memory.llm.base import ModelClient
from video_memory.schemas import QAItem, QAParseResult


class QAParser:
    def __init__(self, client: ModelClient, recently_window_size: int = 100) -> None:
        self.client = client
        self.recently_window_size = recently_window_size

    def parse(self, qa: QAItem, video_time_range: tuple[int, int]) -> QAParseResult:
        parsed = self.client.parse_qa(qa, video_time_range)
        end = qa.qa_time_id if qa.qa_time_id is not None else video_time_range[1]
        time_range = parsed.time_range or (video_time_range[0], end)
        time_range = (max(video_time_range[0], time_range[0]), min(end, time_range[1]))

        qa_types = _single_type(parsed.qa_types)
        if qa_types == ["preference"]:
            time_range = (video_time_range[0], end)
        elif parsed.temporal_hint == "recently":
            time_range = (max(video_time_range[0], end - self.recently_window_size + 1), end)

        return QAParseResult(
            qa_types=qa_types,
            entities=parsed.entities,
            time_range=time_range,
            temporal_hint=parsed.temporal_hint,
            time_order=parsed.time_order,
            intent=parsed.intent,
        )


def _single_type(qa_types: list[str]) -> list:
    for value in qa_types:
        if value in {"detail", "summary", "preference"}:
            return [value]
    return []
