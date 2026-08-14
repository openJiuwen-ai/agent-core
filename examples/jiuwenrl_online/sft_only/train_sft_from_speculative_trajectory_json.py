#!/usr/bin/env python3
# coding: utf-8

"""Run hard-label SFT directly from speculative trajectory samples.

This entrypoint is intentionally separate from online PPO:
- teacher/large model output text is the SFT target;
- small model draft output is kept as metadata for mismatch analysis;
- no reward, logprob, old-policy, or reference-policy tensors are required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
JIUWENRL_ROOT = SCRIPT_DIR.parent
AGENT_CORE_ROOT = JIUWENRL_ROOT.parents[1]
WORKSPACE_ROOT = AGENT_CORE_ROOT.parent

for path in (AGENT_CORE_ROOT, WORKSPACE_ROOT / "jiuwenclaw"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from transformers import AutoTokenizer  # noqa: E402


DEFAULT_MODEL_PATH = "/data1/lll/models/Qwen3-4B-Thinking-2507"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare speculative trajectories and launch verl SFT.",
    )
    parser.add_argument(
        "samples_json",
        nargs="?",
        default=str(SCRIPT_DIR / "speculative_sft_sample_trajectory.json"),
        help="JSON file containing {'samples': [...]} or a non-empty list.",
    )
    parser.add_argument("--student-model-path", default=os.getenv("STUDENT_MODEL_PATH") or os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH))
    parser.add_argument("--output-dir", default=str(JIUWENRL_ROOT / "records" / "speculative_sft"))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "4,5,6,7"))
    parser.add_argument("--nproc-per-node", type=int, default=0)
    parser.add_argument("--max-length", type=int, default=int(os.getenv("SFT_MAX_LENGTH", "4096")))
    parser.add_argument("--max-token-len-per-gpu", type=int, default=int(os.getenv("SFT_MAX_TOKEN_LEN_PER_GPU", "4096")))
    parser.add_argument("--train-batch-size", type=int, default=int(os.getenv("SFT_TRAIN_BATCH_SIZE", "0")))
    parser.add_argument("--micro-batch-size-per-gpu", type=int, default=int(os.getenv("SFT_MICRO_BATCH_SIZE_PER_GPU", "1")))
    parser.add_argument("--epochs", type=int, default=int(os.getenv("SFT_TOTAL_EPOCHS", "1")))
    parser.add_argument("--save-freq", type=int, default=int(os.getenv("SFT_SAVE_FREQ", "-1")))
    parser.add_argument("--learning-rate", default=os.getenv("SFT_LR", "1e-5"))
    parser.add_argument("--lora-rank", type=int, default=int(os.getenv("SFT_LORA_RANK", "16")))
    parser.add_argument("--lora-alpha", type=int, default=int(os.getenv("SFT_LORA_ALPHA", "32")))
    parser.add_argument("--target-modules", default=os.getenv("SFT_TARGET_MODULES", "all-linear"))
    parser.add_argument("--ulysses-sp", type=int, default=int(os.getenv("SFT_ULYSSES_SP", "1")))
    parser.add_argument("--dtype", default=os.getenv("SFT_DTYPE", "bfloat16"), choices=("bfloat16", "float16"))
    parser.add_argument("--param-offload", action="store_true", default=os.getenv("SFT_PARAM_OFFLOAD", "0") == "1")
    parser.add_argument("--optimizer-offload", action="store_true", default=os.getenv("SFT_OPTIMIZER_OFFLOAD", "0") == "1")
    parser.add_argument("--activation-offload", action="store_true", default=os.getenv("SFT_ACTIVATION_OFFLOAD", "0") == "1")
    parser.add_argument("--trust-remote-code", action="store_true", default=os.getenv("SFT_TRUST_REMOTE_CODE", "1") != "0")
    parser.add_argument("--export-lora-adapter", action="store_true", default=os.getenv("SFT_EXPORT_LORA_ADAPTER", "1") != "0")
    parser.add_argument(
        "--export-on-interrupt",
        action="store_true",
        default=os.getenv("SFT_EXPORT_ON_INTERRUPT", "1") != "0",
        help="Try exporting the latest checkpoint when torchrun is interrupted.",
    )
    parser.add_argument("--prepare-only", action="store_true", help="Only write parquet/stats; do not launch torchrun.")
    parser.add_argument("--keep-workdir", action="store_true", help="Keep generated parquet/config artifacts.")
    return parser.parse_args()


def load_payload(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("samples") or payload.get("trajectories") or payload.get("data")
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path} must contain a non-empty list or a dict with a non-empty samples list")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("all samples must be JSON objects")
    return payload


def _as_message(value: Any, *, default_role: str = "assistant", loss_mask: int | None = None) -> dict[str, Any]:
    if isinstance(value, dict):
        msg = dict(value)
    else:
        msg = {"role": default_role, "content": "" if value is None else str(value)}
    msg.setdefault("role", default_role)
    msg.setdefault("content", "")
    if loss_mask is not None:
        msg["loss_mask"] = int(loss_mask)
    return msg


def _large_message(sample: dict[str, Any]) -> dict[str, Any] | None:
    large = sample.get("large") or sample.get("teacher") or sample.get("target")
    if isinstance(large, dict):
        if isinstance(large.get("message"), dict):
            return _as_message(large["message"], loss_mask=1)
        if isinstance(large.get("output"), dict):
            return _as_message(large["output"], loss_mask=1)
        if large.get("text") is not None:
            return _as_message(large.get("text"), loss_mask=1)
    if sample.get("large_output") is not None:
        return _as_message(sample.get("large_output"), loss_mask=1)
    response = sample.get("response")
    if isinstance(response, dict):
        msg = response.get("message", response)
        if isinstance(msg, dict):
            return _as_message(msg, loss_mask=1)
    return None


def _small_text(sample: dict[str, Any]) -> str:
    small = sample.get("small") or sample.get("student") or sample.get("draft")
    if isinstance(small, dict):
        text = small.get("text") or small.get("content") or small.get("output")
        return "" if text is None else str(text)
    if sample.get("small_output") is not None:
        return str(sample["small_output"])
    return ""


def _message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _default_loss_masks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        item = dict(msg)
        if "loss_mask" not in item:
            item["loss_mask"] = 1 if item.get("role") == "assistant" else 0
        out.append(item)
    return out


def _merge_prefix_messages_for_verl(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make the first supervised turn match verl's system/user/assistant schema."""

    first_assistant = next((idx for idx, msg in enumerate(messages) if msg.get("role") == "assistant"), -1)
    if first_assistant <= 0:
        return messages

    prefix = messages[:first_assistant]
    suffix = messages[first_assistant:]
    system_msg = prefix[0] if prefix and prefix[0].get("role") == "system" else None
    user_parts = []
    for msg in prefix[1:] if system_msg is not None else prefix:
        text = _message_text(msg).strip()
        if text:
            user_parts.append(text)
    user_msg = {"role": "user", "content": "\n\n".join(user_parts), "loss_mask": 0}
    if system_msg is not None:
        return [system_msg, user_msg, *suffix]
    return [user_msg, *suffix]


