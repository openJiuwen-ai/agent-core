# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ACI observation helpers for ``retrieval_interface=focused``.

Classic find_* payloads stay untouched. This module only reshapes results
when ``CodeGraphRunState.uses_focused`` is true: short candidates, role
groups, one next action, and exact-literal line hits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path

FOCUSED_MAX_CANDIDATES = 5
FOCUS_WINDOW_MIN = 50
FOCUS_WINDOW_MAX = 100
FOCUS_WINDOW_TARGET = 80
ROLE_IMPLEMENTATION = "implementation"
ROLE_TEST = "test"
ROLE_BUILD = "build"
_BUILD_NAME_MARKERS = frozenset(
    {
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "makefile",
        "dockerfile",
        "package.json",
        "cargo.toml",
        "go.mod",
    }
)
_BUILD_DIR_MARKERS = (
    "vendor/",
    "third_party/",
    "node_modules/",
    "dist/",
    "build/",
    "docs/",
    "doc/",
    "generated/",
    "_generated/",
)
_QUERY_NOISE_TOKENS = frozenset({"def", "class", "function", "method", "async"})
_CALLER_HINTS = ("caller", "callers", "who calls", "who call")
_IMPORT_HINTS = ("register", "decorator", "importer", "importers", "import site")


def classify_role(file_path: str) -> str:
    """implementation / test / build. Tests stay visible; vendor/docs drop rank."""
    rel = str(file_path or "").replace("\\", "/").lstrip("./")
    lowered = rel.lower()
    name = Path(rel).name.lower()
    if is_test_path(rel):
        return ROLE_TEST
    if name in _BUILD_NAME_MARKERS or any(marker in lowered for marker in _BUILD_DIR_MARKERS):
        return ROLE_BUILD
    return ROLE_IMPLEMENTATION


def infer_match_mode(query: str) -> str:
    """``exact`` for literals the prompt already promised; else ``lexical``."""
    text = str(query or "").strip()
    if len(text) >= 2 and text[0] in "\"'`" and text[-1] == text[0]:
        return "exact"
    if text.startswith("@"):
        return "exact"
    if re.search(r"(Error|Exception|Traceback|Warning)", text):
        return "exact"
    if re.fullmatch(r"[A-Za-z_][\w.]*", text) and ("." in text or "_" in text or text.isupper()):
        return "exact"
    return "lexical"


def strip_literal_quotes(query: str) -> str:
    text = str(query or "").strip()
    if len(text) >= 2 and text[0] in "\"'`" and text[-1] == text[0]:
        return text[1:-1]
    return text


def signature_of(item: dict[str, Any]) -> str:
    if str(item.get("signature") or "").strip():
        return str(item.get("signature")).strip()
    name = str(item.get("name") or "").strip()
    kind = str(item.get("kind") or "").strip().lower()
    if not name:
        return ""
    if kind == "class":
        return f"class {name}"
    if kind in {"function", "method"}:
        return f"def {name}"
    return name


def lines_of(item: dict[str, Any]) -> str:
    start = item.get("start_line")
    end = item.get("end_line")
    if start in (None, ""):
        return ""
    try:
        start_i = int(start)
    except (TypeError, ValueError):
        return ""
    if end in (None, ""):
        return str(start_i)
    try:
        end_i = int(end)
    except (TypeError, ValueError):
        return str(start_i)
    if end_i == start_i:
        return str(start_i)
    return f"{start_i}-{end_i}"


def summarize_hit(
    item: dict[str, Any],
    *,
    matched_by: list[str],
    matched_line: str = "",
) -> dict[str, Any]:
    """One decision-sized candidate. No definition body."""
    file_path = str(item.get("file") or "").replace("\\", "/")
    start = item.get("start_line")
    end = item.get("end_line")
    try:
        start_i = int(start) if start not in (None, "") else 0
    except (TypeError, ValueError):
        start_i = 0
    try:
        end_i = int(end) if end not in (None, "") else start_i
    except (TypeError, ValueError):
        end_i = start_i
    score = item.get("score")
    try:
        score_f = round(float(score), 3) if score not in (None, "") else None
    except (TypeError, ValueError):
        score_f = None
    summary = {
        "symbol_id": str(item.get("symbol_id") or ""),
        "name": str(item.get("name") or ""),
        "kind": str(item.get("kind") or ""),
        "role": classify_role(file_path),
        "file": file_path,
        "start_line": start_i,
        "end_line": end_i,
        "lines": lines_of({"start_line": start_i, "end_line": end_i}),
        "signature": signature_of(item),
        "matched_line": str(matched_line or item.get("matched_line") or "").strip(),
        "matched_by": list(matched_by),
    }
    if score_f is not None:
        summary["score"] = score_f
    return summary


