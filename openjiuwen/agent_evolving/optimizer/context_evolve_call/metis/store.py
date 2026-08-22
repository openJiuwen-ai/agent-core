# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis memory store: the state boundary for the task-memory library.

Holds tips / tools / recent-queries per user and persists per-user JSON
snapshots under ``./memories/metis`` by default. The operator stays
preview-only; all real state mutation goes through
:meth:`MetisMemoryStore.commit`. Retrieval exposes the complete live library
to the Manager, which is the sole relevance selector.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openjiuwen.agent_evolving.optimizer.context_evolve_call.contracts import ContextEvolveRecord
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.orchestrator import EvolveState
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.schema import (
    BaseTip,
    CodeTool,
    tip_from_dict,
    tip_to_dict,
    tool_from_dict,
    tool_to_dict,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

_USER_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SNAPSHOT_VERSION = 2

# Single source for the algorithm identity carried by records and the optimizer.
METIS_ALGORITHM_ID = "metis"


@dataclass
class MetisMemoryDelta:
    """One evolve pass's output: the evolved library plus this round's additions.

    Tip curation is invalidate-and-append, so ``state`` is authoritative;
    ``new_tip_ids`` / ``new_tool_ids`` only report what this round added.
    """

    user_id: str
    task_id: str
    state: EvolveState
    new_tip_ids: List[str] = field(default_factory=list)
    new_tool_ids: List[str] = field(default_factory=list)


@dataclass
class _UserMemory:
    tips: List[BaseTip] = field(default_factory=list)
    tools: List[CodeTool] = field(default_factory=list)
    recent_queries: List[str] = field(default_factory=list)


class MetisMemoryStore:
    """Per-user Metis task-memory library with optional JSON persistence.

    By default snapshots are stored as ``./memories/metis/<user_id>.json``.
    Pass a different ``persist_dir`` to relocate them, or ``None`` to keep the
    store process-local only.
    """

    def __init__(self, *, persist_dir: Optional[str] = "./memories/metis") -> None:
        """Create a store with optional per-user JSON persistence.

        Args:
            persist_dir: Snapshot directory. Use ``None`` for process-local
                in-memory storage.

        """
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._users: Dict[str, _UserMemory] = {}
        self._lock = asyncio.Lock()

    # ---- state access -------------------------------------------------

    async def load_state(self, user_id: str) -> EvolveState:
        """Deep-copied working state for one evolve pass (safe to mutate)."""
        mem = await self._ensure_loaded(user_id)
        return EvolveState(
            tips=copy.deepcopy(mem.tips),
            tools=copy.deepcopy(mem.tools),
            recent_queries=list(mem.recent_queries),
        )

    async def load_candidates(self, user_id: str) -> Tuple[List[BaseTip], List[CodeTool]]:
        """Return the complete live tip/tool library for Manager selection."""
        mem = await self._ensure_loaded(user_id)
        live_tips = [t for t in mem.tips if not t.is_invalidated]
        return copy.deepcopy(live_tips), copy.deepcopy(mem.tools)

    # ---- commit -------------------------------------------------------

    async def commit(self, record: ContextEvolveRecord) -> None:
        """Apply one evolve pass's record and persist the resulting library.

        Implements the dimension's ``ContextStore`` protocol: the opaque
        ``record.payload`` must be a :class:`MetisMemoryDelta`.
        """
        if record.algorithm != METIS_ALGORITHM_ID:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"MetisMemoryStore.commit rejects records from algorithm {record.algorithm!r}",
            )
        delta = record.payload
        if not isinstance(delta, MetisMemoryDelta):
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"MetisMemoryStore.commit expects a MetisMemoryDelta payload, got {type(delta).__name__}",
            )
        if record.scope_id != delta.user_id:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"record scope_id {record.scope_id!r} does not match delta user_id {delta.user_id!r}",
            )
        async with self._lock:
            mem = await self._ensure_loaded(delta.user_id)
            mem.tips = copy.deepcopy(delta.state.tips)
            mem.tools = copy.deepcopy(delta.state.tools)
            mem.recent_queries = list(delta.state.recent_queries)

            await self._persist(delta.user_id, mem)
            logger.info(
                "[MetisMemoryStore] commit user=%s: %d tips, %d tools (+%d new)",
                delta.user_id,
                len(mem.tips),
                len(mem.tools),
                len(delta.new_tip_ids) + len(delta.new_tool_ids),
            )

    # ---- persistence --------------------------------------------------

    def _snapshot_path(self, user_id: str) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        safe = _USER_ID_SAFE_RE.sub("_", user_id) or "default"
        return self._persist_dir / f"{safe}.json"

    async def _ensure_loaded(self, user_id: str) -> _UserMemory:
        mem = self._users.get(user_id)
        if mem is not None:
            return mem
        mem = _UserMemory()
        path = self._snapshot_path(user_id)
        if path is not None and path.exists():
            try:
                data = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
                mem.tips = [tip_from_dict(d) for d in data.get("tips") or []]
                mem.tools = [tool_from_dict(d) for d in data.get("tools") or []]
                mem.recent_queries = [str(q) for q in data.get("recent_queries") or []]
            except (OSError, ValueError) as exc:
                logger.warning("[MetisMemoryStore] failed to load snapshot for user=%s: %s", user_id, exc)
        self._users[user_id] = mem
        return mem

    async def _persist(self, user_id: str, mem: _UserMemory) -> None:
        path = self._snapshot_path(user_id)
        if path is None:
            return
        data = {
            "version": _SNAPSHOT_VERSION,
            "tips": [tip_to_dict(t) for t in mem.tips],
            "tools": [tool_to_dict(t) for t in mem.tools],
            "recent_queries": list(mem.recent_queries),
        }

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        await asyncio.to_thread(_write)


__all__ = [
    "MetisMemoryDelta",
    "MetisMemoryStore",
]
