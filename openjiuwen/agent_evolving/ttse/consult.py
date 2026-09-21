# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ttse_consult``: one read-only tool for FACT/TIP retrieval.

Both ``category`` and ``query`` are required. ``category`` is one catalog id,
comma-separated ids, or the tool-only token ``all`` to search the whole bank.
``all`` is not a stored classification id.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, NamedTuple, Optional, Sequence

from .categories import category_ids, normalize_category
from .render import QUERY_GUIDANCE_EN, build_section_text

TTSE_CONSULT_TOOL_NAME = "ttse_consult"
CONSULT_ALL_CATEGORY = "all"
DEFAULT_TOP_K = 8
DEFAULT_RRF_K = 60
_SPLIT_IDS = re.compile(r"[,;|\s]+")

_CONSULT_DESCRIPTION = (
    "Retrieve FACT and TIP rule bodies. "
    "The category listing and counts are already in the trailing attachment — "
    "do not call this tool to re-list the catalog. "
    "Set category to one listed id, several related ids separated by commas, "
    "or all to search the whole bank (all is not a stored class; do not mix "
    "all with catalog ids). "
    f"{QUERY_GUIDANCE_EN} "
    "Open only categories relevant to the current task. "
    "Do not use bash or read_file to scan the experience bank."
)


def clamp_top_k(
    value: Any,
    *,
    default: int = DEFAULT_TOP_K,
    max_rules: int = 40,
) -> int:
    """Parse and clamp ``top_k`` from a server-side setting."""
    fallback = default if isinstance(default, int) and default > 0 else DEFAULT_TOP_K
    cap = max_rules if isinstance(max_rules, int) and max_rules > 0 else fallback
    parsed: Optional[int]
    try:
        if value is None or value == "":
            parsed = fallback
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    if parsed <= 0:
        parsed = fallback
    return min(parsed, cap)


def _effective_top_k(
    store: Any,
    top_k: Any,
    *,
    default: int,
    max_rules: int,
) -> int:
    """Prefer an explicit value, else live ``TTSEConfig.consult_top_k``."""
    if top_k is None or top_k == "":
        cfg = getattr(store, "_config", None)
        top_k = getattr(cfg, "consult_top_k", None) if cfg is not None else None
    return clamp_top_k(top_k, default=default, max_rules=max_rules)


def _truncate(text: str, max_chars: int) -> str:
    body = text or ""
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    return body[: max(0, max_chars - 20)].rstrip() + "\n… [truncated]\n"


def parse_consult_query(query: Any) -> str:
    if query is None:
        return ""
    return str(query).strip()


