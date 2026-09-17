# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Algorithm-agnostic contracts for the context-evolve dimension.

The common layer defines envelopes only, never algorithm content: payloads
stay opaque, algorithm-private state travels through ``evolution_context``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

# Dimension-level bind(**config) key carrying {scope_id: state} snapshots.
SCOPE_STATES_CONFIG_KEY = "scope_states"


@dataclass(frozen=True)
class ContextRetrievalResult:
    """Read-side result: rendered injection text plus private carry-over."""

    content: str
    evolution_context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextEvolveRecord:
    """Commit envelope built by the rail at the store boundary."""

    scope_id: str
    algorithm: str
    payload: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    entry_type: Optional[str] = None


@runtime_checkable
class ContextRetriever(Protocol):
    """Read side: retrieve context to inject for one scope and query."""

    async def retrieve(self, scope_id: str, query: str) -> ContextRetrievalResult:
        """Retrieve rendered context and private evolution metadata."""
        raise NotImplementedError


@runtime_checkable
class ContextStore(Protocol):
    """State boundary: load per-scope state and commit evolved records."""

    async def load_state(self, scope_id: str) -> Any:
        """Load a working state snapshot for one evolution scope."""
        raise NotImplementedError

    async def commit(self, record: ContextEvolveRecord) -> Any:
        """Commit one validated evolution record to authoritative storage."""
        raise NotImplementedError


__all__ = [
    "SCOPE_STATES_CONFIG_KEY",
    "ContextEvolveRecord",
    "ContextRetrievalResult",
    "ContextRetriever",
    "ContextStore",
]
