# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lightweight i18n for agent team tool descriptions.

Each language is a flat ``STRINGS`` dict in its own module (``cn.py``,
``en.py``).  ``make_translator`` returns a closure bound to one language,
so multiple translators can coexist in the same process.

Tool ``_desc`` entries can also live in Markdown files under
``descs/<lang>/``.  Markdown files take precedence over ``STRINGS`` dict
entries.

Those files are grouped into domain sub-directories mirroring the tool
modules they describe (``team/``, ``member/``, ``task/``, ``message/``,
``async_task/``, ``workflow/``, ``workspace/``, ``common/``), but a
``desc_key`` stays a flat, globally unique name: the directory layout is an
organisational choice for humans and never appears in a key.  Resolution
goes through a per-language index built once from a recursive scan, so
moving a description between domains needs no code change.

``desc_key`` is usually the tool name, but a tool that ships in several
*variants* (same ``ToolCard.name``, different schema and behaviour) gives
each variant its own key — e.g. ``send_message`` and
``send_message_scheduled``.  The variant class picks its own key; this
module never learns what a variant is.

A ``_desc`` Markdown file may declare ``{{slot}}`` placeholders, each
filled from a shared fragment at ``descs/<lang>/fragments/<slot>.md``.
Fragments live in their own flat directory rather than under a domain —
they are reused *across* domains, and slot names form a namespace separate
from ``desc_key``, so the domain index skips that directory entirely.
Fragments are variant-agnostic prose reused across descriptions.  Slots are
enumerated from the template itself and every one of them must resolve, so
a missing fragment fails at tool-construction time rather than leaking a
raw ``{{slot}}`` literal into the model-facing description.

A slot may also describe an optional *capability*, in which case the tool
passes ``omit={"<slot>"}`` and the slot collapses to an empty string instead
of loading its fragment.  The caller names the omitted slots explicitly —
nothing is inferred — and the gate must be the same signal that shapes the
tool's schema, so prose describing a parameter and the parameter itself
always appear and disappear together.  A model that reads about a mechanism
it has no argument to invoke is worse off than one that never heard of it.

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
from typing import TYPE_CHECKING, Callable

from openjiuwen.core.foundation.prompt import PromptTemplate

if TYPE_CHECKING:
    from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache

Translator = Callable[..., str]
"""``(desc_key, key="_desc", *, omit=None, **kwargs) -> str`` — resolves a locale string."""

_DESCS_DIR = Path(__file__).parent / "descs"
_FRAGMENTS_DIRNAME = "fragments"

_SLOT_PATTERN = re.compile(r"\{\{(\w+)\}\}")

#: A newline followed by two or more blank lines. Templates put a blank line on
#: each side of a section-level slot, so omitting one collapses into a run that
#: has to be squeezed back down to a single paragraph break.
_BLANK_RUN_PATTERN = re.compile(r"\n(?:[ \t]*\n){2,}")


@cache
def _desc_index(lang: str) -> dict[str, Path]:
    """Map every ``desc_key`` under a language root to its Markdown file.

    Descriptions are filed under domain sub-directories for readability while
    ``desc_key`` stays flat, so the lookup is an index rather than a path
    join. Built once per language and cached; the ``fragments`` directory is
    skipped because slot names are a separate namespace.

    Args:
        lang: Language code naming the sub-directory of ``descs/``.

    Returns:
        ``desc_key`` (the file stem) to its absolute path; empty when the
        language has no directory at all.

    Raises:
        ValueError: when two files claim the same ``desc_key``. Picking one
            would make the description a model reads depend on directory
            walk order.
    """
    root = _DESCS_DIR / lang
    index: dict[str, Path] = {}
    for path in sorted(root.rglob("*.md")):
        if _FRAGMENTS_DIRNAME in path.relative_to(root).parts:
            continue
        clash = index.get(path.stem)
        if clash is not None:
            raise ValueError(f"Duplicate description key '{path.stem}' for language '{lang}': {clash} and {path}")
        index[path.stem] = path
    return index


@cache
def _load_desc(desc_key: str, lang: str) -> PromptTemplate | None:
    """Load a tool ``_desc`` from a Markdown file, cached.

    Returns ``None`` when no file exists so the caller can fall back
    to the in-module ``STRINGS`` dict. The cached object holds the
    *uninterpolated* template; ``PromptTemplate.format`` deep-copies its
    content, so filling slots never mutates the cache entry.
    """
    path = _desc_index(lang).get(desc_key)
    if path is None:
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
        raise FileNotFoundError(f"Missing description fragment '{slot}' for language '{lang}': expected {path}")
    return path.read_text(encoding="utf-8").strip()


