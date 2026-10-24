from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

import torch
import torch.nn.functional as F

from video_memory.embedding.qwen_encoder import QwenTextEncoder, format_node_text, format_query_text


REQUIRED_FIELDS = {
    "sample_id",
    "qa_id",
    "qa_type",
    "availability",
    "evaluation_eligible",
    "query",
    "qa_time_id",
    "fact_id",
    "fact_description",
    "positive_node_ids",
    "supporting_node_ids",
    "ignore_node_ids",
    "hard_negative_node_ids",
    "reference_frame_groups",
    "candidate_db",
}
K_VALUES = (1, 3, 5, 10)


@dataclass(frozen=True)
class TrainingRecord:
    sample_id: str
    qa_id: str
    qa_type: str
    availability: str
    evaluation_eligible: bool
    query: str
    qa_time_id: int
    fact_id: str
    fact_description: str
    positive_node_ids: tuple[str, ...]
    supporting_node_ids: tuple[str, ...]
    ignore_node_ids: tuple[str, ...]
    hard_negative_node_ids: tuple[str, ...]
    reference_frame_groups: tuple[tuple[str, ...], ...]
    candidate_db: Path

    @property
    def supervised_node_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.positive_node_ids, *self.supporting_node_ids)))


@dataclass(frozen=True)
class CandidateNode:
    node_id: str
    node_type: str
    description_text: str
    time_ids: tuple[int, ...]


