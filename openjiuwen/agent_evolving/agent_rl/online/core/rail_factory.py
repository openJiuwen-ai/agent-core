# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Factory: build online training rails from process environment."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.setup import (
    get_observability_runtime,
    init_observability,
    is_initialized,
)

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.agent_rl.online.backends.rl.rail import RLOnlineRail
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.harness.rails import EvolutionRail

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnlineTrainingRailEnvConfig:
    """Environment-derived config used to choose and initialize the online Rail."""

    gateway_endpoint: str
    gateway_api_key: str
    tenant_id: str | None
    lora_default_policy: str
    capture_mode: str
    backend: str
    sft_scenario: str
    sft_upload_mode: str
    session_done_on_invoke_end: bool
    session_flush_token_threshold_k: int
    upload_timeout_seconds: float
    wal_dir: str
    force_wal: bool


_DEFAULT_TRAJECTORY_SPAN_PROCESSOR: TrajectorySpanProcessor | None = None


def is_rl_online_rail_enabled_from_env() -> bool:
    """True when ``USE_RL_ONLINE_RAIL`` is set to a truthy string."""
    return os.getenv("USE_RL_ONLINE_RAIL", "").strip().lower() in ("1", "true", "yes", "on")


def is_online_training_rail_instance(rail: Any) -> bool:
    """Return True for direct or extension-wrapped online training rails."""

    try:
        from ..backends.rl.rail import RLOnlineRail
        from ..backends.sft.rail import SFTOnlineRail
    except Exception:
        return type(rail).__name__ in {"RLOnlineRail", "SFTOnlineRail", "RLOnlineExtensionRail"}

    if isinstance(rail, (RLOnlineRail, SFTOnlineRail)):
        return True
    inner = getattr(rail, "_inner", None)
    return isinstance(inner, (RLOnlineRail, SFTOnlineRail))


def has_online_training_rail(rails: Iterable[Any]) -> bool:
    """Return True when a rail collection already contains online training collection."""

    return any(is_online_training_rail_instance(rail) for rail in rails)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("build_rl_online_rail_from_env: invalid integer %s=%r, using %d", key, raw, default)
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("build_rl_online_rail_from_env: invalid float %s=%r, using %.1f", key, raw, default)
        return default
    if value <= 0:
        logger.warning("build_rl_online_rail_from_env: non-positive float %s=%r, using %.1f", key, raw, default)
        return default
    return value


def _normalize_backend(raw: str, capture_mode: str) -> str:
    backend = (raw or "").strip().lower()
    if backend in {"sft"}:
        return "sft"
    if backend in {"ppo", "rl", "online_rl"}:
        return "ppo"
    normalized_capture = (capture_mode or "").strip().lower()
    if normalized_capture in {"sft", "raw", "raw_session", "sft_raw"}:
        return "sft"
    return "ppo"


def _load_online_training_rail_env_config() -> OnlineTrainingRailEnvConfig:
    """Read all env knobs in one place before selecting RLOnlineRail/SFTOnlineRail."""

    gateway_endpoint = os.getenv("TRAJECTORY_GATEWAY_URL", "http://127.0.0.1:18080").rstrip("/")
    gateway_api_key = os.getenv("TRAJECTORY_GATEWAY_API_KEY", "") or ""
    tenant_raw = os.getenv("RL_ONLINE_TENANT_ID", "").strip()
    capture_mode = os.getenv("RL_ONLINE_CAPTURE_MODE", "ppo_turn").strip() or "ppo_turn"
    backend = _normalize_backend(os.getenv("TRAIN_BACKEND", ""), capture_mode)
    return OnlineTrainingRailEnvConfig(
        gateway_endpoint=gateway_endpoint,
        gateway_api_key=gateway_api_key,
        tenant_id=tenant_raw or None,
        lora_default_policy=os.getenv("LORA_DEFAULT_POLICY", "disabled").strip() or "disabled",
        capture_mode=capture_mode,
        backend=backend,
        sft_scenario=os.getenv("SFT_SCENARIO", "multi_turn_supervisor").strip() or "multi_turn_supervisor",
        sft_upload_mode=os.getenv("SFT_ONLINE_UPLOAD_MODE", "raw").strip() or "raw",
        session_done_on_invoke_end=_env_bool("RL_ONLINE_SESSION_DONE_ON_INVOKE_END", backend == "ppo"),
        session_flush_token_threshold_k=_env_int("TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K", 0),
        upload_timeout_seconds=_env_float("TRAJECTORY_UPLOAD_TIMEOUT_SECONDS", 300.0),
        wal_dir=os.getenv("TRAJECTORY_WAL_DIR", "records/rail_v1_wal").strip() or "records/rail_v1_wal",
        force_wal=_env_bool("TRAJECTORY_FORCE_WAL", False),
    )


