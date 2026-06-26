# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""VectorNode ↔ memory cross-algorithm decoding (companion to ``io_schema``).

Uses plain ``metadata`` dicts + :class:`~openjiuwen.extensions.context_evolver.core.schema.VectorNode`
to avoid import cycles with ``io_schema``. Spec: ``fallback.md`` in this package.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Type

import json

from openjiuwen.core.common.logging import context_engine_logger as logger

from ..core.schema import VectorNode


def infer_stored_memory_algorithm(node: VectorNode) -> Optional[str]:
    """Infer which algorithm wrote *node* from its id prefix (see io_schema to_vector_node).

    Returns:
        ``ACE``, ``ReasoningBank``, ``ReMe``, ``Cognition``, or ``None`` if unknown.
    """
    nid = (node.id or "").lower()
    if nid.startswith("reasoning_bank_"):
        return "ReasoningBank"
    if nid.startswith("cognition_"):
        return "Cognition"
    if nid.startswith("reme_"):
        return "ReMe"
    if nid.startswith("ace_"):
        return "ACE"
    return None


def deserialization_target_algorithm(memory_cls: Type[Any]) -> str:
    """Algorithm name for the memory model being deserialized (matches TaskMemoryService names)."""
    name = getattr(memory_cls, "__name__", "")
    if name in ("ReMeMemory", "OursMemory"):
        return "ReMe"
    if name == "ACEMemory":
        return "ACE"
    if name == "ReasoningBankMemory":
        return "ReasoningBank"
    if name == "CognitionMemory":
        return "Cognition"
    return name


def use_cross_algorithm_fallback(node: VectorNode, target_algorithm: str) -> bool:
    """Whether to apply cross-algo field mapping (fallback.md).

    Native (same-algorithm) parsing is used when storage matches *target_algorithm*.
    Unknown storage (no recognised id prefix) still uses cross-algo mapping for safety.
    """
    stored = infer_stored_memory_algorithm(node)
    if stored is None:
        return True
    return stored != target_algorithm


def _exp_json_to_text(experience_json: Any) -> str:
    if not experience_json:
        return ""
    try:
        items = json.loads(experience_json) if isinstance(experience_json, str) else experience_json
        if not isinstance(items, list):
            return str(items) if items else ""
        return "\n".join(str(i) for i in items if i)
    except (json.JSONDecodeError, TypeError):
        return ""


def _attrs_domain(attributes_json: Any) -> str:
    if not attributes_json:
        return ""
    try:
        attrs = json.loads(attributes_json) if isinstance(attributes_json, str) else attributes_json
        if not isinstance(attrs, dict):
            return ""
        return attrs.get("domain", "") or ""
    except (json.JSONDecodeError, TypeError):
        return ""


def _parse_experience_list(metadata: Dict[str, Any]) -> List[str]:
    raw = metadata.get("experience_json")
    if raw is None:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        return [str(items)] if items else []
    return [str(i) for i in items if i is not None and str(i).strip()]


def _memory_item_dicts(metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = metadata.get("memory")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw:
        if hasattr(item, "model_dump"):
            try:
                item = item.model_dump()
            except Exception as exc:
                logger.warning("Failed to serialize memory item, skipping: %s", exc)
                continue
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "content": str(item.get("content") or ""),
            }
        )
    return out


