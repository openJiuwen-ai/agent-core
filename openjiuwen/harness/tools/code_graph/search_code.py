# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Candidate generation over definition symbols.

``find_code_symbols`` only produces candidates. Structure browsing and
relation hops belong to the other find_* tools, so a confident hit returns
``next_actions`` that point at them instead of inviting another reworded search.
The engine query is still ``service.search_code``.
"""

from __future__ import annotations

import re
from typing import Any

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext

# Kinds whose members usually matter before choosing an edit boundary.
_SHARED_KINDS = {"class", "module"}
_QUERY_NOISE_TOKENS = frozenset({"def", "class", "function", "method", "async"})


class FindCodeSymbolsTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("find_code_symbols", "FindCodeSymbolsTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        payload = dict(inputs)
        if payload.get("limit") in (None, ""):
            payload["limit"] = 5
        query = str(payload.get("query") or "").strip()
        if not query:
            return ToolOutput(success=False, error="query is required")
        kinds = payload.get("symbol_kinds")
        if kinds is not None and not isinstance(kinds, list):
            kinds = [kinds]
        include_tests = bool(payload.get("include_tests"))
        from openjiuwen.harness.tools.code_graph.session import (
            DIMINISHING_RETURN_STREAK,
            hash_search_query,
        )

        state = self.context.run_state
        query_hash = hash_search_query(
            query,
            symbol_kinds=kinds,
            path_prefix=payload.get("path_prefix"),
            include_tests=include_tests,
        )
        if state is not None and query_hash in state.query_hashes:
            repeated = self._repeat_response(query_hash)
            if repeated is not None:
                return repeated
        output = await self._invoke_service(
            lambda service: service.search_code(
                query,
                symbol_kinds=kinds,
                path_prefix=payload.get("path_prefix"),
                limit=self.policy.results(payload.get("limit") or self._default_results()),
                include_tests=include_tests,
            )
        )
        if not isinstance(output.data, dict):
            return output
        output.data["candidates_only"] = True
        matches = [item for item in (output.data.get("matches") or []) if isinstance(item, dict)]
        if "next_actions" not in output.data:
            if bool(getattr(state, "is_locate_exam", False)):
                actions = locate_search_next_actions(query, matches)
            else:
                actions = next_actions(query, matches)
            if actions:
                output.data["next_actions"] = actions
        if state is None:
            return output
        state.note_search(query_hash, [str(item.get("symbol_id") or "") for item in matches])
        if state.skips_locator_budget:
            state.search_cache[query_hash] = dict(output.data)
        elif state.diminishing_returns(DIMINISHING_RETURN_STREAK):
            self._note_diminishing_returns(output.data, state)
        self._persist_session()
        return output

    def _repeat_response(self, query_hash: str) -> ToolOutput | None:
        """Answer a query this run already asked.

        Graph runs (product and locate exam) may come back to the same query
        after an edit, so they get the cached answer plus advice. The
        bounded-episode error path is for non-graph profiles that still share
        this implementation.
        """
        state = self.context.run_state
        if state is None:
            return None
        if not state.skips_locator_budget:
            budget = self._touch_budget()
            if budget is not None:
                return budget
            self._persist_session()
            return ToolOutput(
                success=True,
                data=status_payload(
                    CodeGraphStatus.PARTIAL,
                    message=(
                        "query already searched in this localization session; "
                        f"change the query or call {state.terminal_tool_name} with PARTIAL"
                    ),
                    extra={"duplicate_query": True, "query_hash": query_hash},
                ),
            )
        self._touch_budget()
        cached = state.search_cache.get(query_hash)
        if cached is None:
            return None
        payload = dict(cached)
        payload["duplicate_query"] = True
        payload["message"] = (
            "same query as earlier in this task; cached result returned. "
            "Call read_symbol or a find_* relation tool instead of rewording the search."
        )
        return ToolOutput(success=True, data=payload)

    @staticmethod
    def _note_diminishing_returns(data: dict[str, Any], state: Any) -> None:
        data["diminishing_returns"] = True
        prior = str(data.get("message") or "").rstrip()
        suffix = f"; no new symbols in 3 queries; {state.terminal_tool_name} with PARTIAL or change strategy"
        data["message"] = (prior + suffix) if prior else suffix.lstrip("; ")
        warning = "diminishing returns: no new symbols in 3 consecutive searches"
        if warning not in state.warnings:
            state.warnings.append(warning)


def locate_search_next_actions(
    query: str,
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Locate-exam follow-ups as in run12.

    Named/confident hits suggest ``read_symbol`` only — not ``find_callers``,
    not whole-file ``read_file``. Unconfident lists stay unpinned; falling
    back to ``matches[0]`` was the run13 inversion and cut Symbol/Span/Line
    Coverage. Empty lists map ``search_text`` to ``search_source_text``.
    """
    if not matches:
        return [
            {
                "tool": "search_source_text",
                "reason": "no definition matched; look for the literal, message, or config key",
            }
        ]
    chosen = _chosen_match(query, matches)
    if chosen is None:
        return []
    return [
        {
            "tool": "read_symbol",
            "symbol_id": chosen.get("symbol_id"),
            "file": chosen.get("file"),
            "start_line": chosen.get("start_line"),
            "reason": f"read the definition of {chosen.get('name')}",
        }
    ]


