# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Closed-set TTSE business-scenario categories.

Mirrors skill retrieval's L1 ``tree_root_categories`` (id / name /
description / select_when / dont_select_when) plus ``other``. Children are
intentionally omitted — the bank is small and ``ttse_consult`` only needs one
layer. agent-core does not import jiuwenclaw.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

OTHER_CATEGORY = "other"

# Skill L1 roots. Keep ids byte-identical to taxonomy_config.tree_root_categories.
DEFAULT_CATEGORIES: Tuple[Dict[str, str], ...] = (
    {
        "id": "external-service-automation",
        "name": "External service automation and integrations",
        "description": "Operate a named remote app, SaaS product, API, connector, account or cloud service.",
        "select_when": (
            "Route here only when the primary action is to read or change state "
            "in an external service or connected account."
        ),
        "dont_select_when": (
            "Do not route local files, source code, datasets, media, science models, "
            "security investigations or simulated environments here."
        ),
    },
    {
        "id": "documents-office-and-records",
        "name": "Documents, office files and records",
        "description": "Create, read, convert, extract from or edit local document and office-file artifacts.",
        "select_when": (
            "Route here when the primary object is a PDF, Word, PPT, spreadsheet, "
            "CSV, form, contract, report or record file."
        ),
        "dont_select_when": (
            "Do not route remote service operations, source-code work, raw media tasks, "
            "dataset analytics or scientific modeling here."
        ),
    },
    {
        "id": "data-analytics-and-visualization",
        "name": "Data analytics, BI and visualization",
        "description": (
            "Analyze structured data to compute metrics, patterns, features, models, "
            "charts or business conclusions."
        ),
        "select_when": (
            "Route here when the primary object is a dataset or table and the goal is "
            "analysis, cleaning, modeling or visualization."
        ),
        "dont_select_when": (
            "Do not route office-file formatting, remote service operation, source-code "
            "implementation, security forensics or physical-science modeling here."
        ),
    },
    {
        "id": "software-engineering-devops",
        "name": "Software engineering and DevOps",
        "description": (
            "Modify, test, build, migrate, review or operate software projects and "
            "developer infrastructure."
        ),
        "select_when": (
            "Route here when the primary object is source code, a repository, build "
            "system, runtime environment, CI job or developer workflow."
        ),
        "dont_select_when": (
            "Do not route remote business app operations, document processing, media "
            "tasks, scientific domain work or security investigations here unless "
            "code work is primary."
        ),
    },
    {
        "id": "security-privacy-and-risk",
        "name": "Security, privacy and risk analysis",
        "description": (
            "Investigate or reduce security, privacy, vulnerability, network, identity "
            "or operational risk."
        ),
        "select_when": (
            "Route here when the primary goal is security assessment, threat detection, "
            "vulnerability handling, forensics, hardening or risk reporting."
        ),
        "dont_select_when": (
            "Do not route ordinary software testing, generic API automation, normal "
            "document redaction, business analytics or scientific signal analysis here."
        ),
    },
    {
        "id": "media-multimodal-and-creative",
        "name": "Media, multimodal and creative processing",
        "description": (
            "Transform or understand audio, video, images, graphics, 3D assets or "
            "multimodal creative content."
        ),
        "select_when": (
            "Route here when the primary object is media content rather than a document "
            "file, dataset, remote service or codebase."
        ),
        "dont_select_when": (
            "Do not route office-document OCR, structured data analysis, source-code "
            "work, remote SaaS operation or scientific modeling here."
        ),
    },
    {
        "id": "science-engineering-modeling",
        "name": "Science, engineering and mathematical modeling",
        "description": (
            "Solve domain equations, simulations or analyses in science, engineering, "
            "math or physical systems."
        ),
        "select_when": (
            "Route here when scientific or engineering domain semantics are primary, "
            "even if code or data processing is involved."
        ),
        "dont_select_when": (
            "Do not route generic software work, business analytics, remote API "
            "operations, document processing, media editing or security forensics here."
        ),
    },
    {
        "id": "search-research-and-knowledge",
        "name": "Search, research and knowledge work",
        "description": (
            "Find, retrieve, verify, cite or organize information from sources or "
            "reference datasets."
        ),
        "select_when": (
            "Route here when the primary goal is evidence gathering, lookup, literature "
            "work, knowledge retrieval or information organization."
        ),
        "dont_select_when": (
            "Do not route execution in a remote account, local file editing, source-code "
            "work, media processing, data modeling or security investigation here."
        ),
    },
    {
        "id": "embodied-simulation-and-interactive-tasks",
        "name": "Embodied, simulated and interactive tasks",
        "description": (
            "Act inside simulated, embodied, game-like or interactive environments "
            "with state and actions."
        ),
        "select_when": (
            "Route here when the primary task is navigation, object manipulation, "
            "environment state change, game-state reasoning or action execution."
        ),
        "dont_select_when": (
            "Do not route real SaaS APIs, local files, source-code work, media "
            "processing, data analysis or physical-system modeling here."
        ),
    },
    {
        "id": "skill-agent-meta-workflows",
        "name": "Agent, skill and workflow meta tasks",
        "description": (
            "Work on skills, agents, prompts, memory, retrieval, benchmarks, plans "
            "or orchestration itself."
        ),
        "select_when": (
            "Route here only when the task is about building, testing, evaluating or "
            "coordinating the agent/skill system."
        ),
        "dont_select_when": (
            "Do not route normal domain tasks here just because they may use a skill "
            "or agent internally."
        ),
    },
    {
        "id": OTHER_CATEGORY,
        "name": "Other",
        "description": "Rules that do not fit any of the listed business scenarios.",
        "select_when": "Route here when no other category is a clear primary fit.",
        "dont_select_when": "Do not use this when a listed scenario clearly applies.",
    },
)


