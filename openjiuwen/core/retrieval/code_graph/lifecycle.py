# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared graph entry, immutable generations, and query leases."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity, workspace_relative_path
from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.workspace_token import WorkspaceToken

if TYPE_CHECKING:
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager
    from openjiuwen.core.retrieval.code_graph.service import CodeGraphService


def estimate_index_bytes(index: CodeGraphIndex) -> int:
    """Rough resident size used by weighted LRU, not a precise RSS sample."""
    return (
        len(index.symbols) * 512
        + len(index.relations) * 128
        + len(index.extracted) * 256
        + len(index.file_hashes) * 64
        + max(0, index.file_count) * 32
    )


@dataclass
class GraphGeneration:
    """One immutable published index for a workspace."""

    generation_id: int
    token: WorkspaceToken
    index: CodeGraphIndex
    created_at: float
    estimated_bytes: int
    reader_count: int = 0
    reason: str = "build"


@dataclass
class GraphLease:
    """Query-scoped pin of one generation. Conversation close must release this."""

    generation: GraphGeneration
    _released: bool = False

    @property
    def index(self) -> CodeGraphIndex:
        return self.generation.index

    def release(self) -> None:
        if self._released:
            return
        self.generation.reader_count = max(0, self.generation.reader_count - 1)
        self._released = True

    def __enter__(self) -> "GraphLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


@dataclass
class GraphEntry:
    """Process-local live graph for one workspace path."""

    identity: RepoIdentity
    config_hash: str
    active: GraphGeneration | None = None
    retired: list[GraphGeneration] = field(default_factory=list)
    dirty_paths: set[str] = field(default_factory=set)
    dirty_unknown: bool = False
    change_epoch: int = 0
    update_task: asyncio.Task[GraphGeneration] | None = None
    update_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access_at: float = field(default_factory=time.time)
    last_freshness_at: float = 0.0
    last_token: WorkspaceToken | None = None
    last_full_build_seconds: float | None = None
    checkpoint_dirty: bool = False
    next_generation_id: int = 1
    service: CodeGraphService | None = None
    manager: CodeGraphManager | None = None
    file_hashes: dict[str, str] = field(default_factory=dict)
    cancel_event: object | None = None
    limit_error: object | None = None
    limit_exceeded_digest: str | None = None

    def acquire_lease(self) -> GraphLease:
        if self.active is None:
            raise RuntimeError("graph entry has no active generation")
        self.active.reader_count += 1
        self.last_access_at = time.time()
        return GraphLease(self.active)

    def mark_dirty(self, paths: list[str]) -> None:
        for raw in paths:
            rel = workspace_relative_path(self.identity.canonical_root, raw)
            if rel:
                self.dirty_paths.add(rel)
        self.normalize_dirty_paths()
        self.change_epoch += 1
        self.last_access_at = time.time()

    def normalize_dirty_paths(self) -> None:
        """Rewrite leftover absolute / realpath dirty entries to repo-relative."""
        normalized: set[str] = set()
        for raw in self.dirty_paths:
            rel = workspace_relative_path(self.identity.canonical_root, raw)
            if rel:
                normalized.add(rel)
        self.dirty_paths = normalized

    def mark_dirty_unknown(self) -> None:
        # First build has no published generation yet. A shell command during
        # BUILDING must not poison the unpublished walk.
        if self.active is None:
            return
        self.dirty_unknown = True
        self.change_epoch += 1
        self.last_access_at = time.time()

    def clear_dirty(self) -> None:
        self.dirty_paths.clear()
        self.dirty_unknown = False

    def publish(self, generation: GraphGeneration) -> None:
        if self.active is not None and self.active is not generation:
            self.retired.append(self.active)
        self.active = generation
        self.next_generation_id = max(self.next_generation_id, generation.generation_id + 1)
        self.last_token = generation.token
        self.file_hashes = dict(generation.index.file_hashes)
        self.checkpoint_dirty = True
        self.last_access_at = time.time()
        self.clear_dirty()
        self.limit_error = None
        self.limit_exceeded_digest = None
        self.gc_retired()

    def abandon(self) -> None:
        """Drop the published graph. Next query must rebuild or fall back."""
        self.active = None
        self.retired.clear()
        self.clear_dirty()
        self.last_token = None
        self.file_hashes.clear()
        self.checkpoint_dirty = False
        self.last_full_build_seconds = None

    def gc_retired(self) -> None:
        self.retired = [item for item in self.retired if item.reader_count > 0]

    def estimated_bytes(self) -> int:
        total = self.active.estimated_bytes if self.active is not None else 0
        total += sum(item.estimated_bytes for item in self.retired)
        return total
