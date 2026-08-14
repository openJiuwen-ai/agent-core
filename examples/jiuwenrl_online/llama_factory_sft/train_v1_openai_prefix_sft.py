"""Offline prefix-split SFT training from V1 OpenAI-style agent data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
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
    convert_samples_to_llama_factory_openai_prefix,
    load_sft_samples_from_path,
    prepare_llama_factory_records_run,
    run_llama_factory_train_cli,
)

DEFAULT_V1_DATA = str(SCRIPT_DIR / "v1data+v0train" / "data_openai")
DEFAULT_OUTPUT_ROOT = "/data1/lll/workspace/swe-dataset/llama_factory_sft_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="?", default=os.getenv("SFT_V1_OPENAI_DATA_PATH", DEFAULT_V1_DATA))
    parser.add_argument("--model-path", default=os.getenv("STUDENT_MODEL_PATH", os.getenv("MODEL_PATH", "")))
    parser.add_argument("--output-root", default=os.getenv("SFT_LLAMAFACTORY_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "4,5,6,7"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_SAMPLE_LIMIT", "0")))
    parser.add_argument("--cutoff-len", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_CUTOFF_LEN", "32768")))
    parser.add_argument("--template", default=os.getenv("SFT_LLAMAFACTORY_TEMPLATE", ""))
    parser.add_argument("--epochs", type=float, default=float(os.getenv("SFT_LLAMAFACTORY_EPOCHS", "1")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_MAX_STEPS", "-1")))
    parser.add_argument("--learning-rate", default=os.getenv("SFT_LLAMAFACTORY_LR", "1e-5"))
    parser.add_argument("--lora-rank", type=int, default=int(os.getenv("SFT_LORA_RANK", "16")))
    parser.add_argument("--lora-alpha", type=int, default=int(os.getenv("SFT_LORA_ALPHA", "32")))
    parser.add_argument("--lora-target", default=os.getenv("SFT_LORA_TARGET", os.getenv("SFT_TARGET_MODULES", "all")))
    parser.add_argument("--scan-only", action="store_true", help="Write converted train data and reports without training.")
    parser.add_argument("--prepare-only", action="store_true", help="Alias for --scan-only.")
    parser.add_argument("--max-warning-lines", type=int, default=50)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.model_path:
        print("[v1-prefix-sft] missing --model-path or MODEL_PATH", file=sys.stderr)
        return 2

    run_dir = Path(args.output_root).resolve() / datetime.now(tz=UTC).strftime("run_%Y%m%d_%H%M%S")
    dataset_dir = run_dir / "dataset"
    output_dir = run_dir / "lora"
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    source_samples = load_sft_samples_from_path(args.samples, limit=args.limit)
    prefix_records = convert_samples_to_llama_factory_openai_prefix(source_samples)
    scan = scan_records(
        prefix_records,
        model_path=args.model_path,
        cutoff_len=args.cutoff_len,
        max_warning_lines=args.max_warning_lines,
    )
    write_json(report_dir / "scan_report.json", scan["summary"])
    write_json(report_dir / "failed_samples.json", scan["failed"])

    train_records = scan["records"]
    template = args.template.strip() or guess_template(args.model_path)
    train_config = LLaMAFactoryTrainConfig(
        model_name_or_path=args.model_path,
        output_dir=str(output_dir),
        cutoff_len=args.cutoff_len,
        template=template,
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_target=args.lora_target,
    )

    print(f"[v1-prefix-sft] source={Path(args.samples).resolve()}")
    print(f"[v1-prefix-sft] model={args.model_path} template={template} cutoff_len={args.cutoff_len}")
    print(
        "[v1-prefix-sft] "
        f"conversations={len(source_samples)} prefix_records={len(prefix_records)} "
        f"trainable={len(train_records)} failed={len(scan['failed'])}"
    )
    print(f"[v1-prefix-sft] scan_report={report_dir / 'scan_report.json'}")
    print(f"[v1-prefix-sft] failed_samples={report_dir / 'failed_samples.json'}")

    if not train_records:
        print("[v1-prefix-sft] no trainable records after prefix split and cutoff scan", file=sys.stderr)
        return 3

    paths = prepare_llama_factory_records_run(train_records, dataset_dir=dataset_dir, train_config=train_config)
    print(f"[v1-prefix-sft] train_file={paths.train_file}")
    print(f"[v1-prefix-sft] train_yaml={paths.train_yaml_file}")
    print(f"[v1-prefix-sft] output_dir={output_dir}")

    if args.scan_only or args.prepare_only:
        print("[v1-prefix-sft] scan/prepare mode; skip training")
        return 0

    run_llama_factory_train_cli(paths.train_yaml_file, run_dir=run_dir, training_gpu_ids=args.train_gpu)
    return 0


def scan_records(
    records: list[dict[str, Any]],
    *,
    model_path: str,
    cutoff_len: int,
    max_warning_lines: int,
) -> dict[str, Any]:
    tokenizer = load_tokenizer(model_path)
    train_records: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    token_lengths: list[int] = []

    for idx, record in enumerate(records):
        sample_id = record_identifier(record, idx)
        try:
            token_count = estimate_tokens(record, tokenizer=tokenizer)
        except (TypeError, ValueError, RuntimeError) as exc:
            failed.append(failure(record, sample_id=sample_id, reason="tokenize_failed", message=str(exc)))
            continue

        token_lengths.append(token_count)
        record.setdefault("metadata", {})
        record["metadata"]["token_count"] = token_count
        if token_count > cutoff_len:
            item = failure(
                record,
                sample_id=sample_id,
                reason="over_cutoff_len",
                token_count=token_count,
                message=f"token_count={token_count} cutoff_len={cutoff_len}",
            )
            failed.append(item)
            if len(failed) <= max_warning_lines:
                print(
                    "[v1-prefix-sft][WARN] skip overlength "
                    f"sample={sample_id} token_count={token_count} cutoff_len={cutoff_len}",
                    file=sys.stderr,
                )
            continue
        train_records.append(record)

    if len(failed) > max_warning_lines:
        print(
            "[v1-prefix-sft][WARN] "
            f"{len(failed) - max_warning_lines} additional failed records omitted from console; "
            "see failed_samples.json",
            file=sys.stderr,
        )

    summary = {
        "model_path": model_path,
        "cutoff_len": cutoff_len,
        "prefix_records": len(records),
        "trainable_records": len(train_records),
        "failed_records": len(failed),
        "overlength_records": sum(1 for item in failed if item["reason"] == "over_cutoff_len"),
        "max_token_count": max(token_lengths) if token_lengths else 0,
        "min_token_count": min(token_lengths) if token_lengths else 0,
        "failed_by_source": dict(Counter(str(item.get("source_sample_id") or "unknown") for item in failed)),
    }
    return {"records": train_records, "failed": failed, "summary": summary}


def load_tokenizer(model_path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)


def estimate_tokens(record: dict[str, Any], *, tokenizer: Any) -> int:
    messages = record.get("messages") or []
    tools = parse_tools(record.get("tools"))
    try:
        if tools:
            return len(
                tokenizer.apply_chat_template(
                    messages,
                    tools=tools,
                    tokenize=True,
                    add_generation_prompt=False,
                )
            )
        return len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False))
    except (KeyError, TypeError, ValueError, RuntimeError):
        text = "\n".join(f"{message.get('role')}: {message.get('content')}" for message in messages)
        if tools:
            text += "\nTOOLS: " + json.dumps(tools, ensure_ascii=False, sort_keys=True)
        return len(tokenizer.encode(text, add_special_tokens=True))


def parse_tools(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def record_identifier(record: dict[str, Any], idx: int) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    source_id = metadata.get("source_sample_id") or idx
    prefix_index = metadata.get("prefix_index")
    if prefix_index:
        return f"{source_id}:prefix-{prefix_index}"
    return str(source_id)


def failure(
    record: dict[str, Any],
    *,
    sample_id: str,
    reason: str,
    token_count: int = 0,
    message: str = "",
) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return {
        "sample_id": sample_id,
        "reason": reason,
        "token_count": token_count,
        "source_sample_id": metadata.get("source_sample_id"),
        "prefix_index": metadata.get("prefix_index"),
        "prefix_total": metadata.get("prefix_total"),
        "message": message,
    }


def guess_template(model_path: str) -> str:
    lowered = Path(model_path).name.lower().replace("_", "-")
    if "qwen3.5" in lowered or "qwen3-5" in lowered:
        return "qwen3_5"
    if "qwen3" in lowered:
        return "qwen3"
    return "qwen"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
