# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helpers for task-scoped GitCode authentication."""

from __future__ import annotations

import base64
import os
from typing import Dict


def build_git_auth_env(
    *,
    username: str = "",
    token: str = "",
) -> Dict[str, str]:
    """Build a subprocess environment for non-interactive GitCode auth.

    The environment avoids user-global credential helpers and injects an
    Authorization header only for ``https://gitcode.com`` requests.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"

    if not username or not token:
        return env

    basic = base64.b64encode(
        f"{username}:{token}".encode("utf-8")
    ).decode("ascii")
    env.update({
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.interactive",
        "GIT_CONFIG_VALUE_1": "never",
        "GIT_CONFIG_KEY_2": "http.https://gitcode.com/.extraheader",
        "GIT_CONFIG_VALUE_2": (
            f"AUTHORIZATION: basic {basic}"
        ),
    })
    return env


__all__ = ["build_git_auth_env"]