def register_focused_candidates(
    state: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Assign stable C1..Cn ids for this run and remember them for focus_code."""
    registered: list[dict[str, Any]] = []
    for item in items[:FOCUSED_MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        state.focus_seq = int(getattr(state, "focus_seq", 0) or 0) + 1
        candidate_id = f"C{state.focus_seq}"
        payload = dict(item)
        payload["candidate_id"] = candidate_id
        state.focused_candidates[candidate_id] = payload
        registered.append(payload)
    return registered


def group_candidates(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {
        ROLE_IMPLEMENTATION: [],
        ROLE_TEST: [],
        ROLE_BUILD: [],
    }
    for item in candidates:
        role = str(item.get("role") or ROLE_IMPLEMENTATION)
        groups.setdefault(role, []).append(item)
    return {key: value for key, value in groups.items() if value}


def _query_name_tokens(query: str) -> set[str]:
    return {
        part.lower()
        for part in re.split(r"[^A-Za-z0-9_]+", query or "")
        if part and part.lower() not in _QUERY_NOISE_TOKENS
    }


def _named_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    tokens = _query_name_tokens(query)
    if not tokens:
        return None
    for item in candidates:
        if str(item.get("name") or "").lower() in tokens:
            return item
    return None


def _is_confident(candidates: list[dict[str, Any]]) -> bool:
    if len(candidates) == 1:
        return True
    top = float(candidates[0].get("score") or 0.0)
    second = float(candidates[1].get("score") or 0.0)
    return top > 0 and second > 0 and top >= 2 * second


def focused_next_actions(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    issue: str = "",
    empty_hint: str = "lexical",
) -> list[dict[str, Any]]:
    """One preferred action. Relation hops are not the default."""
    if not candidates:
        return [
            {
                "tool": "search_source_text",
                "match_mode": empty_hint,
                "reason": "search succeeded but no matches; change match_mode or narrow path_prefix",
            }
        ]
    issue_text = f"{issue} {query}".lower()
    if any(hint in issue_text for hint in _CALLER_HINTS):
        chosen = candidates[0]
        symbol_id = str(chosen.get("symbol_id") or "")
        if symbol_id:
            return [
                {
                    "tool": "find_callers",
                    "symbol_id": symbol_id,
                    "reason": "the issue asks who calls this symbol",
                }
            ]
    if any(hint in issue_text for hint in _IMPORT_HINTS):
        chosen = candidates[0]
        symbol_id = str(chosen.get("symbol_id") or "")
        if symbol_id:
            return [
                {
                    "tool": "find_importers",
                    "symbol_id": symbol_id,
                    "reason": "the issue asks about registration or imports",
                }
            ]
    impl = [item for item in candidates if item.get("role") == ROLE_IMPLEMENTATION]
    pool = impl or list(candidates)
    names = [str(item.get("name") or "") for item in pool]
    if len(pool) > 1 and names[0] and len(set(names)) == 1 and _named_candidate(query, pool) is None:
        return [
            {
                "tool": "resolve_symbol",
                "name": names[0],
                "reason": "several symbols share this name; resolve or add path_prefix",
            }
        ]
    chosen = _named_candidate(query, pool)
    if chosen is None and _is_confident(pool):
        chosen = pool[0]
    if chosen is None:
        chosen = pool[0]
    kind = str(chosen.get("kind") or "").lower()
    if kind in {"class", "module", "file"}:
        return [
            {
                "tool": "inspect_code_structure",
                "file": chosen.get("file"),
                "reason": f"see the members of {chosen.get('name') or chosen.get('file')} before focusing",
            }
        ]
    return [
        {
            "tool": "focus_code",
            "candidate_id": chosen.get("candidate_id"),
            "reason": f"open a 50-100 line window on {chosen.get('name') or chosen.get('symbol_id')}",
        }
    ]


def focus_reminder(state: Any) -> str:
    focus = getattr(state, "current_focus", None) or {}
    if not focus:
        return ""
    label = focus.get("candidate_id") or focus.get("symbol_id") or focus.get("file")
    return f"a focus_code target is already set ({label}); edit that window or focus another candidate"


def apply_focused_observation(
    data: dict[str, Any],
    *,
    query: str,
    state: Any,
    raw_items: list[dict[str, Any]],
    matched_by: list[str],
    empty_hint: str = "lexical",
) -> dict[str, Any]:
    """Replace ranked lists with summary candidates. Classic callers never enter."""
    summaries = [
        summarize_hit(item, matched_by=matched_by)
        for item in raw_items
        if isinstance(item, dict)
    ]
    truncated = len(summaries) > FOCUSED_MAX_CANDIDATES
    candidates = register_focused_candidates(state, summaries)
    groups = group_candidates(candidates)
    issue = str(getattr(getattr(state, "request", None), "query", "") or "")
    actions = focused_next_actions(query, candidates, issue=issue, empty_hint=empty_hint)
    if not candidates:
        data["status"] = data.get("status") or "NO_MATCH"
        data["message"] = "search succeeded but no matches; narrow the query or path_prefix"
    else:
        data["status"] = "COMPLETE"
        extra = "; narrow the query or path_prefix" if truncated else ""
        data["message"] = (
            f"found {len(candidates)} summary candidate(s); "
            f"pick one with focus_code{extra}"
        )
    reminder = focus_reminder(state)
    if reminder:
        prior = str(data.get("message") or "").rstrip()
        data["message"] = f"{prior}; {reminder}" if prior else reminder
    data["candidates"] = candidates
    data["groups"] = groups
    data["candidates_only"] = True
    data["next_actions"] = actions
    # Keep remember_payload working, but drop bulky classic lists from the prompt.
    data["matches"] = candidates
    if "chunks" in data:
        data["chunks"] = candidates
    return data


def focus_line_window(
    start_line: int,
    end_line: int,
    *,
    matched_line: int | None = None,
    file_len: int | None = None,
) -> tuple[int, int]:
    """Clamp a symbol span to the 50–100 line ACI window."""
    start = max(1, int(start_line or 1))
    end = max(start, int(end_line or start))
    span = end - start + 1
    if FOCUS_WINDOW_MIN <= span <= FOCUS_WINDOW_MAX:
        window = (start, end)
    elif span > FOCUS_WINDOW_MAX:
        center = int(matched_line or start)
        half = FOCUS_WINDOW_MAX // 2
        left = max(1, center - half)
        window = (left, left + FOCUS_WINDOW_MAX - 1)
    else:
        pad = (FOCUS_WINDOW_TARGET - span) // 2
        left = max(1, start - pad)
        right = left + FOCUS_WINDOW_TARGET - 1
        window = (left, right)
    left, right = window
    if file_len is not None and file_len > 0:
        right = min(right, file_len)
        if right - left + 1 < min(span, FOCUS_WINDOW_MAX):
            left = max(1, right - min(FOCUS_WINDOW_MAX, file_len) + 1)
    return left, max(left, right)


def _enclosing_symbol(index: Any, file_path: str, line: int) -> Any | None:
    by_file = getattr(index, "by_file", None) or {}
    symbols = getattr(index, "symbols", None) or {}
    best = None
    best_span = None
    for symbol_id in by_file.get(file_path, []):
        symbol = symbols.get(symbol_id)
        if symbol is None:
            continue
        start = int(getattr(symbol, "start_line", 0) or 0)
        end = int(getattr(symbol, "end_line", 0) or 0)
        if start <= line <= end:
            span = end - start
            kind = str(getattr(symbol, "kind", "") or "").lower()
            # Prefer the smallest function/method over a wrapping class.
            rank = (0 if kind in {"function", "method"} else 1, span)
            if best is None or rank < best_span:
                best = symbol
                best_span = rank
    return best


def exact_line_hits(
    *,
    repo_root: str,
    index: Any,
    query: str,
    path_prefix: str | None,
    limit: int,
    ban_tests: bool,
) -> list[dict[str, Any]]:
    """Literal substring hits. Does not change the BM25 formula."""
    needle = strip_literal_quotes(query)
    if not needle:
        return []
    prefix = path_prefix.replace("\\", "/").lstrip("./") if path_prefix else None
    root = Path(repo_root)
    files = list((getattr(index, "by_file", None) or {}).keys()) if index is not None else []
    hits: list[dict[str, Any]] = []
    for rel in files:
        file_path = str(rel).replace("\\", "/")
        if prefix and not file_path.startswith(prefix):
            continue
        if ban_tests and is_test_path(file_path):
            continue
        path = root / file_path
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            if needle not in line:
                continue
            symbol = _enclosing_symbol(index, file_path, line_no) if index is not None else None
            hit: dict[str, Any] = {
                "file": file_path,
                "start_line": line_no,
                "end_line": line_no,
                "matched_line": line.strip(),
                "kind": str(getattr(symbol, "kind", "") or "text"),
                "name": str(getattr(symbol, "name", "") or ""),
                "symbol_id": str(getattr(symbol, "symbol_id", "") or getattr(symbol, "id", "") or ""),
            }
            if symbol is not None and not hit["symbol_id"]:
                hit["symbol_id"] = str(getattr(symbol, "qualified_name", "") or "")
            hits.append(hit)
            if len(hits) >= max(1, limit):
                return hits
    return hits


def format_focus_display(
    *,
    candidate_id: str,
    file_path: str,
    symbol: str,
    start_line: int,
    end_line: int,
    role: str,
    source: str,
    supporting: list[str],
) -> str:
    lines = [
        f"FOCUSED EDIT TARGET {candidate_id or symbol or file_path}",
        "",
        f"File: {file_path}",
        f"Symbol: {symbol}",
        f"Lines: {start_line}-{end_line}",
        f"Role: {role}",
        "",
        source.strip(),
        "",
        "Supporting evidence:",
    ]
    if supporting:
        lines.extend(f"- {item}" for item in supporting)
    else:
        lines.append("- none from verified graph edges")
    lines.extend(
        [
            "",
            "Next recommended action:",
            "Edit this implementation, or call focus_code on another candidate "
            "if the evidence proves this target is wrong.",
        ]
    )
    return "\n".join(lines)
