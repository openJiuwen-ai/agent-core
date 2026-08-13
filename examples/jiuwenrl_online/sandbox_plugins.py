# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Minimal scheduler rollouter example backed by Yuanrong sandbox."""

from __future__ import annotations

import logging
import os
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.sandbox import (
    YuanrongSandboxConfig,
    YuanrongSandboxManager,
)
from openjiuwen.agent_evolving.agent_rl.online.scheduler import (
    RolloutRequest,
    RolloutResult,
)

logger = logging.getLogger("online_rl.examples.sandbox")

DEFAULT_SANDBOX_IMAGE = (
    "swe.cn-east-3.myhuaweicloud.com/openyuanrong/"
    "swe-sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
)


def _sandbox_env() -> dict[str, str]:
    keys = (
        "DEPLOYMENT",
        "AKERNEL_SERVER_ADDRESS",
        "OPENYUANRONG_SERVER_ADDRESS",
        "AKERNEL_TOKEN",
    )
    return {key: value for key in keys if (value := os.getenv(key))}


def _build_manager() -> YuanrongSandboxManager | None:
    env = _sandbox_env()
    if not env.get("AKERNEL_TOKEN"):
        logger.warning("AKERNEL_TOKEN is not set; skip real Yuanrong sandbox call")
        return None
    env.setdefault("DEPLOYMENT", "openyuanrong")
    config = YuanrongSandboxConfig(
        image=os.getenv("ONLINE_RL_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE),
        cpu=int(os.getenv("ONLINE_RL_SANDBOX_CPU", "2000")),
        memory=int(os.getenv("ONLINE_RL_SANDBOX_MEMORY", "4096")),
        idle_timeout=int(os.getenv("ONLINE_RL_SANDBOX_IDLE_TIMEOUT", "600")),
        env=env,
        install_swerex=os.getenv("ONLINE_RL_SANDBOX_INSTALL_SWEREX", "0").lower()
        in {"1", "true", "yes", "on"},
    )
    return YuanrongSandboxManager(config)


def _run_one_sandbox_command(label: str, payload: str) -> dict[str, Any]:
    manager = _build_manager()
    if manager is None:
        return {"sandbox_skipped": True, "reason": "AKERNEL_TOKEN is not set"}
    try:
        result = manager.run(
            "echo {label}_SANDBOX_OK && python3 --version && printf '%s' {payload!r}".format(
                label=label.upper(),
                payload=payload[:200],
            ),
            timeout=30,
        )
        return {
            "sandbox_id": manager.sandbox_id,
            "exit_code": result.exit_code,
            "stdout": result.stdout[-500:],
            "stderr": result.stderr[-500:],
        }
    finally:
        manager.close()


class BasicSandboxRollouter:
    """Example rollouter: call sandbox once, keep scheduler samples unchanged."""

    def rollout(self, request: RolloutRequest) -> RolloutResult:
        prompt_preview = str(request.prompts[0] if request.prompts else "")[:500]
        logger.info(
            "BasicSandboxRollouter called user=%s samples=%d prompts=%d",
            request.user_id,
            len(request.samples),
            len(request.prompts),
        )
        metrics = _run_one_sandbox_command("rollout", prompt_preview)
        logger.info("BasicSandboxRollouter sandbox metrics=%s", metrics)
        return RolloutResult(success=True, trajectories=[], metrics=metrics)
