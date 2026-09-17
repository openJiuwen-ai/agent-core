# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Locate a ripgrep (``rg``) binary for :class:`GrepTool`.

Resolution order:

1. ``OPENJIUWEN_RG`` / ``ICODE_RG`` environment overrides
2. Optional companion vendor hook (``openjiuwen_icode.vendor.rg_binary``)
   when that package is installed; ignored otherwise
3. ``PATH`` via :func:`shutil.which`
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _is_usable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return True
    return os.access(path, os.X_OK)


def _from_env() -> Optional[str]:
    for key in ("OPENJIUWEN_RG", "ICODE_RG"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if _is_usable(path):
            return str(path.resolve())
    return None


def _from_icode_vendor() -> Optional[str]:
    try:
        from openjiuwen_icode.vendor.rg_binary import (  # type: ignore[import-not-found]
            bundled_rg_path,
        )
    except ImportError:
        return None
    path = bundled_rg_path()
    if path is not None and _is_usable(path):
        return str(path.resolve())
    return None


@lru_cache(maxsize=1)
def _resolve_rg_binary_fallback() -> Optional[str]:
    """Cache vendor / PATH lookup only (env is checked on every call)."""
    return _from_icode_vendor() or shutil.which("rg")


def resolve_rg_binary() -> Optional[str]:
    """Return an absolute path to ``rg``, or ``None`` if unavailable.

    ``OPENJIUWEN_RG`` / ``ICODE_RG`` are read on every call so runtime
    changes take effect without requiring :func:`clear_rg_binary_cache`.
    """
    from_env = _from_env()
    if from_env is not None:
        return from_env
    return _resolve_rg_binary_fallback()


def clear_rg_binary_cache() -> None:
    """Drop the cached vendor/PATH resolution result (tests)."""
    _resolve_rg_binary_fallback.cache_clear()


__all__ = ["clear_rg_binary_cache", "resolve_rg_binary"]
