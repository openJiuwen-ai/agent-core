# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
skill_train ReflACT offline training example.

Thin wrapper over :func:`openjiuwen.agent_evolving.skill_train.run_offline_training`
(the same API the ``jiuwenswarm skill-train`` command calls).
Supports SearchQA / DocVQA / OfficeQA via ``--env`` (or ``SKILL_TRAIN_ENV``).

Target backends (``--backend`` / ``TARGET_BACKEND``):

- ``openai_chat`` (default): the target is a chat model (``TARGET_MODEL`` ...).
- ``jiuwenswarm_cli_exec``: the target is the jiuwenswarm coding agent driven
  through ``jiuwenswarm chat --jsonl`` in an isolated per-item workspace, with
  the event trace fed back to the optimizer. Requires a running Gateway
  (``jiuwenswarm-start app``) and normally is launched from the jiuwenswarm
  repo via ``jiuwenswarm skill-train --env searchqa``.

Prerequisites:
- Id-split manifests under repo-root ``data/``
  (``searchqa_id_split`` / ``docvqa_id_split`` / ``officeqa_id_split``).
  Dataloaders auto-materialize them into ``*_split`` caches on first run.
- Override with ``SKILL_TRAIN_DATA_ROOT`` / ``--data-root`` or per-env
  ``*_SPLIT_DIR`` when needed.
- Extra assets when required:
  - DocVQA / OfficeQA: auto-download into ``<repo>/data`` when missing
    (needs ``pyarrow``; OfficeQA docs also need gated HF access via ``HF_TOKEN``)
  - If Hugging Face is slow/blocked, set ``HF_ENDPOINT=https://hf-mirror.com``.
- LLM credentials in repo-root ``.env`` (optimizer is always a chat model).

Run::

    uv run python examples/agent_evolving/skill_reflact_train_example.py --env searchqa
    uv run python examples/agent_evolving/skill_reflact_train_example.py --env docvqa
    uv run python examples/agent_evolving/skill_reflact_train_example.py --env officeqa
    TARGET_BACKEND=jiuwenswarm_cli_exec uv run python examples/agent_evolving/skill_reflact_train_example.py --env searchqa
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from openjiuwen.agent_evolving.skill_train import (
    SUPPORTED_ENVS,
    SUPPORTED_TARGET_BACKENDS,
    TrainLaunchOptions,
    run_offline_training,
)
from openjiuwen.core.common.logging import llm_logger, logger


def _configure_logging() -> None:
    quiet = {
        "level": "WARNING",
        "output": ["console"],
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }
    llm_logger.reconfigure(quiet)
    logger.reconfigure(
        {
            "level": os.getenv("SKILL_TRAIN_LOG_LEVEL", "INFO"),
            "output": ["console"],
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        }
    )


def _load_env(repo_root: Path) -> None:
    seen: set[Path] = set()
    for path in (Path.cwd() / ".env", repo_root / ".env"):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        load_dotenv(resolved, override=False)
        seen.add(resolved)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run skill_train ReflACT offline training.")
    parser.add_argument(
        "--env",
        choices=SUPPORTED_ENVS,
        default=os.getenv("SKILL_TRAIN_ENV", "searchqa"),
        help="benchmark environment",
    )
    parser.add_argument(
        "--backend",
        choices=sorted(SUPPORTED_TARGET_BACKENDS),
        default=os.getenv("TARGET_BACKEND", "openai_chat"),
        help="target backend (chat model or jiuwenswarm CLI exec harness)",
    )
    parser.add_argument("--data-root", default="", help="override SKILL_TRAIN_DATA_ROOT")
    parser.add_argument("--output-dir", default="", help="override SKILL_TRAIN_OUTPUT")
    parser.add_argument("--limit", type=int, default=None, help="cap items per rollout (debug)")
    parser.add_argument("--num-epochs", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--exec-timeout", type=int, default=0)
    parser.add_argument("--gateway-url", default="", help="jiuwenswarm Gateway URL (exec backend)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    _load_env(repo_root)
    _configure_logging()

    opts = TrainLaunchOptions(
        env_name=args.env,
        target_backend=args.backend,
        data_root=args.data_root,
        output_dir=args.output_dir,
        limit=args.limit,
        num_epochs=args.num_epochs,
        batch_size=args.batch_size,
        workers=args.workers,
        exec_timeout=args.exec_timeout,
        jiuwenswarm_gateway_url=args.gateway_url,
    )
    result = run_offline_training(opts)
    print(
        f"Training complete. env={args.env} backend={args.backend} "
        f"best_score={result.best_score:.4f} output={result.output_dir}"
    )


if __name__ == "__main__":
    main()
