# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
skill_train ReflACT offline training example.

Supports SearchQA / DocVQA / OfficeQA via ``--env`` (or ``SKILL_TRAIN_ENV``).

Hyperparameters mirror skill_train / ReflACT defaults::

    configs / _base_ / default.yaml
    configs / {searchqa, docvqa, officeqa} / default.yaml

Prerequisites:
- Id-split manifests under ``openjiuwen/agent_evolving/skill_train/data/``
  (``searchqa_id_split`` / ``docvqa_id_split`` / ``officeqa_id_split``).
  Dataloaders auto-materialize them into ``*_split`` caches on first run.
- Override with ``SKILL_TRAIN_DATA_ROOT`` or per-env ``*_SPLIT_DIR`` when needed.
- Extra assets when required:
  - DocVQA / OfficeQA: auto-download into ``skill_train/data`` when missing
    (needs ``pyarrow``; OfficeQA docs also need gated HF access via ``HF_TOKEN``)
  - If Hugging Face is slow/blocked, set ``HF_ENDPOINT=https://hf-mirror.com``.
    Large DocVQA parquet still lands on HF CDN; the downloader uses
    multi-connection Range requests. Tune with
    ``SKILL_TRAIN_DOWNLOAD_CONNECTIONS`` (default 8) and
    ``SKILL_TRAIN_DOWNLOAD_WORKERS`` (default 3).
    If download still fails, place ``docvqa_images`` + ``docvqa_split``
    under ``skill_train/data`` manually.
- LLM credentials in repo-root ``.env``

Run::

    uv run python examples/agent_evolving/skill_reflact_train_example.py --env searchqa
    uv run python examples/agent_evolving/skill_reflact_train_example.py --env docvqa
    uv run python examples/agent_evolving/skill_reflact_train_example.py --env officeqa
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from openjiuwen.agent_evolving.skill_train import SkillReflACTTrainer, SkillTrainConfig, get_env_adapter
from openjiuwen.agent_evolving.skill_train.datasets.materialize import ensure_officeqa_docs
from openjiuwen.agent_evolving.skill_train.paths import resolve_data_path, skill_train_data_root
from openjiuwen.core.common.logging import llm_logger, logger
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model import Model

SUPPORTED_ENVS = (
    "searchqa",
    "docvqa",
    "officeqa",
)


@dataclass(frozen=True)
class EnvPreset:
    """skill_train-aligned defaults for one benchmark env."""

    env_name: str
    skill_relpath: str
    split_relpath: str
    train_size: int = 0  # 0 = derive from dataloader
    batch_size: int = 40
    accumulation: int = 1
    edit_budget: int = 4
    min_edit_budget: int = 2
    workers: int = 8
    analyst_workers: int = 8
    exec_timeout: int = 120
    max_completion_tokens: int = 16384
    env_kwargs: Dict[str, Any] = field(default_factory=dict)
    required_paths: tuple[str, ...] = ()


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
    candidates = (Path.cwd() / ".env", repo_root / ".env")
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        load_dotenv(resolved, override=False)
        seen.add(resolved)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _env_bool(name: str, default: str = "1") -> bool:
    return _env(name, default=default) not in {"0", "false", "False", "no", "NO"}


def _resolve_data_path(repo_root: Path, relpath: str, *env_names: str) -> str:
    return str(resolve_data_path(relpath, *env_names, repo_root=repo_root))


def _build_model(*, model_name: str, api_key: str, api_base: str, provider: str) -> Model:
    client_config = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
    )
    request_config = ModelRequestConfig(model=model_name)
    return Model(model_client_config=client_config, model_config=request_config)


def _presets(repo_root: Path) -> Dict[str, EnvPreset]:
    """Build skill_train-aligned presets with resolved local paths."""
    return {
        "searchqa": EnvPreset(
            env_name="searchqa",
            skill_relpath="openjiuwen/agent_evolving/skill_train/envs/searchqa/skills/initial.md",
            split_relpath="searchqa_id_split",
            train_size=400,
            workers=24,
            analyst_workers=16,
            exec_timeout=120,
            env_kwargs={
                "split_mode": "split_dir",
                "max_turns": 1,
                "limit": 0,
            },
        ),
        "docvqa": EnvPreset(
            env_name="docvqa",
            skill_relpath="openjiuwen/agent_evolving/skill_train/envs/docvqa/skills/initial.md",
            split_relpath="docvqa_id_split",
            train_size=0,
            workers=8,
            exec_timeout=120,
            env_kwargs={
                "split_mode": "split_dir",
                "max_turns": 1,
                "image_detail": "auto",
                "limit": 0,
            },
        ),
        "officeqa": EnvPreset(
            env_name="officeqa",
            skill_relpath="openjiuwen/agent_evolving/skill_train/envs/officeqa/skills/initial.md",
            split_relpath="officeqa_id_split",
            train_size=0,
            workers=8,
            analyst_workers=8,
            exec_timeout=120,
            env_kwargs={
                "split_mode": "split_dir",
                "max_tool_turns": 24,
                "search_mode": "offline",
                "max_queries_per_turn": 4,
                "search_api_url": _env(
                    "OFFICEQA_SEARCH_API_URL",
                    default="http://apisix.westus2.cloudapp.azure.com/search_tool/search",
                ),
                "search_auth_env": "OFFICEQA_CUSTOM_SEARCH_AUTH",
                "search_provider": _env("OFFICEQA_SEARCH_PROVIDER", default="duckduckgo"),
                "search_max_num_results": 4,
                "search_timeout_seconds": 20,
                "limit": 0,
            },
        ),
    }


