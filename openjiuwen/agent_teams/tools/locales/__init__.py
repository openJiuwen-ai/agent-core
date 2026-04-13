# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lightweight i18n for agent team tool descriptions.

Each language is a flat ``STRINGS`` dict in its own module (``cn.py``,
``en.py``).  ``make_translator`` returns a closure bound to one language,
so multiple translators can coexist in the same process.

Tool ``_desc`` entries can also live in Markdown files under
``descs/<lang>/<tool_name>.md``.  Markdown files take precedence over
``STRINGS`` dict entries and support ``{{placeholder}}`` interpolation
via :class:`PromptTemplate`.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Callable

from openjiuwen.core.foundation.prompt import PromptTemplate

Translator = Callable[..., str]
"""``(tool, key="_desc", **kwargs) -> str`` — resolves a locale string."""

_DESCS_DIR = Path(__file__).parent / "descs"


@cache
def _load_desc(tool: str, lang: str) -> PromptTemplate | None:
    """Load a tool ``_desc`` from a Markdown file, cached.

    Returns ``None`` when no file exists so the caller can fall back
    to the in-module ``STRINGS`` dict.
    """
    path = _DESCS_DIR / lang / f"{tool}.md"
    if not path.is_file():
        return None
    return PromptTemplate(name=f"{tool}._desc", content=path.read_text(encoding="utf-8").strip())


def make_translator(lang: str = "cn") -> Translator:
    """Create a language-bound translator closure.

    Each call returns an independent closure — safe for concurrent use
    with different languages in the same process.
    """
    if lang == "en":
        from openjiuwen.agent_teams.tools.locales import en as mod
    else:
        from openjiuwen.agent_teams.tools.locales import cn as mod
    strings: dict[str, str] = mod.STRINGS

    def t(tool: str, key: str = "_desc", **kwargs: str) -> str:
        if key == "_desc":
            tmpl = _load_desc(tool, lang)
            if tmpl is not None:
                return tmpl.format(kwargs).content if kwargs else tmpl.content
        raw = strings[f"{tool}.{key}"]
        return raw.format_map(kwargs) if kwargs else raw

    return t
