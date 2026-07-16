# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ExternalTeamClient against an in-memory team."""

import asyncio

import pytest

from openjiuwen.agent_teams.external import ExternalTeamClient
from openjiuwen.agent_teams.external import client as client_module
from openjiuwen.agent_teams.messager import hybrid as hybrid_module
from openjiuwen.agent_teams.schema.status import TaskStatus


class _FakeWebSocketPublisher:
    instances: list["_FakeWebSocketPublisher"] = []

    def __init__(self, **_kwargs) -> None:
        self.started = False
        self.stopped = False
        self.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def publish(self, _topic_id, _message) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_message_is_received_by_target(team_db, make_descriptor):
    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        message_id = await dev.send_message("leader", "hello leader")
        assert message_id

    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        inbox = await leader.fetch_inbox()
        assert any(m.content == "hello leader" for m in inbox.messages)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_is_received(team_db, make_descriptor):
    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        message_id = await leader.send_message("*", "team announcement")
        assert message_id

    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        inbox = await dev.fetch_inbox()
        assert any(m.content == "team announcement" for m in inbox.messages)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_fetch_inbox_marks_messages_read(team_db, make_descriptor):
    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        await leader.send_message("dev-1", "your task")

    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        first = await dev.fetch_inbox()
        assert any(m.content == "your task" for m in first.messages)

        second = await dev.fetch_inbox()
        assert all(m.content != "your task" for m in second.messages)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claim_and_complete_task(team_db, make_descriptor):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Do X",
        content="details",
        status=TaskStatus.PENDING.value,
    )

    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        claimable = await dev.claimable_tasks()
        assert any(t.task_id == "t1" for t in claimable)

        claim = await dev.claim_task("t1")
        assert claim.ok, claim.reason

        complete = await dev.complete_task("t1")
        assert complete.ok, complete.reason

        detail = await dev.get_task("t1")
        assert detail is not None
        assert detail.status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_update_task_edits_content(team_db, make_descriptor):
    await team_db.task.create_task(
        task_id="t2",
        team_name="ext_team",
        title="Old",
        content="old",
        status=TaskStatus.PENDING.value,
    )

    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        result = await leader.update_task("t2", title="New", content="new")
        assert result.ok, result.reason

        detail = await leader.get_task("t2")
        assert detail is not None
        assert detail.title == "New"
        assert detail.content == "new"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_list_members_returns_roster(team_db, make_descriptor):
    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        members = await dev.list_members()
        names = {m.member_name for m in members}
        assert {"leader", "dev-1"} <= names


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_scope_tools_follow_teammate_mode(team_db, make_descriptor):
    build_descriptor = make_descriptor(member="dev-1", scope="member", teammate_mode="build_mode")
    plan_descriptor = make_descriptor(member="dev-1", scope="member", teammate_mode="plan_mode")

    async with ExternalTeamClient(build_descriptor) as build_client:
        assert "submit_plan" not in build_client.tools

    async with ExternalTeamClient(plan_descriptor) as plan_client:
        assert "submit_plan" in plan_client.tools


@pytest.mark.asyncio
@pytest.mark.level1
async def test_operations_before_connect_raise(make_descriptor):
    client = ExternalTeamClient(make_descriptor(member="dev-1"))
    with pytest.raises(Exception):
        await client.list_members()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_websocket_connect_does_not_create_local_messager(team_db, make_descriptor, monkeypatch):
    descriptor = make_descriptor(member="dev-1")
    transport = descriptor.transport_config.model_copy(
        update={
            "backend": "hybrid",
            "external_publish_url": "ws://gateway:19000/ws",
        }
    )
    descriptor = descriptor.model_copy(update={"transport_config": transport})

    def fail_create_messager(_config):
        raise AssertionError("WebSocket mode must not initialize a local messager")

    _FakeWebSocketPublisher.instances.clear()
    monkeypatch.setattr(client_module, "create_messager", fail_create_messager)
    monkeypatch.setattr(hybrid_module, "WebSocketEventPublisher", _FakeWebSocketPublisher)

    client = ExternalTeamClient(descriptor)
    await client.connect()
    try:
        assert len(_FakeWebSocketPublisher.instances) == 1
        assert _FakeWebSocketPublisher.instances[0].started is True
    finally:
        await client.close()
    assert _FakeWebSocketPublisher.instances[0].stopped is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_backend_selects_local_messager_when_external_publish_url_exists(
    team_db,
    make_descriptor,
    monkeypatch,
):
    descriptor = make_descriptor(member="dev-1")
    transport = descriptor.transport_config.model_copy(
        update={"external_publish_url": "ws://gateway:19000/ws"}
    )
    descriptor = descriptor.model_copy(update={"transport_config": transport})

    def fail_websocket_publisher(**_kwargs):
        raise AssertionError("external_publish_url must not override the configured backend")

    monkeypatch.setattr(hybrid_module, "WebSocketEventPublisher", fail_websocket_publisher)

    async with ExternalTeamClient(descriptor):
        pass


@pytest.mark.asyncio
@pytest.mark.level1
async def test_watch_wakes_on_inbound_message(team_db, make_descriptor):
    received: list[str] = []
    ready = asyncio.Event()

    async def observer(view) -> None:
        received.extend(m.content for m in view.messages)
        ready.set()

    async with ExternalTeamClient(make_descriptor(member="dev-1")) as dev:
        watch_task = asyncio.create_task(dev.watch(observer))
        await asyncio.sleep(0)  # let the subscription register

        async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
            await leader.send_message("dev-1", "wake up")

        await asyncio.wait_for(ready.wait(), timeout=2.0)
        watch_task.cancel()
        try:
            await watch_task
        except asyncio.CancelledError:
            pass

    assert "wake up" in received
