# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_block.schema import (
    REGISTRY_STATE_KEY,
    QABlockEntry,
    QABlockRegistry,
)

_REGISTRY_CACHE_ATTR = "_qa_block_registry_invoke_cache"


def _session_id_from(session: Any) -> str:
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        return str(getter() or "")
    sid = getattr(session, "session_id", None)
    if callable(sid):
        return str(sid() or "")
    return str(sid or "")


def _set_registry_cache(session: Any, registry: QABlockRegistry) -> None:
    setattr(session, _REGISTRY_CACHE_ATTR, registry)


def load_registry(session: Any, *, force_reload: bool = False) -> QABlockRegistry:
    """Load QA block registry from session state, or create an empty one."""
    session_id = _session_id_from(session)
    if not force_reload:
        cached = getattr(session, _REGISTRY_CACHE_ATTR, None)
        if isinstance(cached, QABlockRegistry) and cached.session_id == session_id:
            return cached

    raw = None
    if hasattr(session, "get_state"):
        raw = session.get_state(REGISTRY_STATE_KEY)

    if not raw:
        logger.info(
            "[QABlockRegistry] init empty registry session_id=%s",
            session_id,
        )
        registry = QABlockRegistry(session_id=session_id)
        _set_registry_cache(session, registry)
        return registry

    registry = QABlockRegistry.model_validate(raw)
    if registry.session_id != session_id:
        registry = registry.model_copy(update={"session_id": session_id})
    logger.info(
        "[QABlockRegistry] loaded session_id=%s blocks=%s current_qa_id=%s next_index=%s",
        session_id,
        len(registry.blocks),
        registry.current_qa_id,
        registry.next_qa_index,
    )
    _set_registry_cache(session, registry)
    return registry


def save_registry(session: Any, registry: QABlockRegistry) -> None:
    """Persist registry into session state (checkpoint-eligible)."""
    if not hasattr(session, "update_state"):
        logger.warning("[QABlockRegistry] save skipped: session has no update_state")
        return
    session.update_state({REGISTRY_STATE_KEY: registry.model_dump(mode="json")})
    _set_registry_cache(session, registry)
    logger.info(
        "[QABlockRegistry] saved session_id=%s blocks=%s current_qa_id=%s next_index=%s",
        registry.session_id,
        len(registry.blocks),
        registry.current_qa_id,
        registry.next_qa_index,
    )


def merge_registry_block(session: Any, entry: QABlockEntry) -> QABlockRegistry:
    """Reload registry, merge a single QA block entry, then save.

    Used by async ``freeze_persist`` so a slow L1 LLM pass cannot overwrite
    blocks committed by a subsequent QA while this persist was in flight.
    """
    if not isinstance(entry, QABlockEntry):
        entry = QABlockEntry.model_validate(entry)

    registry = load_registry(session, force_reload=True)
    prior_blocks = len(registry.blocks)
    registry.blocks[entry.qa_id] = entry
    save_registry(session, registry)
    if len(registry.blocks) < prior_blocks:
        logger.warning(
            "[QABlockRegistry] merge block count decreased session_id=%s qa_id=%s before=%s after=%s",
            registry.session_id,
            entry.qa_id,
            prior_blocks,
            len(registry.blocks),
        )
    return registry
