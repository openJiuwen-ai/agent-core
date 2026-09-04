# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt loading for ReflACT skill training."""

from __future__ import annotations

import os
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, str] = {}


def _read_file(path: Path) -> str | None:
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    _CACHE[key] = content
    return content


def load_prompt(name: str, env: str | None = None) -> str:
    """Load prompt with env-specific override then generic fallback."""
    if env is not None:
        env_path = _PACKAGE_DIR / "envs" / env / "prompts" / f"{name}.md"
        content = _read_file(env_path)
        if content is not None:
            return content

    generic_path = _PACKAGE_DIR / "prompts" / f"{name}.md"
    content = _read_file(generic_path)
    if content is not None:
        return content

    searched = []
    if env is not None:
        searched.append(str(_PACKAGE_DIR / "envs" / env / "prompts" / f"{name}.md"))
    searched.append(str(_PACKAGE_DIR / "prompts" / f"{name}.md"))
    raise FileNotFoundError(f"Prompt '{name}' not found. Searched: {', '.join(searched)}")


def clear_cache() -> None:
    """Clear prompt cache (for tests)."""
    _CACHE.clear()
