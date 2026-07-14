# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session state/context version control (vcs).

Per-session linear version control for an agent's LLM context (message-level
append-only WAL) and kv state, with commit, snapshot, replay and overwrite
rewind. ``fork`` clones a brand-new Session (new session_id) for parallel use
by another agent. All records are JSON (never pickle); the kv backend reuses
an injected ``BaseKVStore``.

Typical entry point::

    from openjiuwen.core.session.vcs import for_session, VersioningConfig
    vc = for_session(session, context_engine, config=VersioningConfig())
    await vc.append()
    cid = await vc.commit("milestone")
    fork = await vc.fork(at=cid)
"""
from openjiuwen.core.session.vcs.adapter import for_session
from openjiuwen.core.session.vcs.backend import VersioningBackend
from openjiuwen.core.session.vcs.config import VersioningConfig, build_backend
from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.manager import VersioningManager
from openjiuwen.core.session.vcs.models import (
    Commit,
    ForkResult,
    Head,
    LogEntry,
    MessageDelta,
    Snapshot,
    StateDelta,
)
from openjiuwen.core.session.vcs.protocol import VersionControl

__all__ = [
    "VersionControl",
    "VersioningManager",
    "VersioningBackend",
    "JsonlBackend",
    "KvBackend",
    "VersioningConfig",
    "build_backend",
    "for_session",
    "MessageDelta",
    "StateDelta",
    "LogEntry",
    "Commit",
    "Snapshot",
    "Head",
    "ForkResult",
]
