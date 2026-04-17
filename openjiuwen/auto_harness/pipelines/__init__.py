# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pipeline implementations for auto-harness."""

META_EVOLVE_PIPELINE = "meta_evolve_pipeline"
EXTENDED_EVOLVE_PIPELINE = "extended_evolve_pipeline"

_PIPELINE_NAME_ALIASES = {
    "pr_pipeline": META_EVOLVE_PIPELINE,
    "extended_harness_pipeline": EXTENDED_EVOLVE_PIPELINE,
}


def normalize_pipeline_name(name: str) -> str:
    """Normalize legacy pipeline names to the current built-in names."""
    return _PIPELINE_NAME_ALIASES.get(name, name)


__all__ = [
    "EXTENDED_EVOLVE_PIPELINE",
    "META_EVOLVE_PIPELINE",
    "normalize_pipeline_name",
]
