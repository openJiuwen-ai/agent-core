"""Scan and train selected V1 SFT trajectory data with LLaMA-Factory LoRA."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
JIUWENRL_ROOT = SCRIPT_DIR.parent
AGENT_CORE_ROOT = JIUWENRL_ROOT.parents[1]

if str(AGENT_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE_ROOT))

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.llama_factory import (
    LLaMAFactoryTrainConfig,
    convert_samples_to_llama_factory_openai,
    prepare_llama_factory_records_run,
    run_llama_factory_train_cli,
)

DEFAULT_SFT_DATA = "/data1/lll/workspace/swe-dataset/selected_datasets_llamav1"
DEFAULT_MODEL_PATH = "/data1/lll/models/Qwen3-4B-Instruct-2507"
DEFAULT_OUTPUT_ROOT = "/data1/lll/workspace/swe-dataset/llama_factory_sft_runs"


@dataclass(frozen=True)
class SourceSample:
    sample: dict[str, Any]
    source: str
    line: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="?", default=os.getenv("SFT_V1_DATA_PATH", DEFAULT_SFT_DATA))
    parser.add_argument("--model-path", default=os.getenv("STUDENT_MODEL_PATH", os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH)))
    parser.add_argument("--output-root", default=os.getenv("SFT_LLAMAFACTORY_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "4,5,6,7"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_SAMPLE_LIMIT", "0")))
    parser.add_argument("--cutoff-len", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_CUTOFF_LEN", "32768")))
    parser.add_argument("--template", default=os.getenv("SFT_LLAMAFACTORY_TEMPLATE", "qwen"))
    parser.add_argument("--epochs", type=float, default=float(os.getenv("SFT_LLAMAFACTORY_EPOCHS", "1")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_MAX_STEPS", "-1")))
    parser.add_argument("--learning-rate", default=os.getenv("SFT_LLAMAFACTORY_LR", "1e-5"))
    parser.add_argument("--lora-rank", type=int, default=int(os.getenv("SFT_LORA_RANK", "16")))
    parser.add_argument("--lora-alpha", type=int, default=int(os.getenv("SFT_LORA_ALPHA", "32")))
    parser.add_argument("--lora-target", default=os.getenv("SFT_LORA_TARGET", os.getenv("SFT_TARGET_MODULES", "all")))
    parser.add_argument("--scan-only", action="store_true", help="Only convert/token-scan samples and write reports.")
    parser.add_argument("--prepare-only", action="store_true", help="Write LLaMA-Factory files without launching training.")
    parser.add_argument("--fail-on-overlength", action="store_true", help="Exit before training when any sample exceeds cutoff_len.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.output_root).resolve() / datetime.now(tz=UTC).strftime("run_%Y%m%d_%H%M%S")
    dataset_dir = run_dir / "dataset"
    output_dir = run_dir / "lora"
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    source_samples = load_source_samples(Path(args.samples), limit=args.limit)
    scan = scan_samples(
        source_samples,
        model_path=args.model_path,
        cutoff_len=args.cutoff_len,
    )
    write_json(report_dir / "scan_report.json", scan["summary"])
    write_json(report_dir / "failed_samples.json", scan["failed"])

    train_records = scan["records"]
    print(f"[selected-v1-sft] source={Path(args.samples).resolve()}")
    print(f"[selected-v1-sft] loaded={len(source_samples)} trainable={len(train_records)} failed={len(scan['failed'])}")
    print(f"[selected-v1-sft] scan_report={report_dir / 'scan_report.json'}")
    print(f"[selected-v1-sft] failed_samples={report_dir / 'failed_samples.json'}")

    if not train_records:
        print("[selected-v1-sft] no trainable records after scan", file=sys.stderr)
        return 3
    if args.fail_on_overlength and any(item["reason"] == "over_cutoff_len" for item in scan["failed"]):
        print("[selected-v1-sft] overlength samples found; aborting before training", file=sys.stderr)
        return 4

    train_config = LLaMAFactoryTrainConfig(
        model_name_or_path=args.model_path,
        output_dir=str(output_dir),
        cutoff_len=args.cutoff_len,
        template=args.template,
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_target=args.lora_target,
    )
    paths = prepare_llama_factory_records_run(train_records, dataset_dir=dataset_dir, train_config=train_config)
    print(f"[selected-v1-sft] train_file={paths.train_file}")
    print(f"[selected-v1-sft] train_yaml={paths.train_yaml_file}")
    print(f"[selected-v1-sft] output_dir={output_dir}")

    if args.scan_only or args.prepare_only:
        print("[selected-v1-sft] scan/prepare mode; skip training")
        return 0

    run_llama_factory_train_cli(paths.train_yaml_file, run_dir=run_dir, training_gpu_ids=args.train_gpu)
    return 0


def load_source_samples(path: Path, *, limit: int = 0) -> list[SourceSample]:
    files = sorted([*path.rglob("*.jsonl"), *path.rglob("*.json")]) if path.is_dir() else [path]
    out: list[SourceSample] = []
    for file_path in files:
        for item in load_source_file(file_path):
            out.append(item)
            if limit > 0 and len(out) >= limit:
                return out
    return out


def load_source_file(path: Path) -> list[SourceSample]:
    if path.suffix.lower() == ".jsonl":
        rows: list[SourceSample] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(SourceSample(payload, str(path), line_no))
        return rows

    payload = json.loads(path.read_text(encoding="utf-8"))
    items = (
        payload.get("samples") or payload.get("trajectories") or payload.get("data") or [payload]
        if isinstance(payload, dict)
        else payload
    )
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [SourceSample(item, str(path), idx) for idx, item in enumerate(items, start=1) if isinstance(item, dict)]


def scan_samples(source_samples: list[SourceSample], *, model_path: str, cutoff_len: int) -> dict[str, Any]:
    tokenizer = load_tokenizer(model_path)
    train_records: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    token_lengths: list[int] = []

    for idx, source_sample in enumerate(source_samples):
        converted = convert_samples_to_llama_factory_openai([source_sample.sample])
        sample_id = sample_identifier(source_sample.sample, idx)
        if not converted:
            failed.append(failure(source_sample, sample_id=sample_id, reason="conversion_empty"))
            continue

        record = converted[0]
        try:
            token_count = estimate_tokens(record, tokenizer=tokenizer)
        except (TypeError, ValueError, RuntimeError) as exc:
            failed.append(failure(source_sample, sample_id=sample_id, reason="tokenize_failed", message=str(exc)))
            continue

        token_lengths.append(token_count)
        record.setdefault("metadata", {})
        record["metadata"].update(
            {
                "source_file": source_sample.source,
                "source_line": source_sample.line,
                "token_count": token_count,
            }
        )
        if token_count > cutoff_len:
            failed.append(
                failure(
                    source_sample,
                    sample_id=sample_id,
                    reason="over_cutoff_len",
                    token_count=token_count,
                    message=f"token_count={token_count} cutoff_len={cutoff_len}",
                )
            )
            continue
        train_records.append(record)

    summary = {
        "model_path": model_path,
        "cutoff_len": cutoff_len,
        "input_samples": len(source_samples),
        "trainable_records": len(train_records),
        "failed_records": len(failed),
        "overlength_records": sum(1 for item in failed if item["reason"] == "over_cutoff_len"),
        "max_token_count": max(token_lengths) if token_lengths else 0,
        "min_token_count": min(token_lengths) if token_lengths else 0,
    }
    return {"records": train_records, "failed": failed, "summary": summary}


def load_tokenizer(model_path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)


def estimate_tokens(record: dict[str, Any], *, tokenizer: Any) -> int:
    messages = record.get("messages") or []
    try:
        token_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
        return len(token_ids)
    except (KeyError, TypeError, ValueError, RuntimeError):
        text = "\n".join(f"{message.get('role')}: {message.get('content')}" for message in messages)
        return len(tokenizer.encode(text, add_special_tokens=True))


def sample_identifier(sample: dict[str, Any], idx: int) -> str:
    metadata = sample.get("metadata") if isinstance(sample.get("metadata"), dict) else {}
    return str(
        sample.get("sample_id")
        or sample.get("raw_id")
        or sample.get("trajectory_id")
        or metadata.get("source_sample_id")
        or metadata.get("instance_id")
        or idx
    )


def failure(
    source_sample: SourceSample,
    *,
    sample_id: str,
    reason: str,
    token_count: int = 0,
    message: str = "",
) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "reason": reason,
        "token_count": token_count,
        "source": source_sample.source,
        "line": source_sample.line,
        "message": message,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
