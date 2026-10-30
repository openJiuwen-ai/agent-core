# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Locate packaged skills for native loading and explicit filesystem policies."""

from pathlib import Path


def get_builtin_skill_dir(name: str) -> Path:
    """Return one packaged skill directory, never its sibling library root."""
    if not name or name in {".", ".."} or any(separator in name for separator in ("/", "\\")):
        raise ValueError("A builtin skill name must be a single directory name")
    return Path(__file__).resolve().parent / "skills" / name
