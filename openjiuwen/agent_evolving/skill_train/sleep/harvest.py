# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Harvest JiuwenSwarm ``traces-*.jsonl`` into SessionDigest records."""

from __future__ import annotations

from pathlib import Path
from typing import List

from openjiuwen.agent_evolving.skill_train.sleep.config import SleepConfig
from openjiuwen.agent_evolving.skill_train.sleep.jiuwenswarm_traces import (
    has_jiuwenswarm_traces,
    load_jiuwenswarm_session_digests,
)
from openjiuwen.agent_evolving.skill_train.sleep.types import SessionDigest


def harvest_otlp_trajectories(cfg: SleepConfig) -> List[SessionDigest]:
    """Load JiuwenSwarm traces into SessionDigest rows, one per ``session.id``.

    ``cfg.trajectory_store_dir`` may be a directory containing
    ``traces-*.jsonl`` or a single trace file. Returns an empty list when no
    trace files are present.
    """
    root = Path(cfg.resolved_trajectory_dir())
    if not has_jiuwenswarm_traces(root):
        return []
    return load_jiuwenswarm_session_digests(
        root,
        project=cfg.project or "invoked",
        session_id=cfg.session_id,
        max_sessions=cfg.max_trajectories,
    )
