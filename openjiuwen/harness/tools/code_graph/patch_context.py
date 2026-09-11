# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic ``<PATCH_CONTEXT>`` formatting for locate runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from openjiuwen.harness.schema.code_graph import CodeGraphLocation

# Class spans longer than this must be replaced by method-level evidence.
MAX_CLASS_SUBMIT_LINES = 80


def format_patch_context(locations: Sequence[CodeGraphLocation]) -> str:
    """Always-well-formed PATCH_CONTEXT. Empty input yields an empty string."""
    merged = merge_locations(locations)
    if not merged:
        return ""
    lines = ["<PATCH_CONTEXT>"]
    for location in merged:
        rel = _rel(location.file)
        start = max(1, int(location.start_line or 1))
        end = max(start, int(location.end_line or start))
        lines.append(f"File: {rel}")
        lines.append(f"Lines: {start}-{end}")
    lines.append("</PATCH_CONTEXT>")
    return "\n".join(lines)


def merge_locations(locations: Sequence[CodeGraphLocation]) -> list[CodeGraphLocation]:
    """Merge overlapping / adjacent spans in the same file, keep file order."""
    by_file: dict[str, list[CodeGraphLocation]] = defaultdict(list)
    order: list[str] = []
    for location in locations:
        rel = _rel(location.file)
        if not rel:
            continue
        if rel not in by_file:
            order.append(rel)
        by_file[rel].append(location)
    merged: list[CodeGraphLocation] = []
    for rel in order:
        items = sorted(
            by_file[rel],
            key=lambda item: (int(item.start_line or 0), int(item.end_line or 0)),
        )
        current = _with_file(items[0], rel)
        for item in items[1:]:
            candidate = _with_file(item, rel)
            if int(candidate.start_line) <= int(current.end_line) + 1:
                current = CodeGraphLocation(
                    symbol_id=current.symbol_id or candidate.symbol_id,
                    file=rel,
                    start_line=min(int(current.start_line), int(candidate.start_line)),
                    end_line=max(int(current.end_line), int(candidate.end_line)),
                    reason=current.reason or candidate.reason,
                    confidence=max(float(current.confidence), float(candidate.confidence)),
                    name=current.name or candidate.name,
                    kind=current.kind or candidate.kind,
                    evidence_id=current.evidence_id or candidate.evidence_id,
                )
            else:
                merged.append(current)
                current = candidate
        merged.append(current)
    return merged


def shrink_to_symbol(
    location: CodeGraphLocation,
    evidence: dict[str, Any] | None,
) -> CodeGraphLocation:
    """Prefer the symbol's definition span over a wider read window."""
    if not evidence:
        return location
    start = int(evidence.get("symbol_start_line") or evidence.get("start_line") or 0)
    end = int(evidence.get("symbol_end_line") or evidence.get("end_line") or 0)
    if start < 1 or end < start:
        return location
    return CodeGraphLocation(
        symbol_id=str(evidence.get("symbol_id") or location.symbol_id),
        file=_rel(str(evidence.get("file") or location.file)),
        start_line=start,
        end_line=end,
        reason=location.reason,
        confidence=location.confidence,
        name=str(evidence.get("name") or location.name),
        kind=str(evidence.get("kind") or location.kind),
        evidence_id=location.evidence_id or str(evidence.get("evidence_id") or ""),
    )


def normalize_submit_locations(
    locations: Sequence[CodeGraphLocation],
    *,
    read_evidence: dict[str, dict[str, Any]] | None = None,
    candidates: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[CodeGraphLocation], list[str]]:
    """Replace oversized class spans with method-level evidence when possible.

    Returns ``(locations, blockers)``. Non-empty blockers mean submit must stop
    and ask the model to read members instead of the whole class.
    """
    evidence = read_evidence or {}
    cand = candidates or {}
    method_reads = _method_locations_from_evidence(evidence)
    out: list[CodeGraphLocation] = []
    blockers: list[str] = []
    seen: set[tuple[str, int, int]] = set()

    for location in locations:
        shrunk = shrink_to_symbol(
            location,
            cand.get(location.symbol_id)
            or evidence.get(location.evidence_id)
            or _evidence_for_symbol(evidence, location.symbol_id),
        )
        span = max(0, int(shrunk.end_line) - int(shrunk.start_line) + 1)
        kind = str(shrunk.kind or "").lower()
        if kind in {"class", "module", "interface"} and span > MAX_CLASS_SUBMIT_LINES:
            replacements = _methods_inside(shrunk, method_reads) or _methods_inside(
                shrunk, _method_locations_from_candidates(cand)
            )
            if replacements:
                for item in replacements:
                    key = (_rel(item.file), int(item.start_line), int(item.end_line))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(item)
                continue
            blockers.append(
                f"refuse to submit {shrunk.name or shrunk.symbol_id} "
                f"({span} lines). Call inspect_code_structure on it, then "
                "read_symbol on the methods that change, and submit those."
            )
            continue
        key = (_rel(shrunk.file), int(shrunk.start_line), int(shrunk.end_line))
        if key in seen:
            continue
        seen.add(key)
        out.append(shrunk)
    return out, blockers


