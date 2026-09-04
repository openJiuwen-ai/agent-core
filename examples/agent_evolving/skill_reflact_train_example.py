# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
SkillOpt ReflACT offline training example (SearchQA).

Mirrors SkillOpt ``configs/searchqa/default.yaml`` (+ ``configs/_base_/default.yaml``).

Prerequisites:
- Materialized split at ``data/searchqa_split/{train,val,test}/items.json``
- LLM credentials in repo-root ``.env`` (auto-loaded), supporting either::

    API_KEY=...
    API_BASE=https://.../v1
    MODEL_PROVIDER=openai
    OPTIMIZER_MODEL=GLM-5.2
    TARGET_MODEL=GLM-5.2

  or SkillOpt-style::

    OPENAI_COMPATIBLE_API_KEY=...
    OPENAI_COMPATIBLE_BASE_URL=https://.../v1
    OPENAI_COMPATIBLE_MODEL=...

Optional LLM resilience (defaults keep prior behavior)::

    LLM_ATTEMPT_TIMEOUT=300   # per-call seconds (falls back to EXEC_TIMEOUT)
    LLM_TOTAL_BUDGET=900      # total budget across retries
    LLM_MAX_ATTEMPTS=5
    WORKERS=8                 # lower concurrency if timeouts persist

Run::

    uv run python examples/agent_evolving/skill_reflact_train_example.py
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from openjiuwen.agent_evolving.skill_train import SkillReflACTTrainer, SkillTrainConfig, get_env_adapter
from openjiuwen.core.common.logging import logger, llm_logger
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model import Model


def _configure_logging() -> None:
    """Keep trainer progress; silence per-call LLM INFO spam."""
    quiet = {
        "level": "WARNING",
        "output": ["console"],
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }
    # llm_call_end / "Before parse content..." is INFO on llm_logger
    llm_logger.reconfigure(quiet)
    # keep SkillReflACTTrainer step lines (INFO on common logger)
    logger.reconfigure(
        {
            "level": os.getenv("SKILL_TRAIN_LOG_LEVEL", "INFO"),
            "output": ["console"],
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        }
    )


def _load_env() -> None:
    candidates = (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    )
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


def _build_model(*, model_name: str, api_key: str, api_base: str, provider: str) -> Model:
    client_config = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
    )
    request_config = ModelRequestConfig(model=model_name)
    return Model(model_client_config=client_config, model_config=request_config)


def main() -> None:
    _load_env()
    _configure_logging()

    repo_root = Path(__file__).resolve().parents[2]
    skill_init = _env(
        "SKILL_INIT",
        default=str(repo_root / "openjiuwen/agent_evolving/skill_train/envs/searchqa/skills/initial.md"),
    )
    split_dir = _env("SEARCHQA_SPLIT_DIR", default=str(repo_root / "data/searchqa_split"))

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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _env(
        "SKILL_TRAIN_OUTPUT",
        default=str(repo_root / f"outputs/skillopt_searchqa_{target_model}_{timestamp}"),
    )

    def _env_bool(name: str, default: str = "1") -> bool:
        return _env(name, default=default) not in {"0", "false", "False", "no", "NO"}

    exec_timeout = int(_env("EXEC_TIMEOUT", default="120"))
    # Per-call resilience timeout: explicit LLM_ATTEMPT_TIMEOUT, else EXEC_TIMEOUT.
    llm_attempt_timeout = float(
        _env("LLM_ATTEMPT_TIMEOUT", default=str(exec_timeout))
    )
    llm_total_budget = float(
        _env(
            "LLM_TOTAL_BUDGET",
            default=str(max(600.0, llm_attempt_timeout * 5)),
        )
    )
    llm_max_attempts = int(_env("LLM_MAX_ATTEMPTS", default="5"))

    config = SkillTrainConfig(
        env_name="searchqa",
        output_dir=output_dir,
        skill_init=skill_init,
        num_epochs=int(_env("NUM_EPOCHS", default="4")),
        train_size=int(_env("TRAIN_SIZE", default="400")),
        batch_size=int(_env("BATCH_SIZE", default="40")),
        accumulation=int(_env("ACCUMULATION", default="1")),
        minibatch_size=int(_env("MINIBATCH_SIZE", default="8")),
        merge_batch_size=int(_env("MERGE_BATCH_SIZE", default="8")),
        analyst_workers=int(_env("ANALYST_WORKERS", default="16")),
        edit_budget=int(_env("EDIT_BUDGET", default="4")),
        min_edit_budget=int(_env("MIN_EDIT_BUDGET", default="2")),
        lr_scheduler=_env("LR_SCHEDULER", default="cosine"),
        failure_only=_env("FAILURE_ONLY", default="0") in {"1", "true", "True"},
        use_gate=_env_bool("USE_GATE", default="1"),
        seed=int(_env("SEED", default="42")),
        skill_update_mode=_env("SKILL_UPDATE_MODE", default="patch"),
        # Align SkillOpt configs/_base_/default.yaml
        use_slow_update=_env_bool("USE_SLOW_UPDATE", default="1"),
        slow_update_samples=int(_env("SLOW_UPDATE_SAMPLES", default="20")),
        slow_update_gate_with_selection=_env_bool(
            "SLOW_UPDATE_GATE_WITH_SELECTION", default="0"
        ),
        longitudinal_pair_policy=_env("LONGITUDINAL_PAIR_POLICY", default="mixed"),
        use_meta_skill=_env_bool("USE_META_SKILL", default="1"),
        reasoning_effort=_env("REASONING_EFFORT", default="medium") or None,
        env_kwargs={
            "split_dir": split_dir,
            "split_mode": _env("SPLIT_MODE", default="split_dir"),
            "workers": int(_env("WORKERS", default="24")),
            "limit": int(_env("LIMIT", default="0")),
            "max_turns": int(_env("MAX_TURNS", default="1")),
            "max_completion_tokens": int(_env("MAX_COMPLETION_TOKENS", default="16384")),
            "exec_timeout": exec_timeout,
            "analyst_workers": int(_env("ANALYST_WORKERS", default="16")),
            "failure_only": _env("FAILURE_ONLY", default="0") in {"1", "true", "True"},
            "minibatch_size": int(_env("MINIBATCH_SIZE", default="8")),
            "edit_budget": int(_env("EDIT_BUDGET", default="4")),
            "seed": int(_env("SEED", default="42")),
        },
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
    result = trainer.train(config=config, adapter=get_env_adapter("searchqa", **config.env_kwargs))
    print(f"Training complete. best_score={result.best_score:.4f} output={result.output_dir}")


if __name__ == "__main__":
    main()
