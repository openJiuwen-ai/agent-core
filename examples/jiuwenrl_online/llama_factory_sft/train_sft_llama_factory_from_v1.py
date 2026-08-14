"""Train LoRA SFT with LLaMA-Factory from V1/sft-sample trajectory data."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
JIUWENRL_ROOT = SCRIPT_DIR.parent
AGENT_CORE_ROOT = JIUWENRL_ROOT.parents[1]

if str(AGENT_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE_ROOT))

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.llama_factory import (
    LLaMAFactoryTrainConfig,
    load_sft_samples_from_path,
    prepare_llama_factory_sft_run,
    run_llama_factory_train_cli,
)

DEFAULT_SFT_DATA = "/data1/lll/workspace/swe-dataset/selected_datasets_llamav1"
DEFAULT_MODEL_PATH = "/data1/lll/models/Qwen3-4B-Instruct-2507"
DEFAULT_OUTPUT_ROOT = "/data1/lll/workspace/swe-dataset/llama_factory_sft_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="?", default=os.getenv("SFT_V1_DATA_PATH", DEFAULT_SFT_DATA))
    parser.add_argument("--model-path", default=os.getenv("STUDENT_MODEL_PATH", os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH)))
    parser.add_argument("--output-root", default=os.getenv("SFT_LLAMAFACTORY_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "4,5,6,7"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_SAMPLE_LIMIT", "1")))
    parser.add_argument("--cutoff-len", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_CUTOFF_LEN", "32768")))
    parser.add_argument("--template", default=os.getenv("SFT_LLAMAFACTORY_TEMPLATE", "qwen"))
    parser.add_argument("--epochs", type=float, default=float(os.getenv("SFT_LLAMAFACTORY_EPOCHS", "1")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("SFT_LLAMAFACTORY_MAX_STEPS", "-1")))
    parser.add_argument("--learning-rate", default=os.getenv("SFT_LLAMAFACTORY_LR", "1e-5"))
    parser.add_argument("--lora-rank", type=int, default=int(os.getenv("SFT_LORA_RANK", "16")))
    parser.add_argument("--lora-alpha", type=int, default=int(os.getenv("SFT_LORA_ALPHA", "32")))
    parser.add_argument("--lora-target", default=os.getenv("SFT_LORA_TARGET", os.getenv("SFT_TARGET_MODULES", "all")))
    parser.add_argument("--prepare-only", action="store_true", help="Write LLaMA-Factory files without launching training.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = Path(args.output_root).resolve() / datetime.now(tz=UTC).strftime("run_%Y%m%d_%H%M%S")
    dataset_dir = run_dir / "dataset"
    output_dir = run_dir / "lora"

    samples = load_sft_samples_from_path(args.samples, limit=args.limit)
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
    paths = prepare_llama_factory_sft_run(samples, dataset_dir=dataset_dir, train_config=train_config)

    print(f"[llama-factory-sft] source={Path(args.samples).resolve()}")
    print(f"[llama-factory-sft] samples_loaded={len(samples)}")
    print(f"[llama-factory-sft] train_file={paths.train_file}")
    print(f"[llama-factory-sft] dataset_info={paths.dataset_info_file}")
    print(f"[llama-factory-sft] train_yaml={paths.train_yaml_file}")
    print(f"[llama-factory-sft] stats={paths.stats_file}")
    print(f"[llama-factory-sft] output_dir={output_dir}")

    if args.prepare_only:
        print("[llama-factory-sft] prepare-only; skip training")
        return

    run_llama_factory_train_cli(paths.train_yaml_file, run_dir=run_dir, training_gpu_ids=args.train_gpu)


if __name__ == "__main__":
    main()
