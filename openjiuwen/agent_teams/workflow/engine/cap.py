# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared per-run agent concurrency cap resolution."""
from __future__ import annotations

import os


def resolve_agents_per_run_cap(
    agents_per_run: int | None,
    *,
    cap_override: int | None = None,
) -> int:
    """Return explicit L2 cap or ``min(16, cpu_count - 2)``, clamped to >= 1."""
    if cap_override is not None:
        return max(1, cap_override)
    if agents_per_run is not None:
        return max(1, agents_per_run)
    return max(1, min(16, (os.cpu_count() or 4) - 2))


__all__ = ["resolve_agents_per_run_cap"]
