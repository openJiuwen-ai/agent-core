# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attribute a test failure to the patch, or away from it.

run04 produced thirteen failing runs and zero explanations. The agent read
"FAILED" and either edited again or stopped, because nothing answered the only
question that decides which of those is right: can this red line reach the code
I changed?

That question is a graph question. The traceback gives file and line; the graph
turns those into symbols and says whether a call path exists from any of them to
a symbol in a changed file. Three of run04's failures were three different
answers to it -- a doctest in an unrelated module, a test running straight
through the edit, and a path that never touched the new file at all.

Everything here is derived from the traceback text and the index. No model call,
so the same output always yields the same verdict.
"""

from __future__ import annotations

import re
from collections import deque

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphIndex,
    RelationKind,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path

# How far a call path may run from a failing frame to the patch before the link
# stops being an explanation. Two hops covers "the test calls the wrapper which
# calls the edited function"; more than that and every symbol reaches every
# other one.
MAX_REACH_DEPTH = 3
MAX_REACH_NODES = 400

# Frames of the harness itself are noise: every pytest traceback runs through
# them, so a match there says nothing about the patch.
_NOISE_PREFIXES = ("/opt/", "/usr/", "site-packages/", "_pytest/", "pluggy/", "importlib/")

# ``File "path", line 12, in name`` (Python), ``path:12: in name`` (pytest short
# form), and the ``path:12: Error`` shape used by doctest and collection errors.
_FRAME_PATTERNS = (
    re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)'),
    re.compile(r"^(?P<file>[\w./\\-]+\.\w+):(?P<line>\d+): in ", re.MULTILINE),
    re.compile(r"^(?P<file>[\w./\\-]+\.\w+):(?P<line>\d+):\s", re.MULTILINE),
)
# Verdicts. Duplicated as an enum in the harness (``FailureVerdict``) because
# core must not import the harness; the two are kept in step by a test.
PATCH_RELATED = "patch_related"
UNRELATED_EXISTING_FAILURE = "unrelated_existing_failure"
TEST_TARGET_INVALID = "test_target_invalid"
UNDETERMINED = "undetermined"

# ``FAILED path::test_name - msg`` / ``ERROR path::test_name``.
_FAILED_TEST = re.compile(r"^(?:FAILED|ERROR)\s+(?P<target>[^\s]+)", re.MULTILINE)
# Nothing was even collected, so the target was wrong rather than the code.
_BAD_TARGET = re.compile(
    r"ERROR: (?:not found|file or directory not found)|"
    r"no tests ran|collected 0 items|"
    r"ERROR: (?:found no collectors|Wrong expression passed)",
    re.IGNORECASE,
)


def diagnose_failure_path(
    index: CodeGraphIndex,
    output: str,
    changed_files: list[str],
    *,
    max_depth: int = MAX_REACH_DEPTH,
) -> dict[str, object]:
    """Decide whether the failure in ``output`` can reach ``changed_files``."""
    text = str(output or "")
    changed = {_normalize(item) for item in changed_files if item}
    failing_tests = _failing_tests(text)
    frames = _frames(text, index)
    frame_symbols = [symbol for _, symbol in frames if symbol is not None]
    frame_files = {file for file, _ in frames}

    direct = sorted(file for file in frame_files if _matches_changed(file, changed))
    reached = _reachable_changed_symbols(index, frame_symbols, changed, max_depth=max_depth)

    if _BAD_TARGET.search(text) and not frames:
        verdict = TEST_TARGET_INVALID
        detail = "nothing was collected, so the failure is about the test target, not the code"
    elif direct:
        verdict = PATCH_RELATED
        detail = "the failure runs through " + ", ".join(direct[:3])
    elif reached:
        verdict = PATCH_RELATED
        detail = "a call path reaches " + ", ".join(sorted(reached)[:3])
    elif frames:
        verdict = UNRELATED_EXISTING_FAILURE
        detail = (
            "no frame is in a changed file and no call path from the failing frames reaches one; "
            "this failure exists independently of the patch"
        )
    else:
        verdict = UNDETERMINED
        detail = "no source frame could be read out of the output"

    return status_payload(
        CodeGraphStatus.COMPLETE if verdict != UNDETERMINED else CodeGraphStatus.PARTIAL,
        message=f"failure looks {verdict}: {detail}",
        extra={
            "verdict": verdict,
            "detail": detail,
            "failing_tests": failing_tests,
            "frames": [f"{file}:{line}" for file, line, _ in _frame_rows(text, index)],
            "frames_in_changed_files": direct,
            "reached_changed_symbols": sorted(reached),
            "changed_files": sorted(changed),
            "index_snapshot": index.snapshot,
            "index_revision": index.revision,
        },
    )


def _failing_tests(text: str) -> list[str]:
    out: list[str] = []
    for match in _FAILED_TEST.finditer(text):
        target = match.group("target").strip()
        if target and target not in out:
            out.append(target)
    return out[:20]


def _frame_rows(text: str, index: CodeGraphIndex) -> list[tuple[str, int, Symbol | None]]:
    """Every source frame in the output, innermost symbol attached when known."""
    seen: set[tuple[str, int]] = set()
    rows: list[tuple[str, int, Symbol | None]] = []
    for pattern in _FRAME_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group("file")
            if _is_noise(raw):
                continue
            file = _repo_relative(raw, index)
            try:
                line = int(match.group("line"))
            except ValueError:
                continue
            key = (file, line)
            if key in seen:
                continue
            seen.add(key)
            rows.append((file, line, symbol_at(index, file, line)))
    return rows[:60]


def _frames(text: str, index: CodeGraphIndex) -> list[tuple[str, Symbol | None]]:
    return [(file, symbol) for file, _, symbol in _frame_rows(text, index)]


def symbol_at(index: CodeGraphIndex, file: str, line: int) -> Symbol | None:
    """Innermost symbol of ``file`` covering ``line``."""
    best: Symbol | None = None
    for symbol_id in index.by_file.get(file) or _by_suffix(index, file):
        symbol = index.symbols.get(symbol_id)
        if symbol is None or symbol.start_line > line or symbol.end_line < line:
            continue
        if best is None or (symbol.end_line - symbol.start_line) < (best.end_line - best.start_line):
            best = symbol
    return best


def _by_suffix(index: CodeGraphIndex, file: str) -> list[str]:
    """Symbols of a file the traceback named under a different root.

    The tests run in a container where the checkout is ``/testbed/...`` while the
    index knows it as a repo-relative path, so an exact key miss is normal rather
    than a sign the file is unknown.
    """
    text = _normalize(file)
    if not text or "/" not in text:
        return []
    for known, symbol_ids in index.by_file.items():
        if text.endswith("/" + known) or known.endswith("/" + text):
            return list(symbol_ids)
    return []


def _reachable_changed_symbols(
    index: CodeGraphIndex,
    frames: list[Symbol],
    changed: set[str],
    *,
    max_depth: int,
) -> set[str]:
    """Symbols in a changed file that a failing frame can call into."""
    if not frames or not changed:
        return set()
    found: set[str] = set()
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque((symbol.symbol_id, 0) for symbol in frames)
    while queue and len(seen) < MAX_REACH_NODES:
        symbol_id, depth = queue.popleft()
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        symbol = index.symbols.get(symbol_id)
        if symbol is not None and _matches_changed(symbol.file, changed) and depth:
            found.add(symbol_id)
        if depth >= max_depth:
            continue
        for relation in (RelationKind.CALLS, RelationKind.CONTAINS, RelationKind.INHERITS):
            for neighbor in index.neighbors(symbol_id, relation):
                queue.append((neighbor, depth + 1))
    return found


def _matches_changed(file: str, changed: set[str]) -> bool:
    normalized = _normalize(file)
    if not normalized:
        return False
    return any(normalized.endswith(item) or item.endswith(normalized) for item in changed)


def _is_noise(raw: str) -> bool:
    text = _normalize(raw)
    if is_test_path(text):
        # Test frames are the *entry* point of the failure and are worth keeping:
        # a test in a changed file is the strongest possible link to the patch.
        return False
    return any(part in text for part in _NOISE_PREFIXES)


def _repo_relative(raw: str, index: CodeGraphIndex) -> str:
    text = _normalize(raw)
    root = _normalize(index.repo_root).rstrip("/")
    if root and text.startswith(root + "/"):
        return text[len(root) + 1 :]
    # Containers run the same checkout under a different root (``/testbed``), so
    # a leading absolute path is matched by suffix instead of rejected.
    return text.lstrip("/") if text.startswith("/") else text


def _normalize(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


__all__ = ["MAX_REACH_DEPTH", "diagnose_failure_path", "symbol_at"]
