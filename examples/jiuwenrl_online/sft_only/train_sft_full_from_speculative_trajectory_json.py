#!/usr/bin/env python3
# coding: utf-8

"""Run full-parameter SFT directly from speculative trajectory samples."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from train_sft_from_speculative_trajectory_json import (
    DEFAULT_MODEL_PATH,
    device_name_from_env,
    load_payload,
    normalize_samples,
    token_prefix_stats,
    write_parquet,
)

from transformers import AutoTokenizer


SCRIPT_DIR = Path(__file__).resolve().parent
JIUWENRL_ROOT = SCRIPT_DIR.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare speculative trajectories and launch full-parameter verl SFT.",
    )
    parser.add_argument(
        "samples_json",
        nargs="?",
        default=str(SCRIPT_DIR / "speculative_sft_sample_trajectory.json"),
        help="JSON file containing {'samples': [...]} or a non-empty list.",
    )
    parser.add_argument("--student-model-path", default=os.getenv("STUDENT_MODEL_PATH") or os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH))
    parser.add_argument("--output-dir", default=str(JIUWENRL_ROOT / "records" / "speculative_sft_full"))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "4,5,6,7"))
    parser.add_argument("--nproc-per-node", type=int, default=0)
    parser.add_argument("--max-length", type=int, default=int(os.getenv("SFT_FULL_MAX_LENGTH", "4096")))
    parser.add_argument(
        "--max-token-len-per-gpu",
        type=int,
        default=int(os.getenv("SFT_FULL_MAX_TOKEN_LEN_PER_GPU", os.getenv("SFT_FULL_MAX_LENGTH", "4096"))),
    )
    parser.add_argument("--train-batch-size", type=int, default=int(os.getenv("SFT_FULL_TRAIN_BATCH_SIZE", "0")))
    parser.add_argument(
        "--micro-batch-size-per-gpu",
        type=int,
        default=int(os.getenv("SFT_FULL_MICRO_BATCH_SIZE_PER_GPU", "1")),
    )
    parser.add_argument("--epochs", type=int, default=int(os.getenv("SFT_FULL_TOTAL_EPOCHS", "1")))
    parser.add_argument("--learning-rate", default=os.getenv("SFT_FULL_LR", "1e-6"))
    parser.add_argument("--ulysses-sp", type=int, default=int(os.getenv("SFT_FULL_ULYSSES_SP", "1")))
    parser.add_argument("--dtype", default=os.getenv("SFT_FULL_DTYPE", "bfloat16"), choices=("bfloat16", "float16"))
    parser.add_argument("--model-dtype", default=os.getenv("SFT_FULL_MODEL_DTYPE", os.getenv("SFT_FULL_DTYPE", "bfloat16")))
    parser.add_argument(
        "--train-layer-spec",
        default=os.getenv("SFT_FULL_TRAIN_LAYER_SPEC", "all"),
        help="all, last:N, first:N, layers:i,j,k, or layers:start-end. Still full-param SFT, not LoRA.",
    )
    parser.add_argument("--fsdp-size", type=int, default=int(os.getenv("SFT_FULL_FSDP_SIZE", "-1")))
    parser.add_argument("--use-orig-params", default=os.getenv("SFT_FULL_USE_ORIG_PARAMS", "0"))
    parser.add_argument("--use-torch-compile", default=os.getenv("SFT_FULL_USE_TORCH_COMPILE", "1"))
    parser.add_argument("--param-offload", action="store_true", default=os.getenv("SFT_FULL_PARAM_OFFLOAD", "0") == "1")
    parser.add_argument(
        "--optimizer-offload",
        action="store_true",
        default=os.getenv("SFT_FULL_OPTIMIZER_OFFLOAD", "0") == "1",
    )
    parser.add_argument(
        "--activation-offload",
        action="store_true",
        default=os.getenv("SFT_FULL_ACTIVATION_OFFLOAD", "0") == "1",
    )
    parser.add_argument("--trust-remote-code", action="store_true", default=os.getenv("SFT_FULL_TRUST_REMOTE_CODE", "1") != "0")
    parser.add_argument("--save-hf-model", default=os.getenv("SFT_FULL_SAVE_HF_MODEL", "1"))
    parser.add_argument("--prepare-only", action="store_true", help="Only write parquet/stats; do not launch torchrun.")
    parser.add_argument("--keep-workdir", action="store_true", help="Kept for parity with LoRA SFT entrypoint.")
    return parser.parse_args()


def _bool_text(value: str | bool) -> str:
    if isinstance(value, bool):
        return str(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _decode_token_ids(tokenizer: Any, value: Any) -> str:
    if not isinstance(value, list) or not value:
        return ""
    try:
        token_ids = [int(item) for item in value]
    except (TypeError, ValueError):
        return ""
    return tokenizer.decode(token_ids, skip_special_tokens=False)


def _trajectory_text(trajectory: dict[str, Any], text_key: str, ids_key: str, tokenizer: Any) -> str:
    text = str(trajectory.get(text_key) or "")
    if text:
        return text
    return _decode_token_ids(tokenizer, trajectory.get(ids_key))


def normalize_full_sft_samples(samples: list[dict[str, Any]], tokenizer: Any) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        trajectory = sample.get("trajectory")
        if isinstance(trajectory, dict) and (
            trajectory.get("prompt_text") is not None
            or trajectory.get("response_text") is not None
            or trajectory.get("prompt_ids") is not None
            or trajectory.get("response_ids") is not None
        ):
            prompt_text = _trajectory_text(trajectory, "prompt_text", "prompt_ids", tokenizer)
            response_text = _trajectory_text(trajectory, "response_text", "response_ids", tokenizer)
            prompt_token_count = len(trajectory.get("prompt_ids") or [])
            response_token_count = len(trajectory.get("response_ids") or [])
            converted.append(
                {
                    "sample_id": sample.get("sample_id") or f"full-sft-trajectory-{idx}",
                    "session_id": sample.get("session_id") or "trajectory",
                    "turn_num": sample.get("turn_num") or idx + 1,
                    "tools": [],
                    "enable_thinking": False,
                    "messages": [
                        {"role": "user", "content": prompt_text, "loss_mask": 0},
                        {"role": "assistant", "content": response_text, "loss_mask": 1},
                    ],
                    "small": {"text": ""},
                    "large": {"message": {"role": "assistant", "content": response_text}},
                    "trajectory_token_counts": {
                        "prompt_ids": prompt_token_count,
                        "response_ids": response_token_count,
                    },
                }
            )
            continue
        converted.append(sample)
    return normalize_samples(converted)


def validate_parallel_config(args: argparse.Namespace, nproc: int) -> dict[str, Any]:
    config_path = Path(args.student_model_path) / "config.json"
    if not config_path.exists():
        return {"warning": f"missing model config: {config_path}"}
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    text_cfg = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    heads = int(text_cfg.get("num_attention_heads") or 0)
    layers = int(text_cfg.get("num_hidden_layers") or 0)
    if args.ulysses_sp > 1:
        if nproc % args.ulysses_sp != 0:
            raise ValueError(f"nproc_per_node={nproc} must be divisible by ulysses_sp={args.ulysses_sp}")
        if heads and heads % args.ulysses_sp != 0:
            raise ValueError(
                f"num_attention_heads={heads} must be divisible by ulysses_sp={args.ulysses_sp}; "
                "choose a divisor of the model attention heads"
            )
    if args.fsdp_size > 0 and nproc % args.fsdp_size != 0:
        raise ValueError(f"nproc_per_node={nproc} must be divisible by fsdp_size={args.fsdp_size}")
    return {
        "model_type": cfg.get("model_type"),
        "num_hidden_layers": layers,
        "num_attention_heads": heads,
        "ulysses_sp": args.ulysses_sp,
        "fsdp_size": args.fsdp_size,
    }


def build_torchrun_cmd(args: argparse.Namespace, parquet_path: Path, ckpt_dir: Path, train_batch_size: int, nproc: int) -> list[str]:
    checkpoint_save_contents = "[model,optimizer,extra]"
    if _bool_text(args.save_hf_model):
        checkpoint_save_contents = "[model,optimizer,extra,hf_model]"

    return [
        "torchrun",
        "--standalone",
        "--nnodes=1",
        f"--nproc_per_node={nproc}",
        str(SCRIPT_DIR / "verl_sft_full_finetune_entry.py"),
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
        "model.lora_rank=0",
        f"engine.ulysses_sequence_parallel_size={args.ulysses_sp}",
        f"engine.dtype={args.dtype}",
        f"engine.model_dtype={args.model_dtype}",
        f"engine.fsdp_size={args.fsdp_size}",
        f"engine.use_orig_params={_bool_text(args.use_orig_params)}",
        f"engine.use_torch_compile={_bool_text(args.use_torch_compile)}",
        f"engine.param_offload={str(args.param_offload)}",
        f"engine.optimizer_offload={str(args.optimizer_offload)}",
        f"optim.lr={args.learning_rate}",
        f"checkpoint.save_contents={checkpoint_save_contents}",
        "trainer.logger=[console]",
        f"trainer.total_epochs={args.epochs}",
        "trainer.save_freq=-1",
        "trainer.test_freq=-1",
        "trainer.resume_mode=disable",
        f"trainer.default_local_dir={ckpt_dir}",
        "trainer.project_name=speculative-sft-full",
        f"trainer.experiment_name={ckpt_dir.name}",
        f"trainer.device={device_name_from_env()}",
    ]


def main() -> None:
    args = parse_args()
    gpu_ids = [item.strip() for item in args.train_gpu.split(",") if item.strip()]
    nproc = args.nproc_per_node or len(gpu_ids) or 1
    train_batch_size = args.train_batch_size or nproc
    if train_batch_size % nproc != 0:
        raise ValueError(f"train_batch_size={train_batch_size} must be divisible by nproc_per_node={nproc}")

    from datetime import datetime

    samples_path = Path(args.samples_json).resolve()
    output_root = Path(args.output_dir).resolve()
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    work_dir = output_root / run_id
    parquet_path = work_dir / "train.parquet"
    stats_path = work_dir / "speculative_stats.json"
    ckpt_dir = work_dir / "checkpoints"

    parallel_config = validate_parallel_config(args, nproc)
    tokenizer = AutoTokenizer.from_pretrained(args.student_model_path, trust_remote_code=args.trust_remote_code)
    rows = normalize_full_sft_samples(load_payload(samples_path), tokenizer)
    stats = token_prefix_stats(rows, tokenizer)
    trajectory_token_counts = [
        (sample.get("metadata") or {}).get("trajectory_token_counts")
        for sample in rows
        if isinstance((sample.get("metadata") or {}).get("trajectory_token_counts"), dict)
    ]
    stats.update(
        {
            "physical_samples": len(rows),
            "student_model_path": args.student_model_path,
            "sft_mode": "full_parameter",
            "train_layer_spec": args.train_layer_spec,
            "save_hf_model": _bool_text(args.save_hf_model),
            "parallel_config": parallel_config,
            "trajectory_token_counts": trajectory_token_counts,
            "note": (
                "SFT labels are large-model text retokenized with the student tokenizer. "
                "SFT_FULL_TRAIN_LAYER_SPEC controls which full-parameter transformer layers remain trainable."
            ),
        }
    )

    written_rows = write_parquet(rows, parquet_path, min_rows=train_batch_size)
    stats["parquet_rows"] = written_rows
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[spec-full-sft] samples={samples_path}")
    print(f"[spec-full-sft] parquet={parquet_path} rows={written_rows}")
    print(f"[spec-full-sft] stats={stats_path}")
    print(f"[spec-full-sft] train_layer_spec={args.train_layer_spec}")
    print(f"[spec-full-sft] parallel_config={parallel_config}")

    if args.prepare_only:
        print("[spec-full-sft] prepare-only; skip torchrun")
        return

    torchrun = shutil.which("torchrun")
    if not torchrun:
        raise RuntimeError("torchrun not found in PATH after activating the training environment")

    visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
    os.environ[visible_devices_env] = args.train_gpu
    os.environ["ONLINE_RL_VISIBLE_DEVICES_ENV"] = visible_devices_env
    os.environ["SFT_FULL_TRAIN_LAYER_SPEC"] = args.train_layer_spec

    cmd = build_torchrun_cmd(args, parquet_path, ckpt_dir, train_batch_size, nproc)
    print("[spec-full-sft] launch:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
