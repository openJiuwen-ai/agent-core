# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Configuration for trajectory recording."""
from pathlib import Path

from pydantic import BaseModel

DEFAULT_ROOT_DIRNAME = ".openjiuwen"
TRAJECTORY_DIRNAME = "trajectories"
DEFAULT_MAX_CONTENT_CHARS = 8192
DEFAULT_MAX_RETAINED_TRACES = 1024


class TrajectoryConfig(BaseModel):
    """User-facing trajectory recording configuration.

    Attributes:
        root: Filesystem root for trajectory files; defaults to
            ``<cwd>/.openjiuwen/trajectories`` when None.
        max_content_chars: Truncation guard for text payloads (message,
            reasoning, tool arguments, observation content). ``<=0`` disables
            truncation.
        group_by_agent_session: When True, top-level run directories are
            grouped under the owning agent session's id
            (``<root>/<session_id>/<agent_session_id>/<run_dir>/``), for
            long-lived hosts serving many agent sessions per process. Default
            False keeps the flat ``<root>/<session_id>/<run_dir>/`` layout.
        snapshot_every_n_steps: Persist the snapshot file every N appended
            steps instead of after every step (finalization always writes).
            Default 1 keeps the always-current-file behavior; raise it for
            long runs where per-step full-document rewrites are too costly.
        redact_secrets: Apply best-effort secret redaction (api keys, bearer
            tokens, passwords, ...) to persisted text payloads. Default True.
        max_retained_traces: Upper bound on finalized traces whose identity
            and lineage records are kept in memory, for long-lived hosts.
            Oldest records are evicted beyond the bound; late steps for an
            evicted trace are dropped.
    """

    root: str | None = None
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS
    group_by_agent_session: bool = False
    snapshot_every_n_steps: int = 1
    redact_secrets: bool = True
    max_retained_traces: int = DEFAULT_MAX_RETAINED_TRACES


def default_root() -> Path:
    """Return the default trajectory root: ``<cwd>/.openjiuwen/trajectories``."""
    return Path.cwd() / DEFAULT_ROOT_DIRNAME / TRAJECTORY_DIRNAME
