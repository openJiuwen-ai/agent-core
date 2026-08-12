# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.agent_rl.online.rail.factory import build_rl_online_rail_from_env
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor


class _FakeUploader:
    def __init__(self, endpoint: str, *, api_key: str = "", wal_dir: str = "") -> None:
        self.gateway_endpoint = endpoint
        self.api_key = api_key
        self.wal_dir = wal_dir


def test_factory_injects_the_explicit_processor(monkeypatch) -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "true")
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.agent_rl.online.rail.uploader.TrajectoryUploader",
        _FakeUploader,
    )
    processor = TrajectorySpanProcessor()

    rail = build_rl_online_rail_from_env(trajectory_span_processor=processor)

    assert rail is not None
    assert rail.trajectory_span_processor is processor
    assert rail._uploader.gateway_endpoint == "http://127.0.0.1:18080"


def test_factory_does_not_fallback_when_processor_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "true")

    with pytest.raises(TypeError, match="trajectory_span_processor"):
        build_rl_online_rail_from_env(trajectory_span_processor=None)  # type: ignore[arg-type]