def _prepare_officeqa_preset(repo_root: Path, preset: EnvPreset) -> EnvPreset:
    from dataclasses import replace

    ensured_docs = ensure_officeqa_docs()
    officeqa_docs = (
        str(ensured_docs)
        if ensured_docs is not None
        else _resolve_data_path(
            repo_root,
            "officeqa_docs_official",
            "OFFICEQA_DATA_DIRS",
            "OFFICEQA_DOCS_DIR",
        )
    )
    env_kwargs = dict(preset.env_kwargs)
    env_kwargs["data_dirs"] = [officeqa_docs]
    return replace(preset, env_kwargs=env_kwargs, required_paths=(officeqa_docs,))


def _parse_args(default_env: str = "") -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run skill_train ReflACT training on SearchQA/DocVQA/OfficeQA.")
    parser.add_argument(
        "--env",
        choices=SUPPORTED_ENVS,
        default=default_env or "searchqa",
        help="Benchmark env name (default: searchqa; or set SKILL_TRAIN_ENV).",
    )
    return parser.parse_args()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    _load_env(repo_root)
    _configure_logging()

    args = _parse_args(default_env=_env("SKILL_TRAIN_ENV"))
    env_name = args.env
    presets = _presets(repo_root)
    preset = presets[env_name]
    if env_name == "officeqa":
        preset = _prepare_officeqa_preset(repo_root, preset)

    skill_init = _env(
        "SKILL_INIT",
        default=str(repo_root / preset.skill_relpath),
    )
    split_dir = _resolve_data_path(
        repo_root,
        preset.split_relpath,
        f"{env_name.upper()}_SPLIT_DIR",
        "SPLIT_DIR",
    )

    provider = _env("MODEL_PROVIDER", "OPTIMIZER_PROVIDER", "TARGET_PROVIDER", default="openai")
    api_key = _env("API_KEY", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY")
    api_base = _env("API_BASE", "OPENAI_COMPATIBLE_BASE_URL")
    default_model = _env("MODEL_NAME", "OPENAI_COMPATIBLE_MODEL", default="GLM-5.2")
    optimizer_model = _env("OPTIMIZER_MODEL", default=default_model)
    target_model = _env("TARGET_MODEL", default=default_model)

    missing = [
        name
        for name, value in (
            ("API_KEY / OPENAI_COMPATIBLE_API_KEY", api_key),
            ("API_BASE / OPENAI_COMPATIBLE_BASE_URL", api_base),
            ("OPTIMIZER_MODEL / OPENAI_COMPATIBLE_MODEL", optimizer_model),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))

    if not Path(skill_init).is_file():
        raise SystemExit(f"skill_init not found: {skill_init}")
    if not Path(split_dir).exists():
        raise SystemExit(f"split_dir not found: {split_dir}")
    for req in preset.required_paths:
        if not Path(req).exists():
            raise SystemExit(
                f"Required data path missing for {env_name}: {req}\n"
                f"Expected under {skill_train_data_root()} "
                "(or set OFFICEQA_DATA_DIRS / SKILL_TRAIN_DATA_ROOT)."
            )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _env(
        "SKILL_TRAIN_OUTPUT",
        default=str(repo_root / f"outputs/skill_train_{env_name}_{target_model}_{timestamp}"),
    )

    exec_timeout = int(_env("EXEC_TIMEOUT", default=str(preset.exec_timeout)))
    workers = int(_env("WORKERS", default=str(preset.workers)))
    analyst_workers = int(_env("ANALYST_WORKERS", default=str(preset.analyst_workers)))
    llm_attempt_timeout = float(_env("LLM_ATTEMPT_TIMEOUT", default=str(exec_timeout)))
    llm_total_budget = float(_env("LLM_TOTAL_BUDGET", default=str(max(600.0, llm_attempt_timeout * 5))))
    llm_max_attempts = int(_env("LLM_MAX_ATTEMPTS", default="3"))

    env_kwargs: Dict[str, Any] = {
        "split_dir": split_dir,
        "workers": workers,
        "analyst_workers": analyst_workers,
        "exec_timeout": exec_timeout,
        "max_completion_tokens": int(_env("MAX_COMPLETION_TOKENS", default=str(preset.max_completion_tokens))),
        "minibatch_size": int(_env("MINIBATCH_SIZE", default="8")),
        "edit_budget": int(_env("EDIT_BUDGET", default=str(preset.edit_budget))),
        "seed": int(_env("SEED", default="42")),
        "failure_only": _env("FAILURE_ONLY", default="0") in {"1", "true", "True"},
    }
    env_kwargs.update(preset.env_kwargs)
    if _env("LIMIT"):
        env_kwargs["limit"] = int(_env("LIMIT"))
    if _env("MAX_TURNS") and "max_turns" in env_kwargs:
        env_kwargs["max_turns"] = int(_env("MAX_TURNS"))

    config = SkillTrainConfig(
        env_name=env_name,
        output_dir=output_dir,
        skill_init=skill_init,
        num_epochs=int(_env("NUM_EPOCHS", default="4")),
        train_size=int(_env("TRAIN_SIZE", default=str(preset.train_size))),
        batch_size=int(_env("BATCH_SIZE", default=str(preset.batch_size))),
        accumulation=int(_env("ACCUMULATION", default=str(preset.accumulation))),
        minibatch_size=int(_env("MINIBATCH_SIZE", default="8")),
        merge_batch_size=int(_env("MERGE_BATCH_SIZE", default="8")),
        analyst_workers=analyst_workers,
        edit_budget=int(_env("EDIT_BUDGET", default=str(preset.edit_budget))),
        min_edit_budget=int(_env("MIN_EDIT_BUDGET", default=str(preset.min_edit_budget))),
        lr_scheduler=_env("LR_SCHEDULER", default="cosine"),
        failure_only=env_kwargs["failure_only"],
        use_gate=_env_bool("USE_GATE", default="1"),
        seed=int(_env("SEED", default="42")),
        skill_update_mode=_env("SKILL_UPDATE_MODE", default="patch"),
        use_slow_update=_env_bool("USE_SLOW_UPDATE", default="1"),
        slow_update_samples=int(_env("SLOW_UPDATE_SAMPLES", default="20")),
        slow_update_gate_with_selection=_env_bool("SLOW_UPDATE_GATE_WITH_SELECTION", default="0"),
        longitudinal_pair_policy=_env("LONGITUDINAL_PAIR_POLICY", default="mixed"),
        use_meta_skill=_env_bool("USE_META_SKILL", default="1"),
        reasoning_effort=_env("REASONING_EFFORT", default="medium") or None,
        env_kwargs=env_kwargs,
    )

    logger.info(
        "[skill_reflact_train] env=%s split_dir=%s train_size=%s batch_size=%s workers=%s exec_timeout=%s output=%s",
        env_name,
        split_dir,
        config.train_size,
        config.batch_size,
        workers,
        exec_timeout,
        output_dir,
    )

    trainer = SkillReflACTTrainer(
        optimizer_llm=_build_model(
            model_name=optimizer_model,
            api_key=_env("OPTIMIZER_API_KEY", "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", default=api_key),
            api_base=_env("OPTIMIZER_API_BASE", "OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL", default=api_base),
            provider=_env("OPTIMIZER_PROVIDER", default=provider),
        ),
        target_llm=_build_model(
            model_name=target_model,
            api_key=_env("TARGET_API_KEY", "TARGET_OPENAI_COMPATIBLE_API_KEY", default=api_key),
            api_base=_env("TARGET_API_BASE", "TARGET_OPENAI_COMPATIBLE_BASE_URL", default=api_base),
            provider=_env("TARGET_PROVIDER", default=provider),
        ),
        optimizer_model=optimizer_model,
        target_model=target_model,
        llm_attempt_timeout_secs=llm_attempt_timeout,
        llm_total_budget_secs=llm_total_budget,
        llm_max_attempts=llm_max_attempts,
    )
    result = trainer.train(
        config=config,
        adapter=get_env_adapter(env_name, **config.env_kwargs),
    )
    print(f"Training complete. env={env_name} best_score={result.best_score:.4f} output={result.output_dir}")


if __name__ == "__main__":
    main()