def _online_observability_config() -> ObservabilityConfig:
    """Build a local observability config for env-created online rails."""

    traces_dir = os.getenv("ONLINE_RL_OBSERVABILITY_TRACES_DIR", "").strip()
    if not traces_dir:
        traces_dir = str(Path(os.getenv("TRAJECTORY_WAL_DIR", "records/rail_v1_wal")).parent / "observability_traces")
    return ObservabilityConfig(
        exporter="file",
        traces_dir=traces_dir,
        backend="otlp",
        service_name=os.getenv("ONLINE_RL_OBSERVABILITY_SERVICE_NAME", "openjiuwen-online-rl"),
    )


def _ensure_observability_processor(processor: TrajectorySpanProcessor) -> None:
    """Ensure online rails receive LLM/tool spans in host processes.

    JiuwenSwarm extension rails are constructed through a no-arg plugin API, so
    they cannot rely on a caller-owned processor. In that path we initialize a
    lightweight local file-export observability runtime and attach the processor
    there. If another runtime already exists, adding the same processor is
    idempotent by object identity.
    """

    try:
        if not is_initialized():
            init_observability(_online_observability_config(), additional_span_processors=(processor,))
            return
        get_observability_runtime().add_span_processors((processor,))
    except Exception as exc:
        logger.warning("build_online_training_rail_from_env: observability processor attach failed: %s", exc)


def _resolve_trajectory_span_processor(
    trajectory_span_processor: TrajectorySpanProcessor | None,
) -> TrajectorySpanProcessor:
    """Return an explicit or process-default processor for env rail creation."""

    if trajectory_span_processor is not None:
        if not isinstance(trajectory_span_processor, TrajectorySpanProcessor):
            raise TypeError("trajectory_span_processor must be a TrajectorySpanProcessor")
        _ensure_observability_processor(trajectory_span_processor)
        return trajectory_span_processor

    global _DEFAULT_TRAJECTORY_SPAN_PROCESSOR
    if _DEFAULT_TRAJECTORY_SPAN_PROCESSOR is None:
        _DEFAULT_TRAJECTORY_SPAN_PROCESSOR = TrajectorySpanProcessor()
    _ensure_observability_processor(_DEFAULT_TRAJECTORY_SPAN_PROCESSOR)
    return _DEFAULT_TRAJECTORY_SPAN_PROCESSOR


