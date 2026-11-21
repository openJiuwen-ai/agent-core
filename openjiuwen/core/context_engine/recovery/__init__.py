# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Removable PTL (prompt-too-long) fallback truncation.

Deleting this package is safe by default: ``PtlFallbackConfig`` remains in
``schema/config.py`` with a ``None`` default, so the context engine runs only
its historical Phase A compression and never imports this package. Remove the
config class/field and the ``context_engine/__init__.py`` export only when the
configuration surface should disappear as well.
"""

from openjiuwen.core.context_engine.recovery.ptl_fallback import (
    DEFAULT_OVERFLOW_TRUNCATE_MARKER,
    TRUNCATE_MARKER_METADATA_KEY,
    OverflowTruncationResult,
    PtlFallbackConfig,
    parse_context_limit,
    recover_phase_b,
    truncate_overflowed_context,
)

__all__ = [
    "DEFAULT_OVERFLOW_TRUNCATE_MARKER",
    "OverflowTruncationResult",
    "PtlFallbackConfig",
    "TRUNCATE_MARKER_METADATA_KEY",
    "parse_context_limit",
    "recover_phase_b",
    "truncate_overflowed_context",
]
