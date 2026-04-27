# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Factory: build :class:`RLOnlineRail` from process environment (for DeepAgent integration)."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def is_rl_online_rail_enabled_from_env() -> bool:
    """True when ``USE_RL_ONLINE_RAIL`` is set to a truthy string."""
    return os.getenv("USE_RL_ONLINE_RAIL", "").strip().lower() in ("1", "true", "yes", "on")


def build_rl_online_rail_from_env() -> Optional["RLOnlineRail"]:
    """Instantiate :class:`RLOnlineRail` + :class:`TrajectoryUploader` from env, or return None.

    Environment variables:

    - ``USE_RL_ONLINE_RAIL`` — must be truthy to build (otherwise returns None without error).
    - ``TRAJECTORY_GATEWAY_URL`` — default ``http://127.0.0.1:18080``.
    - ``TRAJECTORY_GATEWAY_API_KEY`` — optional Bearer token for the gateway.
    - ``RL_ONLINE_TENANT_ID`` — optional tenant / user namespace for LoRA routing.

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

    uploader = TrajectoryUploader(gw, api_key=api_key)
    rail = RLOnlineRail(
        session_id="",
        gateway_endpoint=gw,
        tenant_id=tenant_id,
        uploader=uploader,
    )
    logger.info("build_rl_online_rail_from_env: RLOnlineRail ready (rail-v1), gateway=%s", gw)
    return rail