def _parquet_safe(value: Any) -> Any:
    """Remove empty nested dict fields that pyarrow cannot infer as structs."""

    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if item == {}:
                continue
            out[key] = _parquet_safe(item)
        return out
    if isinstance(value, list):
        return [_parquet_safe(item) for item in value]
    return value


def normalize_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        tools = sample.get("tools", [])
        enable_thinking = bool(sample.get("enable_thinking", False))
        metadata = {
            "sample_id": sample.get("sample_id") or f"spec-sft-{idx}",
            "session_id": sample.get("session_id") or "default",
            "turn_num": sample.get("turn_num") or idx + 1,
            "small_text": _small_text(sample),
        }
        if isinstance(sample.get("trajectory_token_counts"), dict):
            metadata["trajectory_token_counts"] = sample["trajectory_token_counts"]

        messages = sample.get("messages")
        if isinstance(messages, list) and messages:
            norm_messages = [_as_message(msg) for msg in messages]
            if not any(msg.get("loss_mask") == 1 for msg in norm_messages):
                large_msg = _large_message(sample)
                if large_msg is None:
                    raise ValueError(f"sample[{idx}] has messages but no assistant target with loss_mask=1")
                norm_messages.append(large_msg)
            norm_messages = _merge_prefix_messages_for_verl(norm_messages)
            rows.append({
                "messages": _default_loss_masks(norm_messages),
                "tools": tools,
                "enable_thinking": enable_thinking,
                "metadata": metadata,
            })
            continue

        context = sample.get("context_messages") or sample.get("prompt_messages") or sample.get("prompt")
        if isinstance(context, str):
            context_messages = [{"role": "user", "content": context, "loss_mask": 0}]
        elif isinstance(context, list):
            context_messages = [_as_message(msg, loss_mask=0) for msg in context]
        else:
            raise ValueError(f"sample[{idx}] must provide messages, context_messages, prompt_messages, or prompt")

        large_msg = _large_message(sample)
        if large_msg is None:
            raise ValueError(f"sample[{idx}] must provide large.message, large.text, or response.message")

        rows.append({
            "messages": context_messages + [large_msg],
            "tools": tools,
            "enable_thinking": enable_thinking,
            "metadata": metadata,
        })
    return rows