@dataclass(frozen=True)
class TrainingPair:
    sample_id: str
    qa_id: str
    fact_id: str
    query_text: str
    positive_node_id: str
    positive_text: str
    positive_weight: float
    negative_node_ids: tuple[str, ...]
    negative_texts: tuple[str, ...]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate, baseline, or train the Qwen embedding pilot.")
    parser.add_argument("--manifest", default="data/embedding_pilot_v1.jsonl")
    parser.add_argument("--mode", choices=["validate", "baseline", "train"], default="validate")
    parser.add_argument("--model-path", default="model/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--checkpoint", help="Adapter checkpoint to load in baseline mode.")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-length", type=int, default=320)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--recently-window-size", type=int, default=100)
    parser.add_argument(
        "--include-auxiliary",
        action="store_true",
        help="Include post-window auxiliary QA items in baseline metrics.",
    )
    parser.add_argument("--output-dir", default="outputs/embedding_pilot_v1")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--projection-learning-rate", type=float)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.07)
    parser.add_argument("--supporting-positive-weight", type=float, default=0.5)
    parser.add_argument("--projection-dim", type=int, default=512)
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-train-steps",
        type=int,
        help="Stop after this many optimizer steps; useful for a smoke test.",
    )
    parser.add_argument(
        "--online-only",
        action="store_true",
        help="Exclude post-window auxiliary facts from training.",
    )
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Skip the pre-training and per-checkpoint retrieval baselines.",
    )
    args = parser.parse_args()

    root = Path.cwd().resolve()
    manifest_path = _resolve_path(root, args.manifest)
    records = load_manifest(manifest_path, root)
    validation = validate_manifest(records)

    if args.mode == "validate":
        print(json.dumps(validation, indent=2, ensure_ascii=False))
        return

    if not torch.cuda.is_available():
        raise RuntimeError(f"CUDA is not available. Run {args.mode} mode where the NVIDIA devices are visible.")

    device_index = torch.device(args.device).index
    device_index = 0 if device_index is None else device_index
    torch.cuda.set_device(device_index)
    torch.cuda.reset_peak_memory_stats(device_index)

    evaluation_records = [
        record for record in records if record.evaluation_eligible or args.include_auxiliary
    ]
    if not evaluation_records:
        raise ValueError("No records selected for retrieval evaluation.")

    if args.mode == "baseline":
        encoder = QwenTextEncoder(
            args.model_path,
            device=args.device,
            max_length=args.max_length,
            adapter_path=args.checkpoint,
        )
        baseline = run_baseline(
            evaluation_records,
            encoder,
            batch_size=args.batch_size,
            recently_window_size=args.recently_window_size,
        )
        output = {"validation": validation, "baseline": baseline, "memory": encoder.memory_stats()}
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if args.checkpoint:
        raise ValueError("--checkpoint is currently supported only in baseline mode.")
    training_records = [
        record for record in records if not args.online_only or record.availability == "online_snapshot"
    ]
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    encoder = QwenTextEncoder(
        args.model_path,
        device=args.device,
        max_length=args.max_length,
        trainable=True,
        projection_dim=args.projection_dim,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
    )
    pre_training_baseline = None
    if not args.skip_evaluation:
        pre_training_baseline = run_baseline(
            evaluation_records,
            encoder,
            batch_size=args.batch_size,
            recently_window_size=args.recently_window_size,
        )
    training = train_embedding(
        training_records,
        evaluation_records,
        encoder,
        output_dir=_resolve_path(root, args.output_dir),
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        projection_learning_rate=args.projection_learning_rate or args.learning_rate,
        weight_decay=args.weight_decay,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        temperature=args.temperature,
        supporting_positive_weight=args.supporting_positive_weight,
        warmup_ratio=args.warmup_ratio,
        max_grad_norm=args.max_grad_norm,
        seed=args.seed,
        max_train_steps=args.max_train_steps,
        skip_evaluation=args.skip_evaluation,
        evaluation_batch_size=args.batch_size,
        recently_window_size=args.recently_window_size,
    )
    output = {
        "validation": validation,
        "pre_training_baseline": pre_training_baseline,
        "training": training,
        "memory": encoder.memory_stats(),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


def load_manifest(path: Path, root: Path) -> list[TrainingRecord]:
    records: list[TrainingRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            missing = sorted(REQUIRED_FIELDS - set(raw))
            if missing:
                raise ValueError(f"{path}:{line_number} is missing fields: {missing}")
            records.append(
                TrainingRecord(
                    sample_id=str(raw["sample_id"]),
                    qa_id=str(raw["qa_id"]),
                    qa_type=str(raw["qa_type"]),
                    availability=str(raw["availability"]),
                    evaluation_eligible=bool(raw["evaluation_eligible"]),
                    query=str(raw["query"]),
                    qa_time_id=int(raw["qa_time_id"]),
                    fact_id=str(raw["fact_id"]),
                    fact_description=str(raw["fact_description"]),
                    positive_node_ids=tuple(map(str, raw["positive_node_ids"])),
                    supporting_node_ids=tuple(map(str, raw["supporting_node_ids"])),
                    ignore_node_ids=tuple(map(str, raw["ignore_node_ids"])),
                    hard_negative_node_ids=tuple(map(str, raw["hard_negative_node_ids"])),
                    reference_frame_groups=tuple(tuple(map(str, group)) for group in raw["reference_frame_groups"]),
                    candidate_db=_resolve_path(root, raw["candidate_db"]),
                )
            )
    if not records:
        raise ValueError(f"Manifest is empty: {path}")
    return records


def validate_manifest(records: list[TrainingRecord]) -> dict[str, Any]:
    sample_ids: set[str] = set()
    issues: list[str] = []
    grouped: dict[str, list[TrainingRecord]] = defaultdict(list)

    for record in records:
        if record.sample_id in sample_ids:
            issues.append(f"duplicate sample_id: {record.sample_id}")
        sample_ids.add(record.sample_id)
        grouped[record.qa_id].append(record)

        if record.qa_type not in {"detail", "summary", "preference"}:
            issues.append(f"{record.sample_id}: invalid qa_type {record.qa_type!r}")
        if not record.supervised_node_ids:
            issues.append(f"{record.sample_id}: no positive or supporting nodes")
        if not record.reference_frame_groups or any(not group for group in record.reference_frame_groups):
            issues.append(f"{record.sample_id}: empty reference frame group")

        positive = set(record.supervised_node_ids)
        ignored = set(record.ignore_node_ids)
        negative = set(record.hard_negative_node_ids)
        if positive & ignored:
            issues.append(f"{record.sample_id}: positive/ignore overlap {sorted(positive & ignored)}")
        if positive & negative:
            issues.append(f"{record.sample_id}: positive/negative overlap {sorted(positive & negative)}")
        if ignored & negative:
            issues.append(f"{record.sample_id}: ignore/negative overlap {sorted(ignored & negative)}")

        issues.extend(_validate_record_database(record))

    for qa_id, qa_records in grouped.items():
        first = qa_records[0]
        for record in qa_records[1:]:
            for field_name in ("query", "qa_type", "qa_time_id", "candidate_db", "availability", "evaluation_eligible"):
                if getattr(record, field_name) != getattr(first, field_name):
                    issues.append(f"{qa_id}: inconsistent {field_name} across fact groups")

    summary = {
        "valid": not issues,
        "record_count": len(records),
        "qa_count": len(grouped),
        "online_record_count": sum(record.availability == "online_snapshot" for record in records),
        "auxiliary_record_count": sum(record.availability == "post_window_auxiliary" for record in records),
        "evaluation_record_count": sum(record.evaluation_eligible for record in records),
        "qa_fact_counts": {qa_id: len(items) for qa_id, items in sorted(grouped.items())},
        "issues": issues,
    }
    if issues:
        raise ValueError(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def _validate_record_database(record: TrainingRecord) -> list[str]:
    issues: list[str] = []
    if not record.candidate_db.exists():
        return [f"{record.sample_id}: missing database {record.candidate_db}"]

    with _connect_read_only(record.candidate_db) as conn:
        labeled = tuple(
            dict.fromkeys(
                (
                    *record.positive_node_ids,
                    *record.supporting_node_ids,
                    *record.ignore_node_ids,
                    *record.hard_negative_node_ids,
                )
            )
        )
        for node_id in labeled:
            row = conn.execute(
                "SELECT node_type, time_ids FROM memory_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            if row is None:
                issues.append(f"{record.sample_id}: missing node {node_id}")
                continue
            if row["node_type"] != record.qa_type:
                issues.append(
                    f"{record.sample_id}: node {node_id} has type {row['node_type']}, expected {record.qa_type}"
                )
            time_ids = tuple(map(int, json.loads(row["time_ids"])))
            if any(time_id > record.qa_time_id for time_id in time_ids):
                issues.append(f"{record.sample_id}: node {node_id} contains time after QA")

        references = tuple(dict.fromkeys(frame for group in record.reference_frame_groups for frame in group))
        for node_id in record.supervised_node_ids:
            if not _node_has_reference_edge(conn, node_id, references):
                issues.append(f"{record.sample_id}: supervised node {node_id} has no reference edge")
        for node_id in record.hard_negative_node_ids:
            if _node_has_reference_edge(conn, node_id, references):
                issues.append(f"{record.sample_id}: negative node {node_id} has a reference edge")
    return issues


def build_training_pairs(
    records: list[TrainingRecord],
    supporting_positive_weight: float,
) -> list[TrainingPair]:
    if not 0.0 < supporting_positive_weight <= 1.0:
        raise ValueError("supporting_positive_weight must be in (0, 1]")

    pairs: list[TrainingPair] = []
    for record in records:
        if not record.hard_negative_node_ids:
            raise ValueError(f"{record.sample_id}: training requires at least one hard negative")
        node_ids = tuple(dict.fromkeys((*record.supervised_node_ids, *record.hard_negative_node_ids)))
        nodes = _load_nodes_by_id(record.candidate_db, node_ids)
        missing = [node_id for node_id in node_ids if node_id not in nodes]
        if missing:
            raise ValueError(f"{record.sample_id}: missing labeled nodes {missing}")

        negative_texts = tuple(
            format_node_text(nodes[node_id].node_type, nodes[node_id].description_text)
            for node_id in record.hard_negative_node_ids
        )
        positive_weights = {node_id: 1.0 for node_id in record.positive_node_ids}
        for node_id in record.supporting_node_ids:
            positive_weights.setdefault(node_id, supporting_positive_weight)

        for node_id, positive_weight in positive_weights.items():
            node = nodes[node_id]
            pairs.append(
                TrainingPair(
                    sample_id=record.sample_id,
                    qa_id=record.qa_id,
                    fact_id=record.fact_id,
                    query_text=format_query_text(record.query, record.qa_type),
                    positive_node_id=node_id,
                    positive_text=format_node_text(node.node_type, node.description_text),
                    positive_weight=positive_weight,
                    negative_node_ids=record.hard_negative_node_ids,
                    negative_texts=negative_texts,
                )
            )
    if not pairs:
        raise ValueError("No training pairs were constructed.")
    return pairs


def train_embedding(
    training_records: list[TrainingRecord],
    evaluation_records: list[TrainingRecord],
    encoder: QwenTextEncoder,
    output_dir: Path,
    epochs: int,
    learning_rate: float,
    projection_learning_rate: float,
    weight_decay: float,
    gradient_accumulation_steps: int,
    temperature: float,
    supporting_positive_weight: float,
    warmup_ratio: float,
    max_grad_norm: float,
    seed: int,
    max_train_steps: int | None,
    skip_evaluation: bool,
    evaluation_batch_size: int,
    recently_window_size: int,
) -> dict[str, Any]:
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if gradient_accumulation_steps <= 0:
        raise ValueError("gradient_accumulation_steps must be positive")
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    if not 0.0 <= warmup_ratio < 1.0:
        raise ValueError("warmup_ratio must be in [0, 1)")
    if max_train_steps is not None and max_train_steps <= 0:
        raise ValueError("max_train_steps must be positive")
    if encoder.projection is None:
        raise ValueError("Training requires a projection layer.")

    from transformers import get_cosine_schedule_with_warmup

    random.seed(seed)
    torch.manual_seed(seed)
    pairs = build_training_pairs(training_records, supporting_positive_weight)
    indexed_pairs = list(enumerate(pairs))
    updates_per_epoch = math.ceil(len(pairs) / gradient_accumulation_steps)
    planned_updates = epochs * updates_per_epoch
    if max_train_steps is not None:
        planned_updates = min(planned_updates, max_train_steps)

    model_parameters = [parameter for parameter in encoder.model.parameters() if parameter.requires_grad]
    projection_parameters = [
        parameter for parameter in encoder.projection.parameters() if parameter.requires_grad
    ]
    trainable_parameters = [*model_parameters, *projection_parameters]
    optimizer = torch.optim.AdamW(
        [
            {"params": model_parameters, "lr": learning_rate, "weight_decay": 0.0},
            {
                "params": projection_parameters,
                "lr": projection_learning_rate,
                "weight_decay": weight_decay,
            },
        ]
    )
    warmup_steps = int(round(planned_updates * warmup_ratio))
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=planned_updates,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    optimizer.zero_grad(set_to_none=True)
    encoder.train()
    optimizer_steps = 0
    history: list[dict[str, Any]] = []
    final_checkpoint: Path | None = None

    for epoch_index in range(epochs):
        epoch_pairs = indexed_pairs.copy()
        random.Random(seed + epoch_index).shuffle(epoch_pairs)
        weighted_losses: list[float] = []
        raw_losses: list[float] = []
        margins: list[float] = []
        correct = 0
        micro_steps = 0
        accumulated = 0
        last_grad_norm = 0.0

        for position, (pair_index, pair) in enumerate(epoch_pairs):
            negative_index = (epoch_index + pair_index) % len(pair.negative_texts)
            vectors = encoder(
                [pair.query_text, pair.positive_text, pair.negative_texts[negative_index]]
            )
            positive_score = torch.dot(vectors[0], vectors[1])
            negative_score = torch.dot(vectors[0], vectors[2])
            logits = torch.stack((positive_score, negative_score)).unsqueeze(0) / temperature
            raw_loss = F.cross_entropy(logits, torch.zeros(1, dtype=torch.long, device=logits.device))
            weighted_loss = raw_loss * pair.positive_weight
            (weighted_loss / gradient_accumulation_steps).backward()

            raw_losses.append(float(raw_loss.detach().cpu()))
            weighted_losses.append(float(weighted_loss.detach().cpu()))
            margin = float((positive_score - negative_score).detach().cpu())
            margins.append(margin)
            correct += int(margin > 0)
            micro_steps += 1
            accumulated += 1

            is_epoch_end = position == len(epoch_pairs) - 1
            if accumulated < gradient_accumulation_steps and not is_epoch_end:
                continue

            if accumulated < gradient_accumulation_steps:
                correction = gradient_accumulation_steps / accumulated
                for parameter in trainable_parameters:
                    if parameter.grad is not None:
                        parameter.grad.mul_(correction)
            grad_norm = torch.nn.utils.clip_grad_norm_(trainable_parameters, max_grad_norm)
            last_grad_norm = float(grad_norm.detach().cpu())
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            optimizer_steps += 1
            accumulated = 0

            if max_train_steps is not None and optimizer_steps >= max_train_steps:
                break

        epoch_summary: dict[str, Any] = {
            "epoch": epoch_index + 1,
            "micro_steps": micro_steps,
            "optimizer_steps_total": optimizer_steps,
            "mean_raw_loss": mean(raw_losses),
            "mean_weighted_loss": mean(weighted_losses),
            "pair_accuracy": correct / micro_steps,
            "mean_similarity_margin": mean(margins),
            "last_gradient_norm": last_grad_norm,
            "learning_rates": [group["lr"] for group in optimizer.param_groups],
        }
        if not skip_evaluation:
            epoch_summary["retrieval_baseline"] = run_baseline(
                evaluation_records,
                encoder,
                batch_size=evaluation_batch_size,
                recently_window_size=recently_window_size,
            )

        checkpoint_name = f"checkpoint-epoch-{epoch_index + 1:03d}"
        if max_train_steps is not None and optimizer_steps >= max_train_steps:
            checkpoint_name = f"checkpoint-step-{optimizer_steps:06d}"
        final_checkpoint = output_dir / checkpoint_name
        history.append(epoch_summary)
        encoder.save_checkpoint(
            final_checkpoint,
            {
                "epoch": epoch_index + 1,
                "optimizer_steps": optimizer_steps,
                "training_pair_count": len(pairs),
                "training_record_count": len(training_records),
                "supporting_positive_weight": supporting_positive_weight,
                "temperature": temperature,
                "projection_dim": encoder.embedding_dim,
                "history": history,
            },
        )
        print(json.dumps({"training_epoch": epoch_summary, "checkpoint": str(final_checkpoint)}, ensure_ascii=False))

        if max_train_steps is not None and optimizer_steps >= max_train_steps:
            break

    return {
        "training_record_count": len(training_records),
        "training_pair_count": len(pairs),
        "optimizer_steps": optimizer_steps,
        "trainable_parameters": encoder.trainable_parameter_counts(),
        "history": history,
        "final_checkpoint": str(final_checkpoint) if final_checkpoint is not None else None,
    }


def _load_nodes_by_id(path: Path, node_ids: tuple[str, ...]) -> dict[str, CandidateNode]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" for _ in node_ids)
    with _connect_read_only(path) as conn:
        rows = conn.execute(
            f"SELECT node_id, node_type, description_text, time_ids FROM memory_nodes "
            f"WHERE node_id IN ({placeholders})",
            node_ids,
        ).fetchall()
    return {
        row["node_id"]: CandidateNode(
            node_id=row["node_id"],
            node_type=row["node_type"],
            description_text=row["description_text"],
            time_ids=tuple(map(int, json.loads(row["time_ids"]))),
        )
        for row in rows
    }


def run_baseline(
    records: list[TrainingRecord],
    encoder: QwenTextEncoder,
    batch_size: int,
    recently_window_size: int,
) -> dict[str, Any]:
    grouped: dict[str, list[TrainingRecord]] = defaultdict(list)
    for record in records:
        grouped[record.qa_id].append(record)

    per_qa: list[dict[str, Any]] = []
    for qa_id, qa_records in sorted(grouped.items()):
        first = qa_records[0]
        start_time = _candidate_start_time(first, recently_window_size)
        candidates = _load_candidates(first.candidate_db, first.qa_type, start_time, first.qa_time_id)
        if not candidates:
            raise ValueError(f"{qa_id}: candidate pool is empty")

        query_text = format_query_text(first.query, first.qa_type)
        node_texts = [format_node_text(node.node_type, node.description_text) for node in candidates]
        query_vector = encoder.encode([query_text], batch_size=1)[0]
        node_vectors = encoder.encode(node_texts, batch_size=batch_size)
        scores = torch.mv(node_vectors, query_vector)
        order = torch.argsort(scores, descending=True).tolist()
        rank_by_node = {candidates[index].node_id: rank + 1 for rank, index in enumerate(order)}

        fact_ranks: dict[str, int] = {}
        for record in qa_records:
            available = [rank_by_node[node_id] for node_id in record.supervised_node_ids if node_id in rank_by_node]
            if not available:
                raise ValueError(f"{record.sample_id}: no supervised nodes survived candidate filtering")
            fact_ranks[record.fact_id] = min(available)

        row: dict[str, Any] = {
            "qa_id": qa_id,
            "availability": first.availability,
            "qa_type": first.qa_type,
            "candidate_count": len(candidates),
            "fact_count": len(qa_records),
            "fact_ranks": fact_ranks,
            "mean_reciprocal_rank": mean(1.0 / rank for rank in fact_ranks.values()),
            "mean_positive_rank": mean(fact_ranks.values()),
            "top_nodes": [
                {
                    "rank": rank + 1,
                    "node_id": candidates[index].node_id,
                    "score": float(scores[index]),
                    "description_text": candidates[index].description_text,
                }
                for rank, index in enumerate(order[:10])
            ],
        }
        for k in K_VALUES:
            covered = sum(rank <= k for rank in fact_ranks.values())
            row[f"fact_coverage_at_{k}"] = covered / len(fact_ranks)
            row[f"all_facts_covered_at_{k}"] = covered == len(fact_ranks)
        per_qa.append(row)

    aggregate: dict[str, Any] = {
        "qa_count": len(per_qa),
        "fact_count": sum(item["fact_count"] for item in per_qa),
        "mean_qa_mrr": mean(item["mean_reciprocal_rank"] for item in per_qa),
        "mean_qa_positive_rank": mean(item["mean_positive_rank"] for item in per_qa),
    }
    for k in K_VALUES:
        aggregate[f"mean_fact_coverage_at_{k}"] = mean(item[f"fact_coverage_at_{k}"] for item in per_qa)
        aggregate[f"all_facts_covered_qa_count_at_{k}"] = sum(item[f"all_facts_covered_at_{k}"] for item in per_qa)
    return {"aggregate": aggregate, "per_qa": per_qa}


def _load_candidates(path: Path, node_type: str, start_time: int, end_time: int) -> list[CandidateNode]:
    candidates: list[CandidateNode] = []
    with _connect_read_only(path) as conn:
        rows = conn.execute(
            "SELECT node_id, node_type, description_text, time_ids FROM memory_nodes WHERE node_type = ? ORDER BY node_id",
            (node_type,),
        ).fetchall()
    for row in rows:
        time_ids = tuple(map(int, json.loads(row["time_ids"])))
        if any(start_time <= time_id <= end_time for time_id in time_ids):
            candidates.append(
                CandidateNode(
                    node_id=row["node_id"],
                    node_type=row["node_type"],
                    description_text=row["description_text"],
                    time_ids=time_ids,
                )
            )
    return candidates


def _candidate_start_time(record: TrainingRecord, recently_window_size: int) -> int:
    if record.qa_type != "preference" and "recently" in record.query.lower():
        return max(0, record.qa_time_id - recently_window_size + 1)
    return 0


def _node_has_reference_edge(conn: sqlite3.Connection, node_id: str, references: Iterable[str]) -> bool:
    references = tuple(references)
    if not references:
        return False
    placeholders = ",".join("?" for _ in references)
    row = conn.execute(
        f"SELECT 1 FROM node_frame_edges WHERE node_id = ? AND frame_key IN ({placeholders}) LIMIT 1",
        (node_id, *references),
    ).fetchone()
    return row is not None


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


if __name__ == "__main__":
    main()
