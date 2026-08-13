#!/usr/bin/env python3
# coding: utf-8

"""Run online PPO training directly from saved trajectory samples.

This bypasses WebSocket request generation and delayed judge scoring, while
still using the normal online PPO executor, LoRA repository, and vLLM hot-load
notifier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from copy import deepcopy
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

from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier  # noqa: E402
from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_executor import PPOTrainingExecutor  # noqa: E402
from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository  # noqa: E402


DEFAULT_MODEL_PATH = "/data1/lll/models/Qwen3-4B-Thinking-2507"
DEFAULT_MODEL_NAME = "Qwen3-4B-Thinking-2507"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trigger online PPO training from a saved trajectory JSON file.",
    )
    parser.add_argument(
        "samples_json",
        nargs="?",
        default=str(SCRIPT_DIR / "direct_train_trajectories.json"),
        help="JSON file containing a list of trajectory samples, or {'samples': [...]}",
    )
    parser.add_argument("--model-path", default=os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH))
    parser.add_argument("--model-name", default=os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME))
    parser.add_argument("--vllm-url", default=os.getenv("VLLM_URL", "http://127.0.0.1:18002"))
    parser.add_argument("--lora-repo", default=os.getenv("LORA_REPO", str(JIUWENRL_ROOT / "lora_repo")))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "6,7"))
    parser.add_argument("--user-id", default=os.getenv("DIRECT_TRAIN_USER_ID") or os.getenv("WEB_USER_ID", "local-web-user"))
    parser.add_argument("--ppo-config-path", default=os.getenv("PPO_CONFIG_PATH") or None)
    parser.add_argument(
        "--ppo-samples-per-step",
        type=int,
        default=int(os.getenv("ONLINE_RL_PPO_SAMPLES_PER_STEP", "0")),
        help="Samples per PPO train_step inside this run; 0 means all samples in one step.",
    )
    parser.add_argument("--training-count", type=int, default=int(os.getenv("DIRECT_TRAINING_COUNT", "1")))
    parser.add_argument("--tmp-root", default=str(JIUWENRL_ROOT / "records"))
    parser.add_argument("--no-hotload", action="store_true", help="Publish LoRA but do not call vLLM load_lora_adapter")
    return parser.parse_args()


def load_samples(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        payload = payload.get("samples")
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path} must contain a non-empty list or a dict with a non-empty 'samples' list")
    return [deepcopy(item) for item in payload]


def ensure_token_fields(
    samples: list[dict[str, Any]],
    *,
    model_path: str,
    model_name: str,
    user_id: str,
) -> list[dict[str, Any]]:
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    out = []
    for idx, sample in enumerate(samples):
        item = deepcopy(sample)
        item.setdefault("sample_id", f"direct-json-{idx}")
        item.setdefault("created_at", "")
        item.setdefault("user_id", user_id)
        item.setdefault("session_id", "direct_json_train")
        item.setdefault("turn_num", idx + 1)
        item.setdefault("mode", "fixture")
        item.setdefault("io_mode", "direct")
        item.setdefault("model", model_name)

        trajectory = item.setdefault("trajectory", {})
        if not isinstance(trajectory, dict):
            raise ValueError(f"sample[{idx}].trajectory must be an object")

        prompt_text = str(trajectory.get("prompt_text") or "")
        response_text = str(trajectory.get("response_text") or "")
        prompt_ids = trajectory.get("prompt_ids")
        response_ids = trajectory.get("response_ids")

        if not isinstance(prompt_ids, list) or not prompt_ids:
            if not prompt_text:
                raise ValueError(f"sample[{idx}] must provide trajectory.prompt_text or prompt_ids")
            prompt_ids = tokenizer.encode(prompt_text, add_special_tokens=False)
            trajectory["prompt_ids"] = prompt_ids
        if not isinstance(response_ids, list) or not response_ids:
            if not response_text:
                raise ValueError(f"sample[{idx}] must provide trajectory.response_text or response_ids")
            response_ids = tokenizer.encode(response_text, add_special_tokens=False)
            trajectory["response_ids"] = response_ids

        trajectory["input_ids"] = trajectory.get("input_ids") or (prompt_ids + response_ids)
        response_logprobs = trajectory.get("response_logprobs")
        if not isinstance(response_logprobs, list) or len(response_logprobs) < len(response_ids):
            trajectory["response_logprobs"] = [-0.1] * len(response_ids)

        judge = item.setdefault("judge", {})
        if not isinstance(judge, dict):
            raise ValueError(f"sample[{idx}].judge must be an object")
        judge.setdefault("score", 0.0)

        out.append(item)
    return out


async def train(args: argparse.Namespace) -> str | None:
    samples_path = Path(args.samples_json)
    samples = ensure_token_fields(
        load_samples(samples_path),
        model_path=args.model_path,
        model_name=args.model_name,
        user_id=args.user_id,
    )

    visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
    os.environ[visible_devices_env] = args.train_gpu
    os.environ["ONLINE_RL_VISIBLE_DEVICES_ENV"] = visible_devices_env

    train_gpus = [item for item in args.train_gpu.split(",") if item.strip()]
    notifier = None if args.no_hotload else InferenceNotifier(args.vllm_url)
    executor = PPOTrainingExecutor(
        base_model_path=args.model_path,
        lora_repo=LoRARepository(args.lora_repo),
        notifier=notifier,
        nproc_per_node=len(train_gpus) or 1,
        training_gpu_ids=args.train_gpu,
        ppo_config_path=args.ppo_config_path,
        ppo_samples_per_step=args.ppo_samples_per_step,
    )

    try:
        tmp_root = Path(args.tmp_root)
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="direct_ppo_", dir=str(tmp_root)) as tmp_dir:
            return await executor.train_batch(
                user_id=args.user_id,
                samples=samples,
                training_count=args.training_count,
                tmp_root=tmp_dir,
            )
    finally:
        await executor.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = parse_args()
    print(f"[direct-train] samples={args.samples_json} user={args.user_id} train_gpu={args.train_gpu}")
    lora_path = asyncio.run(train(args))
    print(f"[direct-train] published_lora={lora_path}")


if __name__ == "__main__":
    main()
