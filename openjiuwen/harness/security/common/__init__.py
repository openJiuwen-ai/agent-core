# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers used by toolguard and fileguard (platform tags)."""

from openjiuwen.harness.security.common.builtin_platforms import (
    VALID_PLATFORMS,
    entry_matches_platforms,
    filter_entries_for_platform,
    normalize_builtin_platform,
    resolve_active_platforms,
)

__all__ = [
    "VALID_PLATFORMS",
    "entry_matches_platforms",
    "filter_entries_for_platform",
    "normalize_builtin_platform",
    "resolve_active_platforms",
]
