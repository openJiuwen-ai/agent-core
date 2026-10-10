from __future__ import annotations

import argparse
import json
from dataclasses import replace

from video_memory.config import load_config
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.llm.api_client import make_model_client
from video_memory.retrieval.qa_parser import QAParser
from video_memory.schemas import QAItem


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--qa-id")
    parser.add_argument("--qa-index", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--provider", choices=["openrouter", "openai"])
    args = parser.parse_args()

    config = load_config(args.config)
    if args.provider:
        config = replace(config, llm=replace(config.llm, provider=args.provider))

    frame_index = build_frame_index(config.paths.frames_dir)
    qa_items = load_qa_items(config.paths.qa_path, frame_index)
    selected = qa_items if args.all else [_select_qa(qa_items, args.qa_id, args.qa_index)]

    client = make_model_client(config.llm)
    qa_parser = QAParser(client, recently_window_size=config.retrieval.recently_window_size)
    video_time_range = (frame_index.min_time_id(), frame_index.max_time_id())

    outputs = []
    for qa in selected:
        parsed = qa_parser.parse(qa, video_time_range)
        reference_times = _reference_times(qa, frame_index)
        outputs.append(
            {
                "qa_id": qa.qa_id,
                "question": qa.question,
                "answer": qa.answer,
                "raw_type": qa.raw_type,
                "qa_time_key": qa.qa_time_key,
                "qa_time_id": qa.qa_time_id,
                "reference_sets": qa.reference_sets,
                "reference_times": reference_times,
                "parse": parsed.to_dict(),
                "time_range_contains_reference": _contains_any_reference_set(parsed.time_range, reference_times),
                "time_range_width": _range_width(parsed.time_range),
            }
        )

    print(json.dumps(outputs if args.all else outputs[0], ensure_ascii=False, indent=2))


def _select_qa(items: list[QAItem], qa_id: str | None, qa_index: int) -> QAItem:
    if qa_id is not None:
        for item in items:
            if item.qa_id == qa_id:
                return item
        raise KeyError(f"Unknown qa_id: {qa_id}")
    return items[qa_index]


def _reference_times(qa: QAItem, frame_index) -> list[list[int | None]]:
    return [[frame_index.time_for_key(frame_key) for frame_key in refset] for refset in qa.reference_sets]


def _contains_any_reference_set(
    time_range: tuple[int, int] | None,
    reference_times: list[list[int | None]],
) -> bool:
    if time_range is None:
        return False
    start, end = time_range
    for refset in reference_times:
        known_times = [time_id for time_id in refset if time_id is not None]
        if known_times and all(start <= time_id <= end for time_id in known_times):
            return True
    return False


def _range_width(time_range: tuple[int, int] | None) -> int | None:
    if time_range is None:
        return None
    return time_range[1] - time_range[0] + 1


if __name__ == "__main__":
    main()
