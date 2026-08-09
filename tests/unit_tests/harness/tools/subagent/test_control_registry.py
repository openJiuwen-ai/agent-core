# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for subagent control registry."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.subagent._control_registry import (
    get_subagent_control,
    release_all_subagent_controls,
    release_subagent_control,
)


@pytest.mark.asyncio
async def test_get_subagent_control_caches_per_parent_session() -> None:
    parent = SimpleNamespace()
    session_a = Session(session_id="parent_a")
    session_b = Session(session_id="parent_b")

    control_a1 = get_subagent_control(parent, session_a)
    control_a2 = get_subagent_control(parent, session_a)
    control_b = get_subagent_control(parent, session_b)

    assert control_a1 is control_a2
    assert control_b is not control_a1
    assert control_a1._parent_session_id == "parent_a"


@pytest.mark.asyncio
async def test_release_subagent_control_cancels_and_drops_cache() -> None:
    parent = SimpleNamespace()
    session = Session(session_id="parent_sess")
    control = get_subagent_control(parent, session)
    control.cancel_all = AsyncMock(return_value=["sub1"])
    control.flush = MagicMock()

    await release_subagent_control(parent, "parent_sess", reason="test")

    control.cancel_all.assert_awaited_once_with("test")
    control.flush.assert_called_once()
    assert not hasattr(parent, "_subagent_controls") or "parent_sess" not in getattr(
        parent,
        "_subagent_controls",
        {},
    )


def test_get_subagent_control_requires_session() -> None:
    parent = SimpleNamespace()
    with pytest.raises(Exception):
        get_subagent_control(parent, None)


@pytest.mark.asyncio
async def test_release_all_subagent_controls_cancels_every_cached_session() -> None:
    parent = SimpleNamespace()
    session_a = Session(session_id="parent_a")
    session_b = Session(session_id="parent_b")
    control_a = get_subagent_control(parent, session_a)
    control_b = get_subagent_control(parent, session_b)
    control_a.cancel_all = AsyncMock(return_value=["a1"])
    control_b.cancel_all = AsyncMock(return_value=["b1"])
    control_a.flush = MagicMock()
    control_b.flush = MagicMock()

    await release_all_subagent_controls(parent, reason="rail_uninit")

    control_a.cancel_all.assert_awaited_once_with("rail_uninit")
    control_b.cancel_all.assert_awaited_once_with("rail_uninit")
    control_a.flush.assert_called_once()
    control_b.flush.assert_called_once()
    assert getattr(parent, "_subagent_controls", {}) == {}


@pytest.mark.asyncio
async def test_controls_for_different_parent_sessions_are_isolated() -> None:
    parent = SimpleNamespace()
    session_a = Session(session_id="parent_a")
    session_b = Session(session_id="parent_b")

    control_a = get_subagent_control(parent, session_a)
    control_b = get_subagent_control(parent, session_b)

    assert control_a is not control_b
    assert control_a._parent_session_id == "parent_a"
    assert control_b._parent_session_id == "parent_b"
    assert control_a._manager is not control_b._manager
