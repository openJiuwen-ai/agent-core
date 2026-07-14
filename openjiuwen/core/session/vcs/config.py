# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Configuration and backend factory for vcs."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.session.vcs import constants as const
from openjiuwen.core.session.vcs.backend import VersioningBackend


class VersioningConfig(BaseModel):
    """User-facing vcs configuration.

    Attributes:
        backend: Storage backend kind, ``"jsonl"`` or ``"kv"``.
        root: Filesystem root for the jsonl backend; defaults to
            ``<cwd>/.openjiuwen/vcs`` when None.
        fsync_policy: fsync timing for the jsonl backend.
        snapshot_every: Auto-snapshot after this many appends; ``<=0`` disables.
    """

    backend: Literal["jsonl", "kv"] = const.DEFAULT_BACKEND
    root: str | None = None
    fsync_policy: Literal["each", "batch", "snapshot", "off"] = const.DEFAULT_FSYNC_POLICY
    snapshot_every: int = const.DEFAULT_SNAPSHOT_EVERY


def default_root() -> Path:
    """Return the default jsonl root: ``<cwd>/.openjiuwen/vcs``."""
    return Path.cwd() / const.DEFAULT_ROOT_DIRNAME / const.VCS_DIRNAME


def build_backend(
    session_id: str,
    config: VersioningConfig,
    *,
    kv_store: BaseKVStore | None = None,
) -> VersioningBackend:
    """Construct a backend for `session_id` from config.

    Args:
        session_id: The session whose isolated space the backend manages.
        config: vcs configuration.
        kv_store: A BaseKVStore instance, required when ``config.backend == "kv"``.

    Returns:
        A VersioningBackend bound to the session.
    """
    if config.backend == const.BACKEND_KV:
        if kv_store is None:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="vcs kv backend requires a BaseKVStore instance",
            )
        from openjiuwen.core.session.vcs.kv_backend import KvBackend

        return KvBackend(session_id, kv_store)
    from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend

    root = config.root or str(default_root())
    return JsonlBackend(session_id, root, fsync_policy=config.fsync_policy)
