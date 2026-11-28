from __future__ import annotations

import json

from video_memory.config import AppConfig
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.evaluation.metrics import evaluate_qa
from video_memory.pipelines.run_qa import RunQAPipeline
from video_memory.tracing.trace_logger import TraceLogger


class EvaluatePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self, limit: int | None = None) -> dict:
        frame_index = build_frame_index(self.config.paths.frames_dir)
        qa_items = load_qa_items(self.config.paths.qa_path, frame_index)
        if limit is not None:
            qa_items = qa_items[:limit]

        runner = RunQAPipeline(self.config)
        trace = TraceLogger(self.config.tracing.traces_dir, self.config.tracing.enabled)
        results = []

        for qa in qa_items:
            answer = runner.run_item(qa, frame_index)
            result = evaluate_qa(qa, answer)
            results.append(result)
            trace.write_json(f"eval/{qa.qa_id}.json", result.to_dict())

        aggregate = _aggregate([result.to_dict() for result in results])
        output_path = self.config.paths.output_dir / "eval" / "summary.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
        return aggregate


def _aggregate(results: list[dict]) -> dict:
    if not results:
        return {"count": 0}

    keys = [
        "qa_accuracy",
        "reference_recall",
        "redundant_ratio",
        "evidence_precision",
        "evidence_f1",
        "evidence_unit_coverage",
        "fact_completeness",
        "evidence_sufficiency",
        "valid_evidence_precision",
        "background_ratio",
        "off_target_ratio",
        "conditional_redundant_ratio",
    ]
    aggregate = {"count": len(results)}
    for key in keys:
        values = [float(result[key]) for result in results if result.get(key) is not None]
        aggregate[f"mean_{key}"] = sum(values) / len(values) if values else None
        if key == "conditional_redundant_ratio":
            aggregate["conditional_redundant_qa_count"] = len(values)

    covered_units = sum(int(result.get("covered_evidence_unit_count", 0)) for result in results)
    evidence_units = sum(int(result.get("evidence_unit_count", 0)) for result in results)
    aggregate["micro_evidence_unit_coverage"] = covered_units / evidence_units if evidence_units else 0.0
    aggregate["covered_evidence_unit_count"] = covered_units
    aggregate["evidence_unit_count"] = evidence_units
    return aggregate
