# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""cua package for cua-driver desktop runtime integration."""

from __future__ import annotations

from openjiuwen.harness.tools.cua.cua_capabilities import (
    DEFAULT_CUA_AGENT_CAPABILITY_NAMES,
    DEFAULT_CUA_CAPABILITIES,
    CuaCapability,
    ResolvedCuaCapabilities,
    resolve_cua_capabilities,
)

__all__ = [
    "DEFAULT_CUA_AGENT_CAPABILITY_NAMES",
    "DEFAULT_CUA_CAPABILITIES",
    "CuaCapability",
    "ResolvedCuaCapabilities",
    "resolve_cua_capabilities",
]
