# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-run memory for find_* tools: selected spans, evidence, and submit packets.

Not an agent. Lives next to the tools because submit_code_context and the
profile rail share this process-level store.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from openjiuwen.harness.schema.code_graph import (
    CodeGraphLocation,
    CodeGraphRelation,
    CodeGraphRunState,
    LocalizationPhase,
)
from openjiuwen.harness.schema.coding_artifacts import LocalizationArtifact, new_loc_id

_lock = threading.Lock()
_sessions_by_key: OrderedDict[str, LocalizationSession] = OrderedDict()
_sessions_by_id: dict[str, LocalizationSession] = {}

MAX_LOCALIZATION_SESSIONS = 32
DIMINISHING_RETURN_STREAK = 3


def normalize_task(task: str) -> str:
    return " ".join(str(task or "").lower().split())


def localization_session_key(repo_root: str, task: str) -> str:
    return f"{Path(repo_root).resolve()}|{normalize_task(task)}"


def hash_search_query(
    query: str,
    *,
    symbol_kinds: Sequence[str] | None = None,
    path_prefix: str | None = None,
    include_tests: bool = False,
) -> str:
    kinds = ",".join(sorted(str(item) for item in (symbol_kinds or [])))
    raw = f"{normalize_task(query)}|{kinds}|{path_prefix or ''}|{int(bool(include_tests))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class LocalizationSession:
    """Shared memory for find_* tools across Root ``task_tool`` invocations."""

    artifact_id: str
    repo_root: str
    task: str
    key: str
    selected: list[CodeGraphLocation] = field(default_factory=list)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[CodeGraphRelation] = field(default_factory=list)
    query_hashes: set[str] = field(default_factory=set)
    seen_symbol_ids: set[str] = field(default_factory=set)
    empty_gain_streak: int = 0
    warnings: list[str] = field(default_factory=list)
    index_snapshot: str = ""
    artifact: LocalizationArtifact | None = None
    expanded_files: set[str] = field(default_factory=set)
    probed_inheritance: set[str] = field(default_factory=set)
    read_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen_files: set[str] = field(default_factory=set)
    # Survives across invocations so a refinement continues the same episode
    # instead of re-running localization from UNBOUND.
    phase: str = LocalizationPhase.UNBOUND.value
    committed_packets: int = 0


def reset_localization_sessions() -> None:
    """Test helper: drop process-level locator sessions."""
    with _lock:
        _sessions_by_key.clear()
        _sessions_by_id.clear()


def localization_session_count() -> int:
    """Test helper: how many locator sessions this process is holding."""
    with _lock:
        return len(_sessions_by_key)


def drop_localization(*, artifact_id: str = "", session_key: str = "") -> None:
    """Forget one locator episode. Safe when the ids are empty or unknown."""
    with _lock:
        session = _sessions_by_id.get(str(artifact_id or ""))
        if session is None and session_key:
            session = _sessions_by_key.get(session_key)
        if session is None:
            return
        _sessions_by_key.pop(session.key, None)
        _sessions_by_id.pop(session.artifact_id, None)


def _touch(session: LocalizationSession) -> None:
    _sessions_by_key[session.key] = session
    _sessions_by_key.move_to_end(session.key)
    _sessions_by_id[session.artifact_id] = session
    limit = max(1, int(MAX_LOCALIZATION_SESSIONS))
    while len(_sessions_by_key) > limit:
        _, oldest = _sessions_by_key.popitem(last=False)
        _sessions_by_id.pop(oldest.artifact_id, None)


def create_localization(repo_root: str, task: str) -> LocalizationSession:
    """First locator run for ``(repo, task)``. Reuses the session if it exists."""
    key = localization_session_key(repo_root, task)
    with _lock:
        existing = _sessions_by_key.get(key)
        if existing is not None:
            _touch(existing)
            return existing
        session = LocalizationSession(
            artifact_id=new_loc_id(),
            repo_root=str(Path(repo_root).resolve()),
            task=task,
            key=key,
        )
        _touch(session)
        return session


def refine_localization(artifact_id: str) -> LocalizationSession:
    """Look up an existing locator artifact. Used to continue the same episode."""
    with _lock:
        session = _sessions_by_id.get(str(artifact_id or ""))
        if session is None:
            raise KeyError(f"unknown localization artifact: {artifact_id}")
        _touch(session)
        return session


def bind_run_state(state: CodeGraphRunState, session: LocalizationSession) -> None:
    """Seed a new agent invocation from the shared session."""
    state.artifact_id = session.artifact_id
    state.session_key = session.key
    state.selected = list(session.selected)
    state.candidates = dict(session.candidates)
    state.relations = list(session.relations)
    state.query_hashes = set(session.query_hashes)
    state.seen_symbol_ids = set(session.seen_symbol_ids)
    state.empty_gain_streak = session.empty_gain_streak
    state.warnings = list(session.warnings)
    state.index_snapshot = session.index_snapshot
    state.expanded_files = set(session.expanded_files)
    state.probed_inheritance = set(session.probed_inheritance)
    state.read_evidence = dict(session.read_evidence)
    state.seen_files = set(session.seen_files)
    state.phase = session.phase
    state.committed_packets = session.committed_packets


def persist_run_state(state: CodeGraphRunState) -> LocalizationSession | None:
    """Write tool progress back so the next ``task_tool`` can refine."""
    if not state.artifact_id:
        return None
    with _lock:
        session = _sessions_by_id.get(state.artifact_id)
        if session is None:
            return None
        _touch(session)
        session.selected = list(state.selected)
        session.candidates = dict(state.candidates)
        session.relations = list(state.relations)
        session.query_hashes = set(state.query_hashes)
        session.seen_symbol_ids = set(state.seen_symbol_ids)
        session.empty_gain_streak = state.empty_gain_streak
        session.warnings = list(state.warnings)
        session.index_snapshot = state.index_snapshot
        session.expanded_files = set(state.expanded_files)
        session.probed_inheritance = set(state.probed_inheritance)
        session.read_evidence = dict(state.read_evidence)
        session.seen_files = set(state.seen_files)
        session.phase = state.phase
        session.committed_packets = state.committed_packets
        return session


def persist_artifact(state: CodeGraphRunState, artifact: LocalizationArtifact) -> None:
    session = persist_run_state(state)
    if session is not None:
        session.artifact = artifact
