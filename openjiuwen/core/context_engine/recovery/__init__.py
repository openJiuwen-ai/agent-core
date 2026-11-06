# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Removable PTL (prompt-too-long) fallback capability.

Deleting this package is safe by default: ``PtlFallbackConfig`` remains in
``schema/config.py`` with a ``None`` default, so the context engine takes its
legacy single-compression path and never imports this package. Remove the
config class/field and the ``context_engine/__init__.py`` export only when the
configuration surface should disappear as well.
"""

from openjiuwen.core.context_engine.recovery.ptl_fallback import (
    DEFAULT_OVERFLOW_TRUNCATE_MARKER,
    TRUNCATE_MARKER_METADATA_KEY,
    OverflowTruncationResult,
    PtlFallbackConfig,
    RecoveryOutcome,
    parse_context_limit,
    recover,
    truncate_overflowed_context,
)

__all__ = [
    "DEFAULT_OVERFLOW_TRUNCATE_MARKER",
    "OverflowTruncationResult",
    "PtlFallbackConfig",
    "RecoveryOutcome",
    "TRUNCATE_MARKER_METADATA_KEY",
    "parse_context_limit",
    "recover",
    "truncate_overflowed_context",
]
