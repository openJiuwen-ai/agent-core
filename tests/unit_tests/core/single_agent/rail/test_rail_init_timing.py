# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026.
# All rights reserved.
"""Unit tests for the timed rail-init entry point."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail import base as rail_base
from openjiuwen.core.single_agent.rail.base import (
    init_rail,
    log_rail_init_breakdown,
)


class _RecordingRail:
    """Rail double recording the agent its init was handed."""

    def __init__(self) -> None:
        self.init_agent = None
        self.init_calls = 0

    def init(self, agent) -> None:
        """Record the init call."""
        self.init_agent = agent
        self.init_calls += 1


class _FailingRail:
    """Rail whose init raises, to check the timing still happens."""

    def init(self, agent) -> None:
        """Fail the way a misconfigured rail would."""
        raise ValueError("init exploded")


@pytest.fixture(name="captured_logs")
def _captured_logs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture the level each rail-init log line was emitted at."""
    records = {"info": [], "debug": []}

    def _record(level):
        def _log(message, *args):
            records[level].append(message % args if args else message)

        return _log

    monkeypatch.setattr(rail_base.logger, "info", _record("info"))
    monkeypatch.setattr(rail_base.logger, "debug", _record("debug"))
    return records


def test_init_rail_runs_init_with_the_agent() -> None:
    """The agent is passed straight through to the rail's init."""
    rail = _RecordingRail()
    agent = MagicMock()

    init_rail(rail, agent)

    assert rail.init_calls == 1
    assert rail.init_agent is agent


def test_init_rail_returns_elapsed_seconds() -> None:
    """The return value is what lets a batch caller build a breakdown."""
    elapsed = init_rail(_RecordingRail(), MagicMock())

    assert isinstance(elapsed, float)
    assert elapsed >= 0.0


def test_fast_init_is_logged_at_debug(captured_logs: dict) -> None:
    """A cheap init must not add an INFO line to every start-up."""
    init_rail(_RecordingRail(), MagicMock())

    assert captured_logs["info"] == []
    assert len(captured_logs["debug"]) == 1
    assert "_RecordingRail" in captured_logs["debug"][0]


def test_slow_init_is_logged_at_info(
    monkeypatch: pytest.MonkeyPatch, captured_logs: dict
) -> None:
    """Crossing the bar names the rail without needing debug logs on."""
    monkeypatch.setattr(rail_base, "SLOW_RAIL_INIT_SECONDS", 0.0)

    init_rail(_RecordingRail(), MagicMock())

    assert captured_logs["debug"] == []
    assert len(captured_logs["info"]) == 1
    assert "_RecordingRail" in captured_logs["info"][0]


def test_failing_init_still_records_timing(captured_logs: dict) -> None:
    """A rail that fails slowly must stay attributable."""
    with pytest.raises(ValueError, match="init exploded"):
        init_rail(_FailingRail(), MagicMock())

    assert len(captured_logs["debug"]) == 1
    assert "_FailingRail" in captured_logs["debug"][0]


def test_breakdown_ranks_slowest_rail_first(
    monkeypatch: pytest.MonkeyPatch, captured_logs: dict
) -> None:
    """The per-rail split is the point — a bare total names no suspect."""
    monkeypatch.setattr(rail_base, "SLOW_RAIL_INIT_BATCH_SECONDS", 0.0)

    log_rail_init_breakdown([("Fast", 0.001), ("Slow", 0.2), ("Middle", 0.05)])

    assert len(captured_logs["info"]) == 1
    line = captured_logs["info"][0]
    assert "3 rails initialized" in line
    assert line.index("Slow=") < line.index("Middle=") < line.index("Fast=")


def test_cheap_batch_is_logged_at_debug(captured_logs: dict) -> None:
    """A dozen quick rails should not announce themselves."""
    log_rail_init_breakdown([("A", 0.001), ("B", 0.002)])

    assert captured_logs["info"] == []
    assert len(captured_logs["debug"]) == 1


def test_empty_batch_logs_nothing(captured_logs: dict) -> None:
    """An agent with no pending rails has nothing to report."""
    log_rail_init_breakdown([])

    assert captured_logs["info"] == []
    assert captured_logs["debug"] == []