def _join_rb_parts(title: str, description: str, content: str) -> str:
    parts = [p.strip() for p in (title, description, content) if p and str(p).strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n".join(parts)


def _looks_like_reme(metadata: Dict[str, Any]) -> bool:
    wt = metadata.get("when_to_use")
    c = metadata.get("content")
    return bool(wt and str(wt).strip() and c is not None and str(c).strip())


def _looks_like_ace(metadata: Dict[str, Any]) -> bool:
    if metadata.get("when_to_use"):
        return False
    return metadata.get("section") is not None and metadata.get("content") is not None


def _cognition_has_shape(metadata: Dict[str, Any]) -> bool:
    if _parse_experience_list(metadata):
        return True
    ic = metadata.get("is_correct")
    has_valid_ic = ic is not None and str(ic) != "none"
    if has_valid_ic and metadata.get("query") and metadata.get("description"):
        return True
    return False


# ---------------------------------------------------------------------------
# ReasoningBank (retrieval)
# ---------------------------------------------------------------------------


def reasoning_bank_item_dicts_from_metadata(
    metadata: Dict[str, Any], node: VectorNode
) -> List[Dict[str, str]]:
    """Build ReasoningBank memory item dicts from VectorNode metadata (fallback.md)."""
    md = metadata

    # Native ReasoningBank shape (summary_algo = reasoningbank): trust memory[] first.
    native = _memory_item_dicts(md)
    if native and any(
        (i.get("title") or "").strip()
        or (i.get("description") or "").strip()
        or (i.get("content") or "").strip()
        for i in native
    ):
        return native

    # ReMe → ReasoningBank
    if _looks_like_reme(md):
        wt = str(md["when_to_use"]).strip()
        body = str(md.get("content", ""))
        return [{"title": wt[:200], "description": "", "content": body}]

    exp_list = _parse_experience_list(md)
    exp_text = _exp_json_to_text(md.get("experience_json"))

    # Cognition → ReasoningBank
    if exp_list or exp_text or _cognition_has_shape(md):
        q = str(md.get("query") or "").strip()
        d = str(md.get("description") or "").strip()
        blob = exp_text or md.get("memory_text") or md.get("content") or node.content or ""
        blob = str(blob).strip()
        if not blob and exp_list:
            blob = "\n".join(exp_list)
        if blob or q or d:
            return [{"title": (q[:200] if q else "Memory"), "description": d, "content": blob or q or d}]

    # ACE → ReasoningBank
    if _looks_like_ace(md):
        ace_content = str(md.get("content") or "").strip()
        if ace_content:
            return [{"title": "", "description": "", "content": ace_content}]

    # Generic chain (legacy / partial nodes)
    content = (
        md.get("content")
        or md.get("memory_text")
        or _exp_json_to_text(md.get("experience_json"))
        or node.content
        or ""
    )
    text = str(content).strip()
    if not text:
        return []

    title = (
        md.get("when_to_use")
        or md.get("section")
        or md.get("description")
        or md.get("query")
        or _attrs_domain(md.get("attributes_json"))
        or "Memory"
    )
    description = str(md.get("description") or "")
    return [{"title": str(title)[:200], "description": description, "content": text}]


def reasoning_bank_query_from_metadata(
    metadata: Dict[str, Any],
    node: VectorNode,
    items: List[Dict[str, str]],
) -> str:
    """Embedding / primary query string for ReasoningBank (per-source mapping)."""
    md = metadata
    if _looks_like_reme(md) and str(md.get("when_to_use") or "").strip():
        return str(md["when_to_use"]).strip()
    if _cognition_has_shape(md) and str(md.get("query") or "").strip():
        return str(md["query"]).strip()
    if _looks_like_ace(md) and str(md.get("content") or "").strip():
        return str(md["content"]).strip()

    q = (
        md.get("query")
        or md.get("when_to_use")
        or md.get("description")
        or (items[0]["title"] if items else "")
        or node.content
        or ""
    )
    return str(q).strip()


# ---------------------------------------------------------------------------
# ACE (retrieval)
# ---------------------------------------------------------------------------


def ace_section_and_content_from_metadata(metadata: Dict[str, Any], node: VectorNode) -> Tuple[str, str]:
    """§ACE: RB / ReMe / Cognition source shapes → section + content."""
    md = metadata
    mem_items = _memory_item_dicts(md)
    if mem_items:
        lines = [_join_rb_parts(i["title"], i["description"], i["content"]) for i in mem_items]
        lines = [x for x in lines if x]
        return "general", "\n\n".join(lines) if lines else ""

    if _looks_like_reme(md):
        wt = str(md.get("when_to_use") or "").strip()
        c = str(md.get("content") or "").strip()
        body = f"{wt}\n{c}".strip() if wt and c else (c or wt)
        return "general", body

    exp_text = _exp_json_to_text(md.get("experience_json"))
    if exp_text.strip():
        sec = _attrs_domain(md.get("attributes_json")) or "general"
        return sec, exp_text

    section = str(md.get("section") or _attrs_domain(md.get("attributes_json")) or "general")
    content = (
        md.get("content")
        or md.get("memory_text")
        or _exp_json_to_text(md.get("experience_json"))
        or node.content
        or ""
    )
    return section, str(content)


# ---------------------------------------------------------------------------
# ReMe (retrieval)
# ---------------------------------------------------------------------------


def reme_when_and_content_from_metadata(metadata: Dict[str, Any], node: VectorNode) -> Tuple[str, str]:
    """§ReMe: ReasoningBank / ACE / Cognition sources → when_to_use + content."""
    md = metadata
    mem_items = _memory_item_dicts(md)
    if mem_items:
        when = str(mem_items[0].get("title") or "").strip()
        bodies: List[str] = []
        for it in mem_items:
            line = _join_rb_parts("", it["description"], it["content"])
            if line:
                bodies.append(line)
        body = "\n\n".join(bodies) if bodies else ""
        return when, body or when

    if _looks_like_ace(md):
        c = str(md.get("content") or node.content or "").strip()
        return c, c

    exp_text = _exp_json_to_text(md.get("experience_json"))
    if exp_text.strip():
        desc = str(md.get("description") or md.get("query") or "").strip()
        return desc or exp_text[:200], exp_text

    wt = str(md.get("when_to_use") or md.get("content") or node.content or "").strip()
    content = str(md.get("content") or "").strip()
    return wt, content if content else wt


# ---------------------------------------------------------------------------
# Cognition (retrieval)
# ---------------------------------------------------------------------------


def cognition_fields_from_metadata(
    metadata: Dict[str, Any], node: VectorNode
) -> Tuple[str, str, List[str], Dict[str, Any]]:
    """§Cognition: RB / ACE / ReMe sources → query, description, experience, attributes."""
    md = metadata
    try:
        attributes = json.loads(md.get("attributes_json", "{}"))
        if not isinstance(attributes, dict):
            attributes = {}
    except (json.JSONDecodeError, TypeError):
        attributes = {}

    try:
        experience = json.loads(md.get("experience_json", "[]"))
        if not isinstance(experience, list):
            experience = []
        experience = [str(i) for i in experience if i is not None and str(i).strip()]
    except (json.JSONDecodeError, TypeError):
        experience = []

    query = str(md.get("query") or "").strip()
    description = str(md.get("description") or "").strip()

    if not experience:
        mem_items = _memory_item_dicts(md)
        if mem_items:
            for it in mem_items:
                line = _join_rb_parts(it["title"], it["description"], it["content"])
                if line:
                    experience.append(line)
            if not query:
                query = str(mem_items[0].get("title") or "").strip()
            if not description:
                description = str(mem_items[0].get("description") or "").strip()
        elif _looks_like_ace(md):
            c = str(md.get("content") or node.content or "").strip()
            if c:
                experience = [c]
            if not query:
                query = c
        elif _looks_like_reme(md):
            wt = str(md.get("when_to_use") or "").strip()
            c = str(md.get("content") or "").strip()
            if c:
                experience = [c]
            if not query:
                query = wt
            if not description:
                description = wt
        else:
            fb = md.get("memory_text") or md.get("content") or node.content
            if fb:
                experience = [str(fb)]

    if not query:
        query = str(md.get("when_to_use") or node.content or "").strip()
    if not description:
        description = query

    return query, description, experience, attributes
