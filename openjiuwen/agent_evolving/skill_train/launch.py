# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Importable launcher for ReflACT offline skill training.

Turns a flat :class:`TrainLaunchOptions` into a :class:`SkillTrainConfig`
plus the LLM clients and runs :class:`SkillReflACTTrainer`. External CLIs
(e.g. ``jiuwenswarm skill-train``) and the repo example both call this so
the preset logic lives in one place.

Environment variables are read only where an option is left at its default
(``""`` / ``None``); explicit option values always win.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
from openjiuwen.agent_evolving.skill_train.model_compat import SUPPORTED_TARGET_BACKENDS, EXEC_TARGET_BACKENDS
from openjiuwen.agent_evolving.skill_train.paths import resolve_data_path, skill_train_root
from openjiuwen.core.common.logging import logger

SUPPORTED_ENVS: tuple[str, ...] = ("searchqa", "docvqa", "officeqa")
DEFAULT_EXEC_WORKERS = 4


# ── Presets ──────────────────────────────────────────────────────────────────


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
    env_kwargs: dict[str, Any] = field(default_factory=dict)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def env_presets() -> dict[str, EnvPreset]:
    """Return the built-in presets (skill paths are package-relative)."""
    return {
        "searchqa": EnvPreset(
            env_name="searchqa",
            skill_relpath="envs/searchqa/skills/initial.md",
            split_relpath="searchqa_id_split",
            train_size=400,
            workers=24,
            analyst_workers=16,
            env_kwargs={"split_mode": "split_dir", "max_turns": 1, "limit": 0},
        ),
        "docvqa": EnvPreset(
            env_name="docvqa",
            skill_relpath="envs/docvqa/skills/initial.md",
            split_relpath="docvqa_id_split",
            workers=8,
            env_kwargs={"split_mode": "split_dir", "max_turns": 1, "image_detail": "auto", "limit": 0},
        ),
        "officeqa": EnvPreset(
            env_name="officeqa",
            skill_relpath="envs/officeqa/skills/initial.md",
            split_relpath="officeqa_id_split",
            workers=8,
            analyst_workers=8,
            # Multi-tool doc lookup routinely exceeds the generic 120s CLI budget.
            exec_timeout=600,
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


# ── Options ──────────────────────────────────────────────────────────────────


@dataclass
class TrainLaunchOptions:
    """Flat, CLI-friendly knobs for one training run.

    ``0`` / ``""`` / ``None`` mean "use preset or environment default".
    """

    env_name: str = "searchqa"
    target_backend: str = ""
    skill_init: str = ""
    split_dir: str = ""
    data_root: str = ""
    output_dir: str = ""
    # optimizer (analyst / aggregator) credentials 鈥?always a chat model
    optimizer_model: str = ""
    api_key: str = ""
    api_base: str = ""
    provider: str = ""
    # target chat model (ignored for exec backends)
    target_model: str = ""
    target_api_key: str = ""
    target_api_base: str = ""
    target_provider: str = ""
    # schedule
    num_epochs: int = 0
    train_size: int | None = None
    batch_size: int = 0
    accumulation: int = 0
    minibatch_size: int = 0
    merge_batch_size: int = 0
    edit_budget: int = 0
    min_edit_budget: int = 0
    workers: int = 0
    analyst_workers: int = 0
    exec_timeout: int = 0
    max_completion_tokens: int = 0
    limit: int | None = None
    selection_eval_size: int | None = None
    seed: int | None = None
    max_turns: int | None = None
    failure_only: bool | None = None
    use_gate: bool | None = None
    reasoning_effort: str = ""
    # jiuwenswarm exec target
    jiuwenswarm_cli_path: str = ""
    jiuwenswarm_gateway_url: str = ""
    jiuwenswarm_chat_mode: str = ""
    jiuwenswarm_instance_name: str = ""
    jiuwenswarm_trace_to_optimizer: bool = True
    # LLM retry policy
    llm_attempt_timeout: float = 0.0
    llm_total_budget: float = 0.0
    llm_max_attempts: int = 0
    extra_env_kwargs: dict[str, Any] = field(default_factory=dict)

    def resolved_backend(self) -> str:
        name = (self.target_backend or _env("TARGET_BACKEND") or "openai_chat").strip().lower()
        if name not in SUPPORTED_TARGET_BACKENDS:
            supported = ", ".join(sorted(SUPPORTED_TARGET_BACKENDS))
            raise ValueError(f"unsupported target_backend {name!r}; supported: {supported}")
        return name

    @property
    def is_exec_backend(self) -> bool:
        return self.resolved_backend() in EXEC_TARGET_BACKENDS


@dataclass(frozen=True)
class ResolvedLaunch:
    """Everything :func:`run_offline_training` needs after option resolution."""

    config: SkillTrainConfig
    backend: str
    optimizer_model: str
    optimizer_api_key: str
    optimizer_api_base: str
    optimizer_provider: str
    target_model: str
    target_api_key: str
    target_api_base: str
    target_provider: str
    llm_attempt_timeout: float
    llm_total_budget: float
    llm_max_attempts: int

    def summary(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "env": cfg.env_name,
            "target_backend": self.backend,
            "optimizer_model": self.optimizer_model,
            "target_model": self.target_model if self.backend not in EXEC_TARGET_BACKENDS else "",
            "output_dir": cfg.output_dir,
            "skill_init": cfg.skill_init,
            "split_dir": cfg.env_kwargs.get("split_dir", ""),
            "train_size": cfg.train_size,
            "batch_size": cfg.batch_size,
            "num_epochs": cfg.num_epochs,
            "workers": cfg.env_kwargs.get("workers"),
            "exec_timeout": cfg.env_kwargs.get("exec_timeout"),
        }


# ── Resolution ───────────────────────────────────────────────────────────────


def _int_opt(value: int, *env_names: str, default: int) -> int:
    if value:
        return int(value)
    raw = _env(*env_names)
    return int(raw) if raw else int(default)


def _optional_int(opt_value: int | None, *env_names: str) -> int | None:
    """Resolve an optional int from an explicit option or environment."""
    if opt_value is not None:
        return int(opt_value)
    raw = _env(*env_names)
    if not raw:
        return None
    return int(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw not in {"0", "false", "False", "no", "NO"}


def _apply_data_root(opts: TrainLaunchOptions) -> None:
    root = (opts.data_root or _env("SKILL_TRAIN_DATA_ROOT")).strip()
    if root:
        os.environ["SKILL_TRAIN_DATA_ROOT"] = str(Path(root).expanduser())


def _resolve_skill_init(opts: TrainLaunchOptions, preset: EnvPreset) -> str:
    explicit = opts.skill_init or _env("SKILL_INIT")
    path = Path(explicit).expanduser() if explicit else skill_train_root() / preset.skill_relpath
    if not path.is_file():
        raise FileNotFoundError(f"skill_init not found: {path}")
    return str(path.resolve())


def _resolve_split_dir(opts: TrainLaunchOptions, preset: EnvPreset) -> str:
    explicit = opts.split_dir or _env(f"{preset.env_name.upper()}_SPLIT_DIR", "SPLIT_DIR")
    if explicit:
        path = Path(explicit).expanduser()
    else:
        path = resolve_data_path(preset.split_relpath)
    if not path.exists():
        raise FileNotFoundError(
            f"split_dir not found for {preset.env_name}: {path} "
            "(set --split-dir / --data-root or SKILL_TRAIN_DATA_ROOT)"
        )
    return str(path.resolve())


def _officeqa_env_kwargs(env_kwargs: dict[str, Any]) -> dict[str, Any]:
    from openjiuwen.agent_evolving.skill_train.datasets.materialize import ensure_officeqa_docs

    ensured = ensure_officeqa_docs()
    docs = (
        str(ensured)
        if ensured is not None
        else str(resolve_data_path("officeqa_docs_official", "OFFICEQA_DATA_DIRS", "OFFICEQA_DOCS_DIR"))
    )
    if not Path(docs).exists():
        raise FileNotFoundError(
            f"OfficeQA docs missing: {docs} (set OFFICEQA_DATA_DIRS / OFFICEQA_DOCS_DIR or SKILL_TRAIN_DATA_ROOT)"
        )
    return {**env_kwargs, "data_dirs": [docs]}


def build_train_config(opts: TrainLaunchOptions) -> ResolvedLaunch:
    """Resolve *opts* against presets and the environment.

    Raises ``ValueError`` for an unknown env / backend or missing optimizer
    credentials and ``FileNotFoundError`` for missing data assets.
    """
    if opts.env_name not in SUPPORTED_ENVS:
        raise ValueError(f"unsupported env {opts.env_name!r}; supported: {', '.join(SUPPORTED_ENVS)}")
    backend = opts.resolved_backend()
    is_exec = backend in EXEC_TARGET_BACKENDS
    _apply_data_root(opts)
    preset = env_presets()[opts.env_name]

    skill_init = _resolve_skill_init(opts, preset)
    split_dir = _resolve_split_dir(opts, preset)

    provider = opts.provider or _env("MODEL_PROVIDER", "OPTIMIZER_PROVIDER", "TARGET_PROVIDER", default="openai")
    api_key = opts.api_key or _env("API_KEY", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY")
    api_base = opts.api_base or _env("API_BASE", "OPENAI_COMPATIBLE_BASE_URL")
    default_model = _env("MODEL_NAME", "OPENAI_COMPATIBLE_MODEL", default="GLM-5.2")
    optimizer_model = opts.optimizer_model or _env("OPTIMIZER_MODEL", default=default_model)
    optimizer_api_key = _env("OPTIMIZER_API_KEY", "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", default=api_key)
    optimizer_api_base = _env("OPTIMIZER_API_BASE", "OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL", default=api_base)
    optimizer_provider = _env("OPTIMIZER_PROVIDER", default=provider)

    missing: list[str] = []
    for name, value in (
        ("API_KEY / --api-key", optimizer_api_key),
        ("API_BASE / --api-base", optimizer_api_base),
        ("OPTIMIZER_MODEL / --optimizer-model", optimizer_model),
    ):
        if not value:
            missing.append(name)
    if missing:
        raise ValueError("missing optimizer credentials: " + ", ".join(missing))

    target_model = "" if is_exec else (opts.target_model or _env("TARGET_MODEL", default=default_model))
    target_api_key = (
        ""
        if is_exec
        else (opts.target_api_key or _env("TARGET_API_KEY", "TARGET_OPENAI_COMPATIBLE_API_KEY", default=api_key))
    )
    target_api_base = (
        ""
        if is_exec
        else (opts.target_api_base or _env("TARGET_API_BASE", "TARGET_OPENAI_COMPATIBLE_BASE_URL", default=api_base))
    )
    target_provider = "" if is_exec else (opts.target_provider or _env("TARGET_PROVIDER", default=provider))

    exec_timeout = _int_opt(opts.exec_timeout, "EXEC_TIMEOUT", default=preset.exec_timeout)
    # Each exec worker is a full `jiuwenswarm chat` process hitting one Gateway,
    # so the chat-model preset fan-out is far too aggressive there.
    default_workers = min(preset.workers, DEFAULT_EXEC_WORKERS) if is_exec else preset.workers
    workers = _int_opt(opts.workers, "WORKERS", default=default_workers)
    analyst_workers = _int_opt(opts.analyst_workers, "ANALYST_WORKERS", default=preset.analyst_workers)
    minibatch_size = _int_opt(opts.minibatch_size, "MINIBATCH_SIZE", default=8)
    edit_budget = _int_opt(opts.edit_budget, "EDIT_BUDGET", default=preset.edit_budget)
    seed = int(opts.seed) if opts.seed is not None else int(_env("SEED", default="42"))
    failure_only = opts.failure_only if opts.failure_only is not None else _bool_env("FAILURE_ONLY", False)

    env_kwargs: dict[str, Any] = {
        "split_dir": split_dir,
        "workers": workers,
        "analyst_workers": analyst_workers,
        "exec_timeout": exec_timeout,
        "max_completion_tokens": _int_opt(
            opts.max_completion_tokens, "MAX_COMPLETION_TOKENS", default=preset.max_completion_tokens
        ),
        "minibatch_size": minibatch_size,
        "edit_budget": edit_budget,
        "seed": seed,
        "failure_only": failure_only,
    }
    env_kwargs.update(preset.env_kwargs)
    if opts.env_name == "officeqa":
        env_kwargs = _officeqa_env_kwargs(env_kwargs)
    limit = _optional_int(opts.limit, "LIMIT")
    if limit is not None:
        env_kwargs["limit"] = int(limit)
    max_turns = _optional_int(opts.max_turns, "MAX_TURNS")
    if max_turns is not None and "max_turns" in env_kwargs:
        env_kwargs["max_turns"] = int(max_turns)
    env_kwargs.update(opts.extra_env_kwargs)

    label = backend if is_exec else target_model
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = opts.output_dir or _env(
        "SKILL_TRAIN_OUTPUT",
        default=str(Path.cwd() / "outputs" / f"skill_train_{opts.env_name}_{label}_{stamp}"),
    )
    train_size = (
        opts.train_size if opts.train_size is not None else _int_opt(0, "TRAIN_SIZE", default=preset.train_size)
    )
    use_gate = opts.use_gate if opts.use_gate is not None else _bool_env("USE_GATE", True)
    selection_eval_size = _optional_int(opts.selection_eval_size, "SELECTION_EVAL_SIZE")
    if selection_eval_size is None:
        selection_eval_size = 40

    config = SkillTrainConfig(
        env_name=opts.env_name,
        output_dir=str(Path(output_dir).expanduser()),
        skill_init=skill_init,
        num_epochs=_int_opt(opts.num_epochs, "NUM_EPOCHS", default=4),
        train_size=int(train_size),
        batch_size=_int_opt(opts.batch_size, "BATCH_SIZE", default=preset.batch_size),
        accumulation=_int_opt(opts.accumulation, "ACCUMULATION", default=preset.accumulation),
        minibatch_size=minibatch_size,
        merge_batch_size=_int_opt(opts.merge_batch_size, "MERGE_BATCH_SIZE", default=8),
        analyst_workers=analyst_workers,
        edit_budget=edit_budget,
        min_edit_budget=_int_opt(opts.min_edit_budget, "MIN_EDIT_BUDGET", default=preset.min_edit_budget),
        lr_scheduler=_env("LR_SCHEDULER", default="cosine"),
        failure_only=failure_only,
        use_gate=use_gate,
        selection_eval_size=selection_eval_size,
        seed=seed,
        skill_update_mode=_env("SKILL_UPDATE_MODE", default="patch"),
        use_slow_update=_bool_env("USE_SLOW_UPDATE", True),
        slow_update_samples=int(_env("SLOW_UPDATE_SAMPLES", default="20")),
        slow_update_gate_with_selection=_bool_env("SLOW_UPDATE_GATE_WITH_SELECTION", False),
        longitudinal_pair_policy=_env("LONGITUDINAL_PAIR_POLICY", default="mixed"),
        use_meta_skill=_bool_env("USE_META_SKILL", True),
        reasoning_effort=(opts.reasoning_effort or _env("REASONING_EFFORT", default="medium")) or None,
        target_backend=backend,
        jiuwenswarm_trace_to_optimizer=opts.jiuwenswarm_trace_to_optimizer,
        jiuwenswarm_cli_path=opts.jiuwenswarm_cli_path or _env("JIUWENSWARM_CLI_PATH"),
        jiuwenswarm_gateway_url=opts.jiuwenswarm_gateway_url or _env("JIUWENSWARM_GATEWAY_URL"),
        jiuwenswarm_chat_mode=opts.jiuwenswarm_chat_mode or _env("JIUWENSWARM_CHAT_MODE"),
        jiuwenswarm_instance_name=opts.jiuwenswarm_instance_name or _env("JIUWENSWARM_INSTANCE_NAME"),
        env_kwargs=env_kwargs,
    )

    attempt_timeout = opts.llm_attempt_timeout or float(_env("LLM_ATTEMPT_TIMEOUT", default=str(exec_timeout)))
    total_budget = opts.llm_total_budget or float(
        _env("LLM_TOTAL_BUDGET", default=str(max(600.0, attempt_timeout * 5)))
    )
    return ResolvedLaunch(
        config=config,
        backend=backend,
        optimizer_model=optimizer_model,
        optimizer_api_key=optimizer_api_key,
        optimizer_api_base=optimizer_api_base,
        optimizer_provider=optimizer_provider,
        target_model=target_model,
        target_api_key=target_api_key,
        target_api_base=target_api_base,
        target_provider=target_provider,
        llm_attempt_timeout=float(attempt_timeout),
        llm_total_budget=float(total_budget),
        llm_max_attempts=_int_opt(opts.llm_max_attempts, "LLM_MAX_ATTEMPTS", default=3),
    )


# ── Execution ────────────────────────────────────────────────────────────────


def _build_model(*, model_name: str, api_key: str, api_base: str, provider: str) -> Any:
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.foundation.llm.model import Model

    return Model(
        model_client_config=ModelClientConfig(client_provider=provider, api_key=api_key, api_base=api_base),
        model_config=ModelRequestConfig(model=model_name),
    )


def build_trainer(resolved: ResolvedLaunch) -> Any:
    """Instantiate :class:`SkillReflACTTrainer` for *resolved* (target client only for chat)."""
    from openjiuwen.agent_evolving.skill_train.trainer import SkillReflACTTrainer

    target_llm = None
    if resolved.backend not in EXEC_TARGET_BACKENDS:
        target_llm = _build_model(
            model_name=resolved.target_model,
            api_key=resolved.target_api_key,
            api_base=resolved.target_api_base,
            provider=resolved.target_provider,
        )
    return SkillReflACTTrainer(
        optimizer_llm=_build_model(
            model_name=resolved.optimizer_model,
            api_key=resolved.optimizer_api_key,
            api_base=resolved.optimizer_api_base,
            provider=resolved.optimizer_provider,
        ),
        optimizer_model=resolved.optimizer_model,
        target_llm=target_llm,
        target_model=resolved.target_model,
        llm_attempt_timeout_secs=resolved.llm_attempt_timeout,
        llm_total_budget_secs=resolved.llm_total_budget,
        llm_max_attempts=resolved.llm_max_attempts,
    )


def run_offline_training(opts: TrainLaunchOptions) -> Any:
    """Resolve options, build the trainer and run the full ReflACT loop.

    Returns :class:`~openjiuwen.agent_evolving.skill_train.trainer.SkillTrainResult`.
    """
    from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter

    resolved = build_train_config(opts)
    logger.info("[skill_train.launch] %s", json.dumps(resolved.summary(), ensure_ascii=False))
    trainer = build_trainer(resolved)
    config = resolved.config
    return trainer.train(config=config, adapter=get_env_adapter(config.env_name, **config.env_kwargs))


def run_offline_eval(opts: TrainLaunchOptions, *, split: str = "test", skill_path: str = "") -> dict[str, Any]:
    """Single rollout of one skill over *split* (no optimization); writes ``eval_summary.json``.

    Uses the same backend wiring as training (exec harness or chat target).
    """
    from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter
    from openjiuwen.agent_evolving.skill_train.scoring import compute_score
    from openjiuwen.agent_evolving.skill_train.trainer import _configure_target_backend

    eval_opts = replace(opts, skill_init=skill_path or opts.skill_init)
    resolved = build_train_config(eval_opts)
    config = resolved.config
    cfg = config.to_trainer_cfg()
    _configure_target_backend(cfg)
    if resolved.backend not in EXEC_TARGET_BACKENDS:
        from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient, set_target_client

        set_target_client(
            ChatLLMClient(
                llm=_build_model(
                    model_name=resolved.target_model,
                    api_key=resolved.target_api_key,
                    api_base=resolved.target_api_base,
                    provider=resolved.target_provider,
                ),
                model=resolved.target_model,
            )
        )

    adapter = get_env_adapter(config.env_name, **config.env_kwargs)
    adapter.setup(cfg)
    limit = int(config.env_kwargs.get("limit") or 0)
    items = list(adapter.build_eval_env(env_num=limit, split=split, seed=config.seed))
    if limit > 0:
        items = items[:limit]
    skill_content = Path(config.skill_init).read_text(encoding="utf-8")
    out_root = Path(config.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    results = adapter.rollout(items, skill_content, str(out_root))
    hard, soft = compute_score(results)
    summary = {
        "env": config.env_name,
        "target_backend": resolved.backend,
        "skill": config.skill_init,
        "split": split,
        "n_items": len(results),
        "hard": hard,
        "soft": soft,
        "output_dir": str(out_root),
    }
    (out_root / "eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("[skill_train.launch] eval done: %s", json.dumps(summary, ensure_ascii=False))
    return summary
