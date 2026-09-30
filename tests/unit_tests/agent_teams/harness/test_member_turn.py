# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory turns of a Team member: which rounds open one and which keep it.

A member turn opens when a round starts from idle or drains follow-ups, and it
survives a steer, a pause and its resume, and an interrupt answer. The counter
lives in the member's session state, so a rebuilt harness keeps numbering.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.agent_teams.harness.turn import (
    MEMBER_TURN_STATE_KEY,
    MemberTurn,
    resolve_member_turn,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from tests.test_logger import logger
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    drain_outputs,
    make_card,
    make_spec,
    rebind_bridge_rails,
    start_harness,
    wait_completed_iterations,
    wait_for_state,
    wait_invoke_running,
)


class _DictStore:
    """Session-state stand-in backed by a plain dict."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})

    def get_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        self.state.update(data)


async def _record_started_turns(harness: NativeHarness, sink: list[MemberTurn]) -> None:
    """Collect the turn of every round the harness starts."""

    async def _on_round(kind: str) -> None:
        if kind != "started":
            return
        active = harness.active_round
        assert active is not None
        sink.append(active.turn)

    await harness.subscribe(on_round=_on_round)


def test_first_round_opens_turn_one_and_stages_it() -> None:
    store = _DictStore()

    turn, opened = resolve_member_turn(store, continues_turn=False)

    assert opened is True
    assert turn.turn_number == 1
    assert turn.turn_id
    assert store.state[MEMBER_TURN_STATE_KEY] == turn.to_dict()


def test_new_turn_advances_number_and_id() -> None:
    store = _DictStore()
    first, _ = resolve_member_turn(store, continues_turn=False)

    second, opened = resolve_member_turn(store, continues_turn=False)

    assert opened is True
    assert second.turn_number == first.turn_number + 1
    assert second.turn_id != first.turn_id


def test_continuation_keeps_the_latest_turn() -> None:
    store = _DictStore()
    first, _ = resolve_member_turn(store, continues_turn=False)

    kept, opened = resolve_member_turn(store, continues_turn=True)

    assert opened is False
    assert kept == first


def test_continuation_without_a_turn_on_record_opens_one() -> None:
    store = _DictStore()

    turn, opened = resolve_member_turn(store, continues_turn=True)

    assert opened is True
    assert turn.turn_number == 1


def test_corrupt_turn_state_reads_as_absent() -> None:
    store = _DictStore({MEMBER_TURN_STATE_KEY: {"turn_id": "", "turn_number": "seven"}})

    turn, opened = resolve_member_turn(store, continues_turn=True)

    assert opened is True
    assert turn.turn_number == 1


def test_numbering_is_monotonic_across_a_state_reload() -> None:
    """A rebuilt member reading the persisted state continues numbering."""
    before_restart = _DictStore()
    for _ in range(3):
        resolve_member_turn(before_restart, continues_turn=False)

    after_restart = _DictStore(before_restart.state)
    turn, _ = resolve_member_turn(after_restart, continues_turn=False)

    logger.info("turn after restart: %s", turn)
    assert turn.turn_number == 4


def test_without_a_store_a_turn_is_still_minted() -> None:
    turn, opened = resolve_member_turn(None, continues_turn=True)

    assert opened is True
    assert turn.turn_number == 1


@pytest.mark.asyncio
async def test_idle_start_and_follow_up_open_turns_while_steer_keeps_it() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.2)
        turns: list[MemberTurn] = []
        await _record_started_turns(harness, turns)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            await wait_invoke_running(fake)
            await harness.send("steer", immediate=True)
            await harness.send("q2", immediate=False)
            assert await wait_for_state(harness, HarnessState.IDLE)
            await harness.send("q3")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        logger.info("rounds: %s", [inv["query"] for inv in fake.invocations])
        # The steer joined round one; the follow-up and the idle send each
        # opened a turn of their own.
        assert [turn.turn_number for turn in turns] == [1, 2, 3]
        assert len({turn.turn_id for turn in turns}) == 3
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_pause_and_resume_keep_the_turn() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1
        turns: list[MemberTurn] = []
        await _record_started_turns(harness, turns)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("long job")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            fake.sleep_seconds = 0.0
            await harness.resume()
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(turns) == 2
        assert turns[0] == turns[1]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_interrupt_answer_from_idle_keeps_the_turn() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)
        turns: list[MemberTurn] = []
        await _record_started_turns(harness, turns)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("ask me something")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await harness.send(InteractiveInput("user-answer"))
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(turns) == 2
        assert turns[0] == turns[1]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_rebuilt_harness_continues_numbering_from_the_session() -> None:
    """The harness is rebuilt every run cycle; its turns keep climbing."""
    await Runner.start()
    try:
        session = Session(card=make_card("turn_owner"), session_id="turn_sid")
        await session.pre_run()
        turns: list[MemberTurn] = []
        for query in ("cycle one", "cycle two"):
            harness = NativeHarness(make_spec())
            await harness.start(session=session)
            fake = FakeReactAgent(harness.card)
            harness.set_react_agent(fake, initialized=True)
            await rebind_bridge_rails(harness, fake)
            await _record_started_turns(harness, turns)

            collected: list = []
            consumer = asyncio.create_task(drain_outputs(harness, collected))
            try:
                await harness.send(query)
                assert await wait_for_state(harness, HarnessState.IDLE)
            finally:
                await harness.stop()
                await consumer

        assert [turn.turn_number for turn in turns] == [1, 2]
        assert MemberTurn.from_dict(session.get_state(MEMBER_TURN_STATE_KEY)) == turns[-1]
        await session.post_run()
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_opening_round_commits_the_turn_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a round that advanced the counter checkpoints it."""
    await Runner.start()
    try:
        session = Session(card=make_card("turn_commit"), session_id="turn_commit_sid")
        await session.pre_run()
        commits: list[Any] = []
        original_commit = session.commit

        async def _commit() -> None:
            commits.append(session.get_state(MEMBER_TURN_STATE_KEY))
            await original_commit()

        monkeypatch.setattr(session, "commit", _commit)
        harness = NativeHarness(make_spec())
        await harness.start(session=session)
        fake = FakeReactAgent(harness.card)
        harness.set_react_agent(fake, initialized=True)
        await rebind_bridge_rails(harness, fake)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("opens a turn")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await harness.send(InteractiveInput("keeps it"))
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(commits) == 1
        assert commits[0]["turn_number"] == 1
        await session.post_run()
    finally:
        await Runner.stop()
