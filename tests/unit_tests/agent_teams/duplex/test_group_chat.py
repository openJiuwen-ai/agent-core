# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Duplex routing of group chat posts: "@ a member" and "no @" rows of the scenario table."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.duplex.handlers import DuplexGroupMessageHandler
from openjiuwen.agent_teams.duplex.policy import MessageSource
from openjiuwen.agent_teams.harness.protocol import INTERRUPT_APPLIED
from openjiuwen.agent_teams.message_template import ExpandedMessage
from tests.unit_tests.agent_teams.duplex.test_router import FakeClassifier, FakeHost, _answer, _marked, _router

URGENT = {"urgency": _answer("INTERRUPT", "APPEND", 0.95)}
NOT_URGENT = {"urgency": _answer("INTERRUPT", "APPEND", 0.85)}


class FakeInterruptible:
    def __init__(self) -> None:
        self.contents: list[str] = []

    async def interrupt(self, content: str) -> str:
        self.contents.append(content)
        return INTERRUPT_APPLIED


def _group_row(mid: str, sender: str, mentions: list[str]) -> Any:
    meta = {
        "type": "group_chat",
        "client_message_id": mid,
        "sender_name": sender,
        "mentions": mentions,
        "attachments": [],
    }
    return SimpleNamespace(
        message_id=mid,
        from_member_name=sender,
        content=f"raw {mid}",
        broadcast=True,
        protocol="",
        meta=json.dumps(meta),
        timestamp=1.0,
    )


def _handler(router: Any) -> DuplexGroupMessageHandler:
    handler = DuplexGroupMessageHandler.__new__(DuplexGroupMessageHandler)
    handler._router = router
    handler._blueprint = SimpleNamespace(member_name="dev", team_spec=SimpleNamespace(leader_member_name="leader"))
    backend = MagicMock()
    backend.is_human_agent = AsyncMock(return_value=False)
    backend.is_passive_human = AsyncMock(return_value=False)
    handler._infra = SimpleNamespace(team_backend=backend)
    return handler


async def _deliver(handler: DuplexGroupMessageHandler, row: Any) -> bool:
    expanded = ExpandedMessage(body=f"excerpt {row.message_id}", is_template=False)
    return await handler._deliver_message(row, f"excerpt {row.message_id}", expanded=expanded, use_steer=True)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_group_scene_from_mentions() -> None:
    router, _, _ = _router(FakeHost(running=False), FakeClassifier(URGENT))
    handler = _handler(router)
    expanded = ExpandedMessage(body="x", is_template=False)
    assert await handler._message_source(_group_row("g1", "peer", ["dev"]), expanded) is MessageSource.GROUP_MENTION
    assert await handler._message_source(_group_row("g2", "peer", ["qa"]), expanded) is MessageSource.GROUP_PLAIN
    assert await handler._message_source(_group_row("g3", "peer", []), expanded) is MessageSource.GROUP_PLAIN
    # The user @-ing a member is a user message: never judged.
    assert await handler._message_source(_group_row("g4", "user", ["dev"]), expanded) is MessageSource.USER


@pytest.mark.level0
@pytest.mark.asyncio
async def test_group_mention_idle_target_is_delivered_now_without_judgment() -> None:
    host = FakeHost(running=False)
    classifier = FakeClassifier(URGENT)
    router, _, _ = _router(host, classifier)
    assert await _deliver(_handler(router), _group_row("g1", "peer", ["dev"])) is True
    assert host.delivered == [("excerpt g1", True)] and classifier.batches == []


@pytest.mark.level0
@pytest.mark.asyncio
async def test_group_mention_busy_target_interrupts_only_when_urgent() -> None:
    harness = FakeInterruptible()
    host = FakeHost(running=True, harness=harness)
    classifier = FakeClassifier(URGENT)
    router, infra, _ = _router(host, classifier)
    handler = _handler(router)
    assert await _deliver(handler, _group_row("g1", "peer", ["dev"])) is False  # taken over by the router
    await router.scheduler.wait_idle()
    # The supervisor sees the raw post, not the delivered excerpt; the target gets the excerpt.
    assert classifier.batches == [["from peer: raw g1"]]
    assert len(harness.contents) == 1 and "excerpt g1" in harness.contents[0]
    assert host.delivered == [] and _marked(infra) == ["g1"]

    classifier.answers = NOT_URGENT  # 0.85 < 0.9: queued, not interrupted
    assert await _deliver(handler, _group_row("g2", "peer", ["dev"])) is False
    await router.scheduler.wait_idle()
    assert host.delivered == [("excerpt g2", False)] and len(harness.contents) == 1
    assert _marked(infra) == ["g1", "g2"]


@pytest.mark.level0
@pytest.mark.asyncio
async def test_group_mention_busy_target_supervisor_failure_queues() -> None:
    host = FakeHost(running=True, harness=FakeInterruptible())
    router, infra, _ = _router(host, FakeClassifier(RuntimeError("supervisor down")))
    assert await _deliver(_handler(router), _group_row("g1", "peer", ["dev"])) is False
    await router.scheduler.wait_idle()
    assert host.delivered == [("excerpt g1", False)] and _marked(infra) == ["g1"]


@pytest.mark.level0
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.asyncio
async def test_group_post_without_mention_is_only_recorded(running: bool) -> None:
    host = FakeHost(running=running, harness=FakeInterruptible())
    classifier = FakeClassifier(URGENT)
    router, _, _ = _router(host, classifier)
    # Marked read by the drain, never delivered, never judged.
    assert await _deliver(_handler(router), _group_row("g1", "peer", ["qa"])) is True
    assert host.delivered == [] and classifier.batches == []