def token_prefix_stats(rows: list[dict[str, Any]], tokenizer) -> dict[str, Any]:
    per_sample = []
    accepted_total = 0
    large_total = 0
    for row in rows:
        meta = row.get("metadata") or {}
        small_text = str(meta.get("small_text") or "")
        large_text = ""
        for msg in row["messages"]:
            if msg.get("role") == "assistant" and int(msg.get("loss_mask", 0)) == 1:
                large_text += _message_text(msg)
        small_ids = tokenizer.encode(small_text, add_special_tokens=False) if small_text else []
        large_ids = tokenizer.encode(large_text, add_special_tokens=False) if large_text else []
        prefix = 0
        for a, b in zip(small_ids, large_ids):
            if a != b:
                break
            prefix += 1
        first_mismatch = None
        if prefix < min(len(small_ids), len(large_ids)) or len(small_ids) != len(large_ids):
            first_mismatch = prefix
        accepted_total += prefix
        large_total += len(large_ids)
        per_sample.append({
            "sample_id": meta.get("sample_id"),
            "student_tokenizer_small_len": len(small_ids),
            "student_tokenizer_large_len": len(large_ids),
            "student_tokenizer_accepted_prefix_len": prefix,
            "student_tokenizer_first_mismatch_pos": first_mismatch,
            "student_tokenizer_acceptance_rate": prefix / max(len(large_ids), 1),
        })
    return {
        "tokenizer": getattr(tokenizer, "name_or_path", ""),
        "comparison_basis": "student_tokenizer_text_retokenization",
        "overall_acceptance_rate": accepted_total / max(large_total, 1),
        "samples": per_sample,
    }


def write_parquet(rows: list[dict[str, Any]], parquet_path: Path, *, min_rows: int) -> int:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required to write parquet") from exc

    expanded = list(rows)
    while len(expanded) < min_rows:
        for row in rows:
            if len(expanded) >= min_rows:
                break
            dup = dict(row)
            meta = dict(dup.get("metadata") or {})
            meta["duplicated_for_distributed_sft"] = True
            dup["metadata"] = meta
            expanded.append(dup)

    dataframe = pd.DataFrame([_parquet_safe(row) for row in expanded])
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_parquet(parquet_path)
    return len(expanded)


def device_name_from_env() -> str:
    backend = os.getenv("ONLINE_RL_DEVICE_BACKEND", "cuda").lower()
    if backend in {"ascend", "npu"}:
        return "npu"
    return "cuda"