def build_online_training_rail_from_env(
    existing_rails: Iterable[Any] = (),
    *,
    trajectory_span_processor: TrajectorySpanProcessor | None = None,
) -> Optional["EvolutionRail"]:
    """Instantiate an online training rail from env, or return None.

    Environment variables:

    - ``USE_RL_ONLINE_RAIL`` — must be truthy to build (otherwise returns None without error).
    - ``TRAIN_BACKEND`` — ``PPO``/``RL`` builds ``RLOnlineRail``; ``SFT`` builds ``SFTOnlineRail``.
    - ``TRAJECTORY_GATEWAY_URL`` — default ``http://127.0.0.1:18080``.
    - ``TRAJECTORY_GATEWAY_API_KEY`` — optional Bearer token for the gateway.
    - ``RL_ONLINE_TENANT_ID`` — optional tenant / user namespace for LoRA routing.
    - ``LORA_DEFAULT_POLICY`` — optional; ``latest_by_user`` makes Rail ask the gateway for the
      effective LoRA. The gateway owns latest-version lookup and vLLM runtime loading.
    - ``RL_ONLINE_CAPTURE_MODE`` — compatibility only; ``raw_session`` maps to SFT.
    - ``SFT_SCENARIO`` — SFT raw scenario tag, e.g. ``multi_turn_supervisor``.
    - ``SFT_ONLINE_UPLOAD_MODE`` — SFT upload mode; ``raw`` keeps replay flow, ``sample`` uploads
      supervisor-collected training samples directly.
    - ``RL_ONLINE_SESSION_DONE_ON_INVOKE_END`` — SFT default false; set true for per-invoke raw sessions.
    - ``TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K`` — optional raw-session token threshold.
    - ``TRAJECTORY_UPLOAD_TIMEOUT_SECONDS`` — optional HTTP timeout for gateway upload.
    - ``TRAJECTORY_WAL_DIR`` — local WAL directory used when upload fails.
    - ``TRAJECTORY_FORCE_WAL`` — force writing batches to WAL without HTTP upload.
    - ``ONLINE_RL_OBSERVABILITY_TRACES_DIR`` — local file-export trace directory
      used when this factory has to initialize observability for JiuwenSwarm.

    On import failure (optional extras not installed), logs a warning and returns None.
    """
    if not is_rl_online_rail_enabled_from_env():
        return None
    processor = _resolve_trajectory_span_processor(trajectory_span_processor)
    if has_online_training_rail(existing_rails):
        logger.info("build_online_training_rail_from_env: online training rail already configured; skip")
        return None
    try:
        from ..backends.rl.rail import RLOnlineRail
        from ..backends.sft.rail import SFTOnlineRail
        from .uploader import TrajectoryUploader
    except Exception as exc:
        logger.warning(
            "build_online_training_rail_from_env: import failed (%s). Install openjiuwen with online-rl extra.",
            exc,
        )
        return None

    env_config = _load_online_training_rail_env_config()

    uploader = TrajectoryUploader(
        env_config.gateway_endpoint,
        api_key=env_config.gateway_api_key,
        timeout=env_config.upload_timeout_seconds,
        wal_dir=env_config.wal_dir,
        force_wal=env_config.force_wal,
    )
    common_kwargs = {
        "session_id": "",
        "gateway_endpoint": env_config.gateway_endpoint,
        "tenant_id": env_config.tenant_id,
        "uploader": uploader,
        "lora_default_policy": env_config.lora_default_policy,
        "gateway_api_key": env_config.gateway_api_key,
        "trajectory_span_processor": processor,
    }
    if env_config.backend == "sft":
        rail = SFTOnlineRail(
            **common_kwargs,
            sft_scenario=env_config.sft_scenario,
            session_done_on_invoke_end=env_config.session_done_on_invoke_end,
            session_flush_token_threshold_k=env_config.session_flush_token_threshold_k,
            upload_mode=env_config.sft_upload_mode,
        )
    else:
        rail = RLOnlineRail(
            **common_kwargs,
            session_done_on_invoke_end=env_config.session_done_on_invoke_end,
            capture_mode=env_config.capture_mode,
        )
    logger.info(
        "build_online_training_rail_from_env: %s ready, gateway=%s, backend=%s, "
        "lora_policy=%s, capture_mode=%s, sft_upload=%s",
        type(rail).__name__,
        env_config.gateway_endpoint,
        env_config.backend,
        env_config.lora_default_policy,
        env_config.capture_mode,
        env_config.sft_upload_mode if env_config.backend == "sft" else "",
    )
    setattr(rail, "_async_evolution", False)
    return rail


def build_rl_online_rail_from_env(
    existing_rails: Iterable[Any] = (),
    *,
    trajectory_span_processor: TrajectorySpanProcessor | None = None,
) -> Optional["EvolutionRail"]:
    """Backward-compatible alias for the online training rail factory."""
    return build_online_training_rail_from_env(
        existing_rails,
        trajectory_span_processor=trajectory_span_processor,
    )


def build_online_rail_from_env(
    existing_rails: Iterable[Any] = (),
    *,
    trajectory_span_processor: TrajectorySpanProcessor | None = None,
) -> Optional["EvolutionRail"]:
    """Backward-compatible env entry point used by older agent-core integrations.

    Older code loaded only ``RLOnlineRail`` from this function.  The integration
    point now keeps the same name but delegates to the backend-aware factory so
    ``TRAIN_BACKEND=SFT`` selects ``SFTOnlineRail``.
    """

    return build_online_training_rail_from_env(
        existing_rails,
        trajectory_span_processor=trajectory_span_processor,
    )


def is_rl_online_rail_instance(rail: Any) -> bool:
    """Backward-compatible duplicate check for env-injected online rails."""

    return is_online_training_rail_instance(rail)


def has_rl_online_rail(rails: Iterable[Any]) -> bool:
    """Backward-compatible duplicate check for env-injected online rails."""

    return has_online_training_rail(rails)