def _dedupe(ids: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def parse_consult_categories(category: Any) -> List[str]:
    """Split ``category`` into unique tokens, preserving order."""
    if category is None:
        return []
    if isinstance(category, (list, tuple)):
        parts = [str(item).strip().strip("`") for item in category]
        return _dedupe([part for part in parts if part])
    raw = str(category).strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return parse_consult_categories(parsed)
    parts = [part.strip().strip("`") for part in _SPLIT_IDS.split(raw)]
    return _dedupe([part for part in parts if part])


def parse_consult_category(category: Any) -> str:
    """Return a single token, or empty when missing or more than one id."""
    parts = parse_consult_categories(category)
    return parts[0] if len(parts) == 1 else ""


def _is_all_category(token: str) -> bool:
    return token.lower() == CONSULT_ALL_CATEGORY


def _unknown_message(unknown: Sequence[str]) -> str:
    if len(unknown) == 1:
        return (
            f"Unknown category `{unknown[0]}`. Use an id from the trailing catalog "
            f"attachment, or `{CONSULT_ALL_CATEGORY}` to search the whole bank."
        )
    listed = ", ".join(f"`{item}`" for item in unknown)
    return (
        f"Unknown category {listed}. Use an id from the trailing catalog "
        f"attachment, or `{CONSULT_ALL_CATEGORY}` to search the whole bank."
    )


class _ConsultScope(NamedTuple):
    """Parsed ttse_consult category argument."""

    known: List[str]
    unknown: List[str]
    blocking: List[str]
    wants_all: bool

    def should_fail(self, errors: Sequence[str], query_text: str) -> bool:
        """True when consult should return validation errors instead of retrieving."""
        if not errors:
            return False
        if self.blocking or not query_text:
            return True
        if self.known or self.wants_all:
            return False
        return bool(self.unknown)


def _resolve_categories(category: Any) -> _ConsultScope:
    """Return known ids, unknown tokens, blocking errors, and whether ``all`` was used."""
    tokens = parse_consult_categories(category)
    blocking: List[str] = []
    if not tokens:
        blocking.append(
            "category is required. Pick one or more ids from the trailing catalog "
            f"attachment, or `{CONSULT_ALL_CATEGORY}` to search the whole bank. "
            "Do not call ttse_consult to re-list the catalog; it is already attached."
        )
        return _ConsultScope([], [], blocking, False)

    wants_all = any(_is_all_category(token) for token in tokens)
    others = [token for token in tokens if not _is_all_category(token)]
    if wants_all and others:
        blocking.append(
            f"`{CONSULT_ALL_CATEGORY}` already searches the whole bank. "
            "Do not mix it with catalog ids."
        )
        return _ConsultScope([], [], blocking, False)

    if wants_all:
        return _ConsultScope([], [], blocking, True)

    allowed = set(category_ids())
    known: List[str] = []
    unknown: List[str] = []
    for raw in others:
        if raw not in allowed:
            unknown.append(raw)
            continue
        known.append(normalize_category(raw))
    return _ConsultScope(known, unknown, blocking, False)


def consult_arg_errors(category: Any, query: Any) -> List[str]:
    """Warning lines when tool arguments are missing or invalid."""
    errors: List[str] = []
    query_text = parse_consult_query(query)
    if not query_text:
        errors.append(
            "query is required. " + QUERY_GUIDANCE_EN
        )

    scope = _resolve_categories(category)
    errors.extend(scope.blocking)
    if scope.blocking:
        return errors
    if scope.unknown and not scope.known and not scope.wants_all:
        errors.append(_unknown_message(scope.unknown))
    return errors


def _maybe_mark_injected(store: Any, facts, tips) -> None:
    marker = getattr(store, "mark_injected", None)
    if not callable(marker):
        return
    marker([*(facts or []), *(tips or [])])


def _format_hits(facts, tips, *, cid: str) -> str:
    if not facts and not tips:
        if cid == CONSULT_ALL_CATEGORY:
            return "No FACT/TIP rules matched the query."
        return f"No FACT/TIP rules in category `{cid}` matched the query."
    body = build_section_text(facts, tips, retrieved=True)
    if cid == CONSULT_ALL_CATEGORY:
        return body
    return f"## `{cid}`\n\n{body.rstrip()}\n"


def _join_blocks(notes: Sequence[str], bodies: Sequence[str], *, max_chars: int) -> str:
    blocks: List[str] = []
    if notes:
        blocks.append("\n".join(notes))
    blocks.extend(body.rstrip() for body in bodies if body)
    if not blocks:
        return ""
    return _truncate("\n\n".join(blocks) + "\n", max_chars)


async def _retrieve_one(
    store: Any,
    *,
    scope: Optional[str],
    query: str,
    top_k: int,
    max_rules: int,
    rrf_k: int,
    mark_injected: bool,
    label: str,
) -> str:
    result = await store.index.retrieve(
        store,
        category=scope,
        query=query,
        top_k=top_k,
        rrf_k=rrf_k,
    )
    facts, tips = list(result.facts), list(result.tips)
    if max_rules > 0:
        facts = facts[:max_rules]
        tips = tips[:max_rules]
    if mark_injected:
        _maybe_mark_injected(store, facts, tips)
    return _format_hits(facts, tips, cid=label)


async def render_consult_result_async(
    store: Any,
    *,
    category: Any = "",
    query: Any = "",
    top_k: Any = None,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
    mark_injected: bool = True,
) -> str:
    """Retrieve FACT/TIP for one or more categories, or the whole bank when ``all``."""
    query_text = parse_consult_query(query)
    scope = _resolve_categories(category)
    errors = consult_arg_errors(category, query)
    if scope.should_fail(errors, query_text):
        return "\n".join(errors)

    notes: List[str] = []
    if scope.unknown:
        notes.append(_unknown_message(scope.unknown))
    limit = _effective_top_k(store, top_k, default=default_top_k, max_rules=max_rules)
    if scope.wants_all:
        body = await _retrieve_one(
            store,
            scope=None,
            query=query_text,
            top_k=limit,
            max_rules=max_rules,
            rrf_k=rrf_k,
            mark_injected=mark_injected,
            label=CONSULT_ALL_CATEGORY,
        )
        return _join_blocks(notes, [body], max_chars=max_chars)

    bodies: List[str] = []
    for cid in scope.known:
        bodies.append(
            await _retrieve_one(
                store,
                scope=cid,
                query=query_text,
                top_k=limit,
                max_rules=max_rules,
                rrf_k=rrf_k,
                mark_injected=mark_injected,
                label=cid,
            )
        )
    return _join_blocks(notes, bodies, max_chars=max_chars)


def render_consult_result(
    store: Any,
    *,
    category: Any = "",
    query: Any = "",
    max_chars: int = 8000,
    max_rules: int = 40,
) -> str:
    """Validate arguments. Retrieval itself is async; invalid calls return warnings."""
    del store, max_rules
    errors = consult_arg_errors(category, query)
    if errors:
        return "\n".join(errors)
    scope = _resolve_categories(category)
    notes: List[str] = []
    if scope.unknown:
        notes.append(_unknown_message(scope.unknown))
    if scope.wants_all:
        notes.append("arguments look valid; use async retrieval for `{all}`.")
    elif scope.known:
        notes.append(
            "arguments look valid for: " + ", ".join(f"`{cid}`" for cid in scope.known) + "."
        )
    return _truncate("\n".join(notes), max_chars)


def create_ttse_consult_tool(
    store: Any,
    *,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> Any:
    """Build the rail-owned consult tool bound to ``store``."""
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    async def ttse_consult(category: Any = None, query: Any = None) -> str:
        return await render_consult_result_async(
            store,
            category=category,
            query=query,
            max_chars=max_chars,
            max_rules=max_rules,
            default_top_k=default_top_k,
            rrf_k=rrf_k,
        )

    card = ToolCard(
        id=TTSE_CONSULT_TOOL_NAME,
        name=TTSE_CONSULT_TOOL_NAME,
        description=_CONSULT_DESCRIPTION,
        input_params={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "One catalog id, several related ids separated by commas, "
                        f"or `{CONSULT_ALL_CATEGORY}` to search the whole bank. "
                        f"`{CONSULT_ALL_CATEGORY}` is not a stored class and must "
                        "not be mixed with catalog ids."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": QUERY_GUIDANCE_EN,
                },
            },
            "required": ["category", "query"],
        },
        parallel_safe=True,
        idempotent=True,
        stateless=False,
    )
    return LocalFunction(card=card, func=ttse_consult)


def create_ttse_consult_tools(
    store: Any,
    *,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> List[Any]:
    return [
        create_ttse_consult_tool(
            store,
            max_chars=max_chars,
            max_rules=max_rules,
            default_top_k=default_top_k,
            rrf_k=rrf_k,
        )
    ]


__all__ = [
    "TTSE_CONSULT_TOOL_NAME",
    "CONSULT_ALL_CATEGORY",
    "create_ttse_consult_tool",
    "create_ttse_consult_tools",
    "parse_consult_category",
    "parse_consult_categories",
    "parse_consult_query",
    "consult_arg_errors",
    "render_consult_result",
    "render_consult_result_async",
    "clamp_top_k",
]