def build_torchrun_cmd(args: argparse.Namespace, parquet_path: Path, ckpt_dir: Path, train_batch_size: int, nproc: int) -> list[str]:
    return [
        "torchrun",
        "--standalone",
        "--nnodes=1",
        f"--nproc_per_node={nproc}",
        "-m",
        "verl.trainer.sft_trainer",
        f"data.train_files={parquet_path}",
        "data.val_files=null",
        f"data.train_batch_size={train_batch_size}",
        f"data.micro_batch_size_per_gpu={args.micro_batch_size_per_gpu}",
        f"data.max_length={args.max_length}",
        f"data.max_token_len_per_gpu={args.max_token_len_per_gpu}",
        "data.pad_mode=no_padding",
        "data.truncation=error",
        "data.use_dynamic_bsz=True",
        f"model.path={args.student_model_path}",
        f"model.tokenizer_path={args.student_model_path}",
        f"model.trust_remote_code={str(args.trust_remote_code)}",
        "model.use_remove_padding=True",
        "model.enable_gradient_checkpointing=True",
        f"model.enable_activation_offload={str(args.activation_offload)}",
        f"model.lora_rank={args.lora_rank}",
        f"model.lora_alpha={args.lora_alpha}",
        f"model.target_modules={args.target_modules}",
        f"engine.ulysses_sequence_parallel_size={args.ulysses_sp}",
        f"engine.dtype={args.dtype}",
        f"engine.param_offload={str(args.param_offload)}",
        f"engine.optimizer_offload={str(args.optimizer_offload)}",
        f"optim.lr={args.learning_rate}",
        "trainer.logger=[console]",
        f"trainer.total_epochs={args.epochs}",
        f"trainer.save_freq={args.save_freq}",
        "trainer.test_freq=-1",
        "trainer.resume_mode=disable",
        f"trainer.default_local_dir={ckpt_dir}",
        f"trainer.project_name=speculative-sft",
        f"trainer.experiment_name={ckpt_dir.name}",
        f"trainer.device={device_name_from_env()}",
    ]


def _rank_from_checkpoint_name(path: Path) -> int:
    match = re.search(r"_rank_(\d+)\.pt$", path.name)
    return int(match.group(1)) if match else 0


def _target_modules(value: str) -> str | list[str]:
    normalized = value.strip()
    if not normalized or normalized == "all-linear":
        return "all-linear"
    return [item.strip() for item in normalized.split(",") if item.strip()]


def latest_checkpoint_dir(ckpt_dir: Path) -> Path:
    checkpoints = sorted(
        [path for path in ckpt_dir.glob("global_step_*") if path.is_dir()],
        key=lambda path: int(path.name.rsplit("_", 1)[-1]) if path.name.rsplit("_", 1)[-1].isdigit() else -1,
    )
    if not checkpoints:
        raise FileNotFoundError(f"no global_step checkpoint found under {ckpt_dir}")
    return checkpoints[-1]


def export_lora_adapter_from_fsdp_checkpoint(
    *,
    checkpoint_dir: Path,
    output_dir: Path,
    base_model_path: str,
    lora_rank: int,
    lora_alpha: int,
    target_modules: str,
) -> None:
    """Extract PEFT LoRA adapter files from verl FSDP checkpoint shards."""

    model_files = sorted(checkpoint_dir.glob("model_world_size_*_rank_*.pt"), key=_rank_from_checkpoint_name)
    if not model_files:
        raise FileNotFoundError(f"no FSDP model shards found under {checkpoint_dir}")

    import torch
    import torch.distributed.tensor  # noqa: F401
    from peft import LoraConfig, TaskType
    from safetensors.torch import save_file

    shard_parts: dict[str, list[tuple[int, int, Any]]] = {}
    for model_file in model_files:
        rank = _rank_from_checkpoint_name(model_file)
        state = torch.load(model_file, map_location="cpu", weights_only=False)
        for key, value in state.items():
            if ".lora_A." not in key and ".lora_B." not in key:
                continue
            dim = 0
            if hasattr(value, "placements") and value.placements:
                dim = int(getattr(value.placements[0], "dim", 0))
            local_tensor = value.to_local() if hasattr(value, "to_local") else value
            shard_parts.setdefault(key.replace(".default", ""), []).append((rank, dim, local_tensor.detach().cpu()))

    adapter_state = {}
    for key, parts in shard_parts.items():
        parts.sort(key=lambda item: item[0])
        dim = parts[0][1]
        adapter_state[key] = torch.cat([tensor for _, _, tensor in parts], dim=dim).contiguous()

    output_dir.mkdir(parents=True, exist_ok=True)
    config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_alpha,
        target_modules=_target_modules(target_modules),
        lora_dropout=0.0,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        base_model_name_or_path=base_model_path,
    )
    config.save_pretrained(output_dir)
    save_file(adapter_state, output_dir / "adapter_model.safetensors")
    print(f"[spec-sft] exported LoRA adapter={output_dir} tensors={len(adapter_state)}")