def _render_desc(tmpl: PromptTemplate, desc_key: str, lang: str, omit: frozenset[str]) -> str:
    """Fill every ``{{slot}}`` in a ``_desc`` template, or fail loudly.

    Args:
        tmpl: The uninterpolated ``_desc`` template.
        desc_key: Description key, used only in error messages.
        lang: Language code the fragments are loaded for.
        omit: Slots whose capability is switched off; each collapses to an
            empty string and its fragment file is never read. Slots outside
            this set must still resolve. Spacing is normalised afterwards, so
            an omitted slot leaves no trace of where it used to sit.

    Raises:
        FileNotFoundError: when a declared slot has no fragment file.
        ValueError: when a placeholder survives interpolation.
    """
    slots = _slots_of(tmpl.content)
    if not slots:
        return tmpl.content
    fills = {slot: "" if slot in omit else _load_fragment(slot, lang) for slot in slots}
    # An omitted slot must leave the surrounding prose reading as if the slot
    # had never been there: collapse the blank-line run it leaves in the middle
    # of a template, and strip the one it leaves at either end. Both come from
    # the same convention -- a section-level slot sits on its own line with a
    # blank line on each side.
    rendered = _BLANK_RUN_PATTERN.sub("\n\n", tmpl.format(fills).content).strip()
    # Guard against a fragment carrying its own placeholder, and against the
    # assembler's silent "reinstate the {{literal}}" behaviour ever reaching a
    # model-facing string.
    if "{{" in rendered:
        raise ValueError(f"Unresolved placeholder left in description '{desc_key}' (language '{lang}')")
    return rendered


def make_translator(lang: str = "cn", ws_cache: "WorkspaceCache | None" = None) -> Translator:
    """Create a language-bound translator closure.

    Each call returns an independent closure — safe for concurrent use
    with different languages in the same process.

    Args:
        lang: Language code; anything other than ``"en"`` resolves to ``cn``.
        ws_cache: The team's resident ``WorkspaceCache``. When set, tool
            descriptions consult the team's evolved ``prompts/tool/`` files
            first: tool-level md via
            ``ws_cache.get_tool_md`` (rendered ``{{slot}}`` fragments),
            param-level via ``ws_cache.get_tool_param``. ``None`` (unit tests /
            ``evolution_enabled=false`` / single agent) resolves straight from
            the framework defaults — exactly the pre-evolvable behaviour.

    Returns:
        ``t(desc_key, key="_desc", *, omit=None, **kwargs) -> str``. ``kwargs``
        interpolate ``{key}`` placeholders in ``STRINGS`` values (runtime error
        messages use this); Markdown ``_desc`` slots are filled from shared
        fragments and take no ``kwargs``. ``omit`` names capability slots to
        collapse to empty (``_desc`` only) — see the module docstring.

    Read order (ws_cache set): tool-level ``ws_cache.get_tool_md`` → descs/ md →
    STRINGS ``_desc``; param-level ``ws_cache.get_tool_param`` → STRINGS.
    """
    if lang == "en":
        from openjiuwen.agent_teams.tools.locales import en as mod
    else:
        from openjiuwen.agent_teams.tools.locales import cn as mod
    strings: dict[str, str] = mod.STRINGS

    def t(desc_key: str, key: str = "_desc", *, omit: frozenset[str] | None = None, **kwargs: str) -> str:
        # Consult the team workspace first when a cache is bound.
        if ws_cache is not None:
            if key == "_desc":
                evolved = ws_cache.get_tool_md(desc_key)
                if evolved is not None:
                    return _render_desc(
                        PromptTemplate(name=f"{desc_key}._desc", content=evolved),
                        desc_key,
                        lang,
                        omit or frozenset(),
                    )
            else:
                evolved = ws_cache.get_tool_param(desc_key, key)
                if evolved is not None:
                    return evolved.format_map(kwargs) if kwargs else evolved
        # Framework fallback — the original resolution path.
        if key == "_desc":
            tmpl = _load_desc(desc_key, lang)
            if tmpl is not None:
                return _render_desc(tmpl, desc_key, lang, omit or frozenset())
            dict_key = f"{desc_key}._desc"
            if dict_key not in strings:
                raise FileNotFoundError(
                    f"Missing description for tool '{desc_key}' in language '{lang}': "
                    f"expected Markdown at {_DESCS_DIR / lang}/<domain>/{desc_key}.md "
                    f"or STRINGS['{dict_key}']"
                )
            return strings[dict_key]
        raw = strings[f"{desc_key}.{key}"]
        return raw.format_map(kwargs) if kwargs else raw

    return t


__all__ = ["Translator", "make_translator"]
