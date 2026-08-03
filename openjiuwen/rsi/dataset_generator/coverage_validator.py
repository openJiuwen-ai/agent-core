# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset coverage validation boundary."""

from __future__ import annotations


class CoverageValidator:
    """Validate that generated cases cover declared task dimensions."""

    async def validate(self, dataset_dir: str, output_path: str) -> str:
        """TODO: write ``coverage_report.yaml`` and return its path."""
        raise NotImplementedError("TODO: validate dataset coverage")


__all__ = [
    "CoverageValidator",
]
