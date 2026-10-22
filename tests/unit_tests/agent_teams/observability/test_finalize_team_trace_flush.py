# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""finalize_team_trace performs a single provider force_flush."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import openjiuwen.agent_teams.observability.setup as team_obs_setup
from openjiuwen.agent_teams.observability.monitor_handler import OtelTeamMonitorHandler


def test_close_team_spans_does_not_force_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.extensions.observability.config import ObservabilityConfig
    from openjiuwen.extensions.observability.semconv import AT_TEAM_NAME

    flush = MagicMock()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.setup.force_flush_provider",
        flush,
    )
    handler = OtelTeamMonitorHandler(ObservabilityConfig(), tracer=MagicMock())
    span = MagicMock()
    span.is_recording.return_value = True
    span.attributes = {AT_TEAM_NAME: "t1"}
    handler._task_spans["task-1"] = span

    handler.close_team_spans("t1")

    span.end.assert_called_once()
    flush.assert_not_called()


def test_finalize_team_trace_force_flushes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    flush = MagicMock()
    close_spans = MagicMock()
    finalize_trace = MagicMock()

    monkeypatch.setattr(team_obs_setup, "force_flush_provider", flush)
    monkeypatch.setattr(team_obs_setup, "finalize_trace", finalize_trace)
    monkeypatch.setattr(team_obs_setup, "_monitor_handler", MagicMock(close_team_spans=close_spans))

    team_obs_setup.finalize_team_trace("team-x")

    close_spans.assert_called_once_with("team-x")
    finalize_trace.assert_called_once_with("team-x")
    flush.assert_called_once_with()


def test_finalize_team_trace_skips_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    flush = MagicMock()
    monkeypatch.setattr(team_obs_setup, "force_flush_provider", flush)

    team_obs_setup.finalize_team_trace("")

    flush.assert_not_called()
