# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Markdown template loader for agent-team prompts.

Kept in its own module so ``sections`` can depend on the loader without
forcing the package ``__init__`` to be ready first.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from openjiuwen.core.foundation.prompt import PromptTemplate

_PROMPTS_DIR = Path(__file__).parent
_DEFAULT_LANGUAGE = "cn"


@cache
def _load(name: str, language: str) -> PromptTemplate:
    """Load a markdown template from the ``<lang>/<name>.md`` file."""
    path = _PROMPTS_DIR / language / f"{name}.md"
    return PromptTemplate(name=name, content=path.read_text(encoding="utf-8"))


def load_template(name: str, language: str = _DEFAULT_LANGUAGE) -> PromptTemplate:
    """Load a language-specific template from ``<lang>/<name>.md``."""
    return _load(name, language)


__all__ = ["load_template"]