def location_from_evidence(
    evidence: dict[str, Any],
    *,
    reason: str = "read evidence",
) -> CodeGraphLocation | None:
    file_path = _rel(str(evidence.get("file") or ""))
    start = int(evidence.get("symbol_start_line") or evidence.get("start_line") or 0)
    end = int(evidence.get("symbol_end_line") or evidence.get("end_line") or start)
    symbol_id = str(evidence.get("symbol_id") or "")
    kind = str(evidence.get("kind") or "")
    if not file_path or start < 1:
        return None
    if evidence.get("large_class") or evidence.get("submit") is None:
        if kind.lower() in {"class", "module", "interface"}:
            span = max(0, end - start + 1)
            if span > MAX_CLASS_SUBMIT_LINES:
                return None
    return CodeGraphLocation(
        symbol_id=symbol_id,
        file=file_path,
        start_line=start,
        end_line=max(start, end),
        reason=reason,
        confidence=0.6,
        name=str(evidence.get("name") or ""),
        kind=kind,
        evidence_id=str(evidence.get("evidence_id") or ""),
    )


def _method_locations_from_evidence(
    evidence: dict[str, dict[str, Any]],
) -> list[CodeGraphLocation]:
    out: list[CodeGraphLocation] = []
    for payload in evidence.values():
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("kind") or "").lower()
        if kind not in {"method", "function"}:
            continue
        if payload.get("large_class"):
            continue
        loc = location_from_evidence(payload, reason="method read evidence")
        if loc is not None:
            out.append(loc)
    return out


def _method_locations_from_candidates(
    candidates: dict[str, dict[str, Any]],
) -> list[CodeGraphLocation]:
    out: list[CodeGraphLocation] = []
    for item in candidates.values():
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        if kind not in {"method", "function"}:
            continue
        start = int(item.get("start_line") or 0)
        end = int(item.get("end_line") or start)
        file_path = _rel(str(item.get("file") or ""))
        if not file_path or start < 1:
            continue
        out.append(
            CodeGraphLocation(
                symbol_id=str(item.get("symbol_id") or ""),
                file=file_path,
                start_line=start,
                end_line=max(start, end),
                reason="method from structure listing",
                confidence=0.5,
                name=str(item.get("name") or ""),
                kind=kind,
            )
        )
    return out


def _methods_inside(
    class_loc: CodeGraphLocation,
    methods: Sequence[CodeGraphLocation],
) -> list[CodeGraphLocation]:
    rel = _rel(class_loc.file)
    start = int(class_loc.start_line)
    end = int(class_loc.end_line)
    class_id = class_loc.symbol_id or ""
    class_name = class_loc.name or ""
    hits: list[CodeGraphLocation] = []
    for method in methods:
        if _rel(method.file) != rel:
            continue
        mid = method.symbol_id or ""
        inside = start <= int(method.start_line) and int(method.end_line) <= end
        child = bool(class_id and mid.startswith(class_id + "."))
        named = bool(class_name and f".{class_name}." in f".{mid}.")
        if inside or child or named:
            hits.append(method)
    return hits


def _evidence_for_symbol(
    evidence: dict[str, dict[str, Any]],
    symbol_id: str,
) -> dict[str, Any] | None:
    if not symbol_id:
        return None
    for payload in evidence.values():
        if isinstance(payload, dict) and str(payload.get("symbol_id") or "") == symbol_id:
            return payload
    return None


def _rel(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")


def _with_file(location: CodeGraphLocation, rel: str) -> CodeGraphLocation:
    if _rel(location.file) == rel:
        return location
    return CodeGraphLocation(
        symbol_id=location.symbol_id,
        file=rel,
        start_line=location.start_line,
        end_line=location.end_line,
        reason=location.reason,
        confidence=location.confidence,
        name=location.name,
        kind=location.kind,
        evidence_id=location.evidence_id,
    )