def next_actions(
    query: str,
    matches: list[dict[str, Any]],
    state: Any = None,
) -> list[dict[str, Any]]:
    """Deterministic follow-ups for a product-graph search result.

    Derived from the named hit, or the top hit when the score gap is decisive.
    ``state`` is accepted for callers that share the locate signature.
    """
    _ = state
    if not matches:
        return [
            {
                "tool": "search_source_text",
                "reason": "no definition matched; look for the literal, message, or config key",
            }
        ]
    chosen = _chosen_match(query, matches)
    if chosen is None:
        production = [item for item in matches if not is_test_path(str(item.get("file") or ""))]
        chosen = (production or matches)[0]
    symbol_id = str(chosen.get("symbol_id") or "")
    name = str(chosen.get("name") or "")
    kind = str(chosen.get("kind") or "").lower()
    actions: list[dict[str, Any]] = [
        {
            "tool": "read_symbol",
            "symbol_id": symbol_id,
            "file": chosen.get("file"),
            "start_line": chosen.get("start_line"),
            "reason": f"verify the current implementation of {name}",
            "must_before": "edit",
        }
    ]
    if kind in _SHARED_KINDS:
        actions.append(
            {
                "tool": "inspect_code_structure",
                "file": chosen.get("file"),
                "reason": f"see the members of {name} before choosing a patch boundary",
                "must_before": "edit",
            }
        )
    else:
        actions.append(
            {
                "tool": "find_callers",
                "symbol_id": symbol_id,
                "reason": f"check who calls {name}",
                "must_before": "edit",
            }
        )
    return actions


def _chosen_match(query: str, matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The hit the query named, even when it is not rank 1.

    ``class TimeSeries`` used to offer nothing because BasePeriodogram scored
    slightly higher. A name that appears in the query is not a guess.
    """
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    named = _named_match(query, matches)
    if named is not None:
        return named
    if _is_confident(query, matches):
        return matches[0]
    return None


def _named_match(query: str, matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = _query_name_tokens(query)
    if not lowered:
        return None
    for match in matches[:5]:
        if str(match.get("name") or "").lower() in lowered:
            return match
    return None


def _query_name_tokens(query: str) -> set[str]:
    """Identifier tokens in the query. ``def alias(`` still names ``alias``."""
    return {
        part.lower()
        for part in re.split(r"[^A-Za-z0-9_]+", query or "")
        if part and part.lower() not in _QUERY_NOISE_TOKENS
    }


def _is_confident(query: str, matches: list[dict[str, Any]]) -> bool:
    """One candidate clearly stands out, so more searching cannot improve it."""
    if len(matches) == 1:
        return True
    if _named_match(query, matches) is not None:
        return True
    top = matches[0]
    top_score = float(top.get("score") or 0.0)
    second = float(matches[1].get("score") or 0.0)
    return top_score > 0 and second > 0 and top_score >= 2 * second
