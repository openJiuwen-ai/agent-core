# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Tests for the per-callback breakdown of an event's chain.
"""

import asyncio

import pytest

from openjiuwen.core.runner.callback import framework as framework_module
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.runner.callback.framework import _callback_owner_name


class _RecordingLogger:
    """Logger double capturing the args of each call."""

    def __init__(self):
        self.info_records = []
        self.debug_records = []

    def info(self, msg, *args):
        self.info_records.append((msg, args))

    def debug(self, msg, *args):
        self.debug_records.append((msg, args))

    def warning(self, msg, *args, **kwargs):
        pass

    def error(self, msg, *args, **kwargs):
        pass


@pytest.fixture(name="chain_logger")
def _chain_logger(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(framework_module, "runner_logger", recorder)
    return recorder


def _breakdowns(recorder):
    """Return the breakdown lines emitted at either level."""
    return [
        rec
        for rec in recorder.info_records + recorder.debug_records
        if "callbacks=" in rec[0]
    ]


class _SlowRail:
    """Rail double whose hook dominates the chain."""

    async def before_model_call(self):
        await asyncio.sleep(0.08)


class _FastRail:
    """Rail double whose hook is negligible."""

    async def before_model_call(self):
        await asyncio.sleep(0)


def test_owner_name_uses_the_rail_class():
    """A bound rail method must be named by its rail, not by the hook."""
    assert _callback_owner_name(_SlowRail().before_model_call) == "_SlowRail"


def test_owner_name_falls_back_for_plain_functions():
    """A module-level callback has no owner, so its own name identifies it."""

    async def standalone():
        return None

    assert _callback_owner_name(standalone) == "standalone"


@pytest.mark.asyncio
async def test_every_callback_appears_in_the_breakdown(framework, chain_logger):
    """The point of the line is telling a dozen rails apart."""
    framework.on("before_model_call")(_SlowRail().before_model_call)
    framework.on("before_model_call")(_FastRail().before_model_call)

    await framework.trigger("before_model_call")

    lines = _breakdowns(chain_logger)
    assert len(lines) == 1
    breakdown = lines[0][1][-1]
    assert "_SlowRail=" in breakdown
    assert "_FastRail=" in breakdown


@pytest.mark.asyncio
async def test_breakdown_is_ordered_slowest_first(framework, chain_logger):
    """The offender leads, so a long line does not have to be scanned."""
    framework.on("before_model_call")(_FastRail().before_model_call)
    framework.on("before_model_call")(_SlowRail().before_model_call)

    await framework.trigger("before_model_call")

    breakdown = _breakdowns(chain_logger)[0][1][-1]
    assert breakdown.startswith("_SlowRail=")


@pytest.mark.asyncio
async def test_slow_chain_is_reported_at_info(framework, chain_logger):
    """A chain over the bar must be visible without enabling debug logs."""
    framework.on("before_model_call")(_SlowRail().before_model_call)

    await framework.trigger("before_model_call")

    assert len(_breakdowns(chain_logger)) == 1
    assert any("callbacks=" in rec[0] for rec in chain_logger.info_records)


@pytest.mark.asyncio
async def test_cheap_chain_stays_at_debug(framework, chain_logger):
    """Ordinary events must not add an INFO line each."""
    framework.on("before_model_call")(_FastRail().before_model_call)

    await framework.trigger("before_model_call")

    assert not any("callbacks=" in rec[0] for rec in chain_logger.info_records)
    assert any("callbacks=" in rec[0] for rec in chain_logger.debug_records)


@pytest.mark.asyncio
async def test_event_without_callbacks_logs_nothing(framework, chain_logger):
    """An event nobody subscribes to must stay silent."""
    await framework.trigger("unsubscribed_event")

    assert _breakdowns(chain_logger) == []


@pytest.mark.asyncio
async def test_failing_callback_still_owns_its_share(framework, chain_logger):
    """A hook that fails slowly must not vanish from the accounting."""

    class _FailingRail:
        async def before_model_call(self):
            await asyncio.sleep(0.06)
            raise ValueError("boom")

    framework.on("before_model_call")(_FailingRail().before_model_call)

    await framework.trigger("before_model_call")

    breakdown = _breakdowns(chain_logger)[0][1][-1]
    assert "_FailingRail=" in breakdown


@pytest.mark.asyncio
async def test_aborted_chain_reports_what_ran(framework, chain_logger):
    """An aborted chain is exactly when the split matters most."""

    class _AbortingRail:
        async def before_model_call(self):
            raise AbortError(reason="stop here")

    framework.on("before_model_call")(_SlowRail().before_model_call)
    framework.on("before_model_call")(_AbortingRail().before_model_call)

    with pytest.raises(AbortError):
        await framework.trigger("before_model_call")

    breakdown = _breakdowns(chain_logger)[0][1][-1]
    assert "_SlowRail=" in breakdown
