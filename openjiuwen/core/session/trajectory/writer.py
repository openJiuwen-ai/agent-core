# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Snapshot persistence for trajectories.

File layout, with one folder per top-level run whose name
(``<YYYY-MM-DD_HH-MM-SS>_<trajectory_id>``, built by the collector) makes runs
identifiable and sortable by start time::

    <root>/<session_id>/<run_dir>/trajectory.json
    <root>/<session_id>/<run_dir>/subagents/<YYYY-MM-DD_HH-MM-SS>_<trajectory_id>.json

With ``TrajectoryConfig.group_by_agent_session`` enabled, ``<run_dir>`` is
prefixed by the owning agent session's id:
``<root>/<session_id>/<agent_session_id>/<run_dir>/...``.

Each file holds one pretty-printed JSON document mirroring the ``Trajectory``
model and is atomically rewritten after every step, so it is always valid JSON
and always current up to the last completed step. ``final_metrics`` carries
the running totals accumulated so far; its ``ended_at`` stays empty until the
owning session finishes (``Session.post_run``) or recording stops.
"""

import asyncio
import json
import os
from pathlib import Path

from openjiuwen.core.session.trajectory.models import Trajectory


def _json_default(obj) -> str:
    return f"<<non-serializable: {type(obj).__qualname__}>>"


class TrajectoryWriter:
    """Atomically rewrites per-trajectory JSON snapshot files."""

    def __init__(self, root: Path, session_id: str):
        self._root = root
        self._session_id = session_id

    def path_for(self, rel_path: str) -> Path:
        return self._root / self._session_id / rel_path

    async def write_snapshot(self, rel_path: str, document: dict) -> None:
        await asyncio.to_thread(self._write_snapshot_sync, self.path_for(rel_path), document)

    @staticmethod
    def _write_snapshot_sync(path: Path, document: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, ensure_ascii=False, indent=2, default=_json_default)
        # Write-then-replace keeps the visible file valid JSON at all times,
        # even if the process dies mid-write.
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp_path, path)


def read_trajectory(path: str | Path) -> Trajectory:
    """Load a ``Trajectory`` from a snapshot file written by ``TrajectoryWriter``."""
    with open(path, "r", encoding="utf-8") as f:
        return Trajectory.model_validate(json.load(f))
