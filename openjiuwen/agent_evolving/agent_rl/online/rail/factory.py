# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Factory: build :class:`RLOnlineRail` from process environment."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

logger = logging.getLogger(__name__)


def is_rl_online_rail_enabled_from_env() -> bool:
    """True when ``USE_RL_ONLINE_RAIL`` is set to a truthy string."""
    return os.getenv("USE_RL_ONLINE_RAIL", "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("build_rl_online_rail_from_env: invalid %s=%r; use default %.1f", name, raw, default)
        return default
    if value <= 0:
        logger.warning("build_rl_online_rail_from_env: non-positive %s=%r; use default %.1f", name, raw, default)
        return default
    return value


def build_rl_online_rail_from_env() -> Optional["RLOnlineRail"]:
    """Instantiate :class:`RLOnlineRail` + :class:`TrajectoryUploader` from env, or return None.

    Environment variables:

    - ``USE_RL_ONLINE_RAIL`` — must be truthy to build (otherwise returns None without error).
    - ``TRAJECTORY_GATEWAY_URL`` — default ``http://127.0.0.1:18080``.
    - ``TRAJECTORY_GATEWAY_API_KEY`` — optional Bearer token for the gateway.
    - ``RL_ONLINE_TENANT_ID`` — optional tenant / user namespace for LoRA routing.
    - ``LORA_DEFAULT_POLICY`` — optional; ``latest_by_user`` makes Rail ask the gateway for the
      effective LoRA. The gateway owns latest-version lookup and vLLM runtime loading.
    - ``TRAJECTORY_UPLOAD_TIMEOUT_SECONDS`` — optional HTTP timeout for rail batch upload.
      Gateway upload can synchronously run judge calls, so the default is 300 seconds.

    On import failure (optional extras not installed), logs a warning and returns None.
    """
    if not is_rl_online_rail_enabled_from_env():
        return None
    try:
        from .online_rail import RLOnlineRail
        from .uploader import TrajectoryUploader
    except Exception as exc:
        logger.warning(
            "build_rl_online_rail_from_env: import failed (%s). Install openjiuwen with online-rl extra.",
            exc,
        )
        return None

    gw = os.getenv("TRAJECTORY_GATEWAY_URL", "http://127.0.0.1:18080").rstrip("/")
    api_key = os.getenv("TRAJECTORY_GATEWAY_API_KEY", "") or ""
    tenant_raw = os.getenv("RL_ONLINE_TENANT_ID", "").strip()
    tenant_id: str | None = tenant_raw or None
    lora_default_policy = os.getenv("LORA_DEFAULT_POLICY", "disabled").strip() or "disabled"
    upload_timeout = _env_float("TRAJECTORY_UPLOAD_TIMEOUT_SECONDS", 300.0)

    uploader = TrajectoryUploader(gw, api_key=api_key, timeout=upload_timeout)
    rail = RLOnlineRail(
        session_id="",
        gateway_endpoint=gw,
        tenant_id=tenant_id,
        uploader=uploader,
        lora_default_policy=lora_default_policy,
        gateway_api_key=api_key,
    )
    logger.info(
        "build_rl_online_rail_from_env: RLOnlineRail ready (rail-v1), gateway=%s, lora_policy=%s, upload_timeout=%.1fs",
        gw,
        lora_default_policy,
        upload_timeout,
    )
    return rail
