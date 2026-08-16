# coding: utf-8
from __future__ import annotations

from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal


def test_defaults_to_pause_and_not_set():
    sig = AbortSignal()
    assert sig.reason == "pause"
    assert sig.is_set() is False


def test_set_stop_carries_reason():
    sig = AbortSignal()
    sig.set("stop")
    assert sig.reason == "stop"
    assert sig.is_set() is True


def test_set_with_no_arg_defaults_to_pause():
    sig = AbortSignal()
    sig.set()
    assert sig.reason == "pause"
    assert sig.is_set() is True


def test_underlying_event_is_set():
    """The wrapped asyncio.Event mirrors the signal's set state."""
    sig = AbortSignal()
    assert sig.event.is_set() is False
    sig.set("pause")
    assert sig.event.is_set() is True