def main() -> None:
    args = parse_args()
    gpu_ids = [item.strip() for item in args.train_gpu.split(",") if item.strip()]
    nproc = args.nproc_per_node or len(gpu_ids) or 1
    train_batch_size = args.train_batch_size or nproc
    if train_batch_size % nproc != 0:
        raise ValueError(f"train_batch_size={train_batch_size} must be divisible by nproc_per_node={nproc}")

    samples_path = Path(args.samples_json).resolve()
    output_root = Path(args.output_dir).resolve()
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    work_dir = output_root / run_id
    parquet_path = work_dir / "train.parquet"
    stats_path = work_dir / "speculative_stats.json"
    ckpt_dir = work_dir / "checkpoints"

    rows = normalize_samples(load_payload(samples_path))
    tokenizer = AutoTokenizer.from_pretrained(args.student_model_path, trust_remote_code=args.trust_remote_code)
    stats = token_prefix_stats(rows, tokenizer)
    stats.update({
        "physical_samples": len(rows),
        "student_model_path": args.student_model_path,
        "note": (
            "SFT labels are large-model text retokenized with the student tokenizer. "
            "If small/large tokenizers differ, these acceptance stats are not exact runtime speculative acceptance."
        ),
    })

    written_rows = write_parquet(rows, parquet_path, min_rows=train_batch_size)
    stats["parquet_rows"] = written_rows
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[spec-sft] samples={samples_path}")
    print(f"[spec-sft] parquet={parquet_path} rows={written_rows}")
    print(f"[spec-sft] stats={stats_path}")

    if args.prepare_only:
        print("[spec-sft] prepare-only; skip torchrun")
        return

    torchrun = shutil.which("torchrun")
    if not torchrun:
        raise RuntimeError("torchrun not found in PATH after activating the training environment")

    visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
    os.environ[visible_devices_env] = args.train_gpu
    os.environ["ONLINE_RL_VISIBLE_DEVICES_ENV"] = visible_devices_env

    cmd = build_torchrun_cmd(args, parquet_path, ckpt_dir, train_batch_size, nproc)
    print("[spec-sft] launch:", " ".join(cmd))
    train_error: BaseException | None = None
    try:
        subprocess.run(cmd, check=True)
    except (KeyboardInterrupt, subprocess.CalledProcessError) as exc:
        train_error = exc
        print(f"[spec-sft] torchrun interrupted or failed: {exc!r}", file=sys.stderr)
    finally:
        should_export = args.export_lora_adapter and args.lora_rank > 0
        should_export = should_export and (train_error is None or args.export_on_interrupt)
        if should_export:
            try:
                export_lora_adapter_from_fsdp_checkpoint(
                    checkpoint_dir=latest_checkpoint_dir(ckpt_dir),
                    output_dir=output_root / "adapter",
                    base_model_path=args.student_model_path,
                    lora_rank=args.lora_rank,
                    lora_alpha=args.lora_alpha,
                    target_modules=args.target_modules,
                )
            except FileNotFoundError as exc:
                if train_error is None:
                    raise
                print(f"[spec-sft] no checkpoint available for interrupted export: {exc}", file=sys.stderr)
        if not args.keep_workdir:
            print(f"[spec-sft] artifacts kept under {work_dir}")
    if train_error is not None:
        raise train_error


if __name__ == "__main__":
    main()
