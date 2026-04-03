# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lightweight i18n for agent team tool descriptions.

Each language is a flat ``STRINGS`` dict in its own module (``cn.py``,
``en.py``).  ``make_translator`` returns a closure bound to one language,
so multiple translators can coexist in the same process.
"""
from __future__ import annotations

from typing import Callable

Translator = Callable[..., str]
"""``(tool, key="_desc", **kwargs) -> str`` — resolves a locale string."""


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
        raw = strings[f"{tool}.{key}"]
        return raw.format_map(kwargs) if kwargs else raw

    return t
