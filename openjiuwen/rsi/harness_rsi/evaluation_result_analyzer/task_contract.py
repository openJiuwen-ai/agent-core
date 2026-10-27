# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic task clauses and exact legacy citation compatibility."""

from __future__ import annotations

import hashlib
from typing import Any


def build_task_clauses(task: str) -> list[dict[str, Any]]:
    """Index non-empty paragraphs, preserving their original text and line range."""
    lines = task.splitlines(keepends=True)
    clauses: list[dict[str, Any]] = []
    start = 0
    for index in range(len(lines) + 1):
        if index < len(lines) and lines[index].strip():
            continue
        if start < index:
            text = "".join(lines[start:index]).rstrip("\r\n")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
            clauses.append({
                "clause_id": f"clause_{start + 1:04d}_{digest}",
                "line_start": start + 1,
                "line_end": index,
                "text": text,
            })
        start = index + 1
    return clauses


def normalize_contract_checks(diagnosis: dict[str, Any], task: str) -> list[str]:
    """Resolve IDs, refill quotes, and distinguish citation/schema from conflict."""
    checks = diagnosis.get("task_contract_checks")
    if checks is None and "task_contract_checks" not in diagnosis:
        return []
    if not isinstance(checks, list):
        return ["[contract_schema] task_contract_checks must be a list"]
    clauses = build_task_clauses(task)
    by_id = {clause["clause_id"]: clause for clause in clauses}
    errors = []
    for index, check in enumerate(checks):
        field = f"task_contract_checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"[contract_schema] {field} must be an object")
            continue
        clause_id = check.get("clause_id")
        clause = by_id.get(clause_id) if isinstance(clause_id, str) else None
        if clause_id is None:
            quote = check.get("task_quote")
            if isinstance(quote, str) and quote.strip() and task.count(quote) == 1:
                matches = [item for item in clauses if quote.rstrip("\r\n") in item["text"]]
                clause = matches[0] if len(matches) == 1 else None
        if clause is None:
            available = ", ".join(list(by_id)[:30])
            errors.append(
                f"[contract_citation] {field}.clause_id must select a supplied task clause; "
                f"legacy task_quote requires one exact unique match. Available IDs (first 30): {available}"
            )
        else:
            check["clause_id"] = clause["clause_id"]
            check["task_quote"] = clause["text"]
            check["line_start"] = clause["line_start"]
            check["line_end"] = clause["line_end"]
        if not isinstance(check.get("interpretation"), str) or not check["interpretation"].strip():
            errors.append(f"[contract_schema] {field}.interpretation must state the relevant guarantee")
        preserves = check.get("recommendation_preserves_contract")
        if type(preserves) is not bool:
            errors.append(f"[contract_schema] {field}.recommendation_preserves_contract is required (boolean)")
        elif preserves is False:
            errors.append(f"[contract_conflict] {field}.recommendation_preserves_contract must preserve the original contract")
    return errors
