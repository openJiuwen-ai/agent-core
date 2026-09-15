from __future__ import annotations

from pathlib import Path
from typing import Protocol

from video_memory.schemas import FrameRecord, FrameWindow, MemoryNode, QAItem, QAParseResult


class ModelClient(Protocol):
    def generate_memory(self, window: FrameWindow, frames: list[FrameRecord]) -> list[dict]:
        ...

    def generate_memory_from_ocr(
        self,
        window: FrameWindow,
        frames: list[FrameRecord],
        ocr_observations: list[dict],
    ) -> list[dict]:
        ...

    def parse_qa(self, qa: QAItem, video_time_range: tuple[int, int]) -> QAParseResult:
        ...

    def rank_nodes(
        self,
        question: str,
        qa_entities: list[str],
        candidate_nodes: list[MemoryNode],
    ) -> dict[str, float]:
        ...

    def answer(
        self,
        question: str,
        selected_nodes: list[MemoryNode],
        frames: list[FrameRecord],
    ) -> str:
        ...


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")
