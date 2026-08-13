# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Backward-compatible online rail import namespace."""

from .factory import build_online_rail_from_env, build_rl_online_rail_from_env
from ..backends.rl.rail import RLOnlineRail
from ..backends.sft.rail import SFTOnlineRail

__all__ = [
    "RLOnlineRail",
    "SFTOnlineRail",
    "build_online_rail_from_env",
    "build_rl_online_rail_from_env",
]