def category_ids(categories: Optional[Iterable[Dict[str, Any]]] = None) -> Tuple[str, ...]:
    """Return closed-set ids, always including ``other`` last."""
    rows = list(categories) if categories is not None else list(DEFAULT_CATEGORIES)
    ids: List[str] = []
    seen = set()
    for row in rows:
        cid = str(row.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if cid != OTHER_CATEGORY:
            ids.append(cid)
    ids.append(OTHER_CATEGORY)
    return tuple(ids)


def category_by_id(categories: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Dict[str, str]]:
    """Map id -> category dict for the closed set."""
    rows = list(categories) if categories is not None else list(DEFAULT_CATEGORIES)
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        cid = str(row.get("id") or "").strip()
        if cid:
            out[cid] = {
                "id": cid,
                "name": str(row.get("name") or cid),
                "description": str(row.get("description") or ""),
                "select_when": str(row.get("select_when") or ""),
                "dont_select_when": str(row.get("dont_select_when") or ""),
            }
    if OTHER_CATEGORY not in out:
        out[OTHER_CATEGORY] = {
            "id": OTHER_CATEGORY,
            "name": "Other",
            "description": "Rules that do not fit any of the listed business scenarios.",
            "select_when": "Route here when no other category is a clear primary fit.",
            "dont_select_when": "Do not use this when a listed scenario clearly applies.",
        }
    return out


def normalize_category(value: Any, *, valid: Optional[Iterable[str]] = None) -> str:
    """Map an arbitrary id to the closed set; unknowns become ``other``."""
    allowed = set(valid) if valid is not None else set(category_ids())
    allowed.add(OTHER_CATEGORY)
    cid = str(value or "").strip()
    if cid in allowed:
        return cid
    return OTHER_CATEGORY


def format_categories_for_prompt(categories: Optional[Iterable[Dict[str, Any]]] = None) -> str:
    """Render the closed set for the assignment LLM (skill-assignment shape)."""
    lines: List[str] = []
    by_id = category_by_id(categories)
    for cid in category_ids(categories):
        payload = by_id[cid]
        lines.append(f"- {cid}: {payload.get('name', cid)}")
        description = str(payload.get("description") or "").strip()
        select_when = str(payload.get("select_when") or "").strip()
        dont_select_when = str(payload.get("dont_select_when") or "").strip()
        if description:
            lines.append(f"  Description: {description}")
        if select_when:
            lines.append(f"  Select when: {select_when}")
        if dont_select_when:
            lines.append(f"  Don't select when: {dont_select_when}")
    return "\n".join(lines)


__all__ = [
    "OTHER_CATEGORY",
    "DEFAULT_CATEGORIES",
    "category_ids",
    "category_by_id",
    "normalize_category",
    "format_categories_for_prompt",
]
