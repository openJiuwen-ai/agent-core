# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lightweight i18n for agent team tool descriptions.

Each language is a flat ``STRINGS`` dict in its own module (``cn.py``,
``en.py``).  ``make_translator`` returns a closure bound to one language,
so multiple translators can coexist in the same process.

Tool ``_desc`` entries can also live in Markdown files under
``descs/<lang>/<desc_key>.md``.  Markdown files take precedence over
``STRINGS`` dict entries.

``desc_key`` is usually the tool name, but a tool that ships in several
*variants* (same ``ToolCard.name``, different schema and behaviour) gives
each variant its own key — e.g. ``send_message`` and
``send_message_scheduled``.  The variant class picks its own key; this
module never learns what a variant is.

A ``_desc`` Markdown file may declare ``{{slot}}`` placeholders, each
filled from a shared fragment at ``descs/<lang>/fragments/<slot>.md``.
Fragments are variant-agnostic prose reused across descriptions.  Slots are
enumerated from the template itself and every one of them must resolve, so
a missing fragment fails at tool-construction time rather than leaking a
raw ``{{slot}}`` literal into the model-facing description.

Two interpolation paths exist and must not be confused: Markdown ``_desc``
slots use ``{{slot}}`` and are filled from fragments by this module, while
``STRINGS`` values use ``{key}`` / ``str.format_map`` and are filled by the
caller's ``**kwargs`` (only runtime error messages do this — parameter
descriptions are plain literals).  Text that varies belongs in a Markdown
slot or a variant-specific key, never in an interpolated ``STRINGS`` value.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Callable

from openjiuwen.core.foundation.prompt import PromptTemplate

Translator = Callable[..., str]
"""``(desc_key, key="_desc", **kwargs) -> str`` — resolves a locale string."""

_DESCS_DIR = Path(__file__).parent / "descs"
_FRAGMENTS_DIRNAME = "fragments"

_SLOT_PATTERN = re.compile(r"\{\{(\w+)\}\}")


@cache
def _load_desc(desc_key: str, lang: str) -> PromptTemplate | None:
    """Load a tool ``_desc`` from a Markdown file, cached.

    Returns ``None`` when no file exists so the caller can fall back
    to the in-module ``STRINGS`` dict. The cached object holds the
    *uninterpolated* template; ``PromptTemplate.format`` deep-copies its
    content, so filling slots never mutates the cache entry.
    """
    path = _DESCS_DIR / lang / f"{desc_key}.md"
    if not path.is_file():
        return None
    return PromptTemplate(name=f"{desc_key}._desc", content=path.read_text(encoding="utf-8").strip())


@cache
def _slots_of(content: str) -> tuple[str, ...]:
    """Return the ordered, de-duplicated ``{{slot}}`` names in a template."""
    return tuple(dict.fromkeys(_SLOT_PATTERN.findall(content)))


@cache
def _load_fragment(slot: str, lang: str) -> str:
    """Load one shared description fragment, cached.

    Raises:
        FileNotFoundError: when the fragment file does not exist. There is no
            fallback — a missing fragment is a wiring bug, and rendering the
            description without it would ship an incomplete behavioural
            contract to the model.
    """
    path = _DESCS_DIR / lang / _FRAGMENTS_DIRNAME / f"{slot}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing description fragment '{slot}' for language '{lang}': expected {path}"
        )
    return path.read_text(encoding="utf-8").strip()


def _render_desc(tmpl: PromptTemplate, desc_key: str, lang: str) -> str:
    """Fill every ``{{slot}}`` in a ``_desc`` template, or fail loudly.

    Raises:
        FileNotFoundError: when a declared slot has no fragment file.
        ValueError: when a placeholder survives interpolation.
    """
    slots = _slots_of(tmpl.content)
    if not slots:
        return tmpl.content
    fills = {slot: _load_fragment(slot, lang) for slot in slots}
    rendered = tmpl.format(fills).content
    # Guard against a fragment carrying its own placeholder, and against the
    # assembler's silent "reinstate the {{literal}}" behaviour ever reaching a
    # model-facing string.
    if "{{" in rendered:
        raise ValueError(
            f"Unresolved placeholder left in description '{desc_key}' (language '{lang}')"
        )
    return rendered


def make_translator(lang: str = "cn") -> Translator:
    """Create a language-bound translator closure.

    Each call returns an independent closure — safe for concurrent use
    with different languages in the same process.

    Args:
        lang: Language code; anything other than ``"en"`` resolves to ``cn``.

    Returns:
        ``t(desc_key, key="_desc", **kwargs) -> str``. ``kwargs`` interpolate
        ``{key}`` placeholders in ``STRINGS`` values (runtime error messages
        use this); Markdown ``_desc`` slots are filled from shared fragments
        and take no ``kwargs``.
    """
    if lang == "en":
        from openjiuwen.agent_teams.tools.locales import en as mod
    else:
        from openjiuwen.agent_teams.tools.locales import cn as mod
    strings: dict[str, str] = mod.STRINGS

    def t(desc_key: str, key: str = "_desc", **kwargs: str) -> str:
        if key == "_desc":
            tmpl = _load_desc(desc_key, lang)
            if tmpl is not None:
                return _render_desc(tmpl, desc_key, lang)
            dict_key = f"{desc_key}._desc"
            if dict_key not in strings:
                raise FileNotFoundError(
                    f"Missing description for tool '{desc_key}' in language '{lang}': "
                    f"expected Markdown at {_DESCS_DIR / lang / f'{desc_key}.md'} "
                    f"or STRINGS['{dict_key}']"
                )
        raw = strings[f"{desc_key}.{key}"]
        return raw.format_map(kwargs) if kwargs else raw

    return t
