# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation case generation boundary."""

from __future__ import annotations


class CaseGenerator:
    """Generate case files from structured task analysis."""

    async def generate_cases(self, task_analysis_path: str, cases_dir: str) -> list[str]:
        """TODO: create case YAML files and return their paths."""
        raise NotImplementedError("TODO: generate evaluation cases")


__all__ = [
    "CaseGenerator",
]
