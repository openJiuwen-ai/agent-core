# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable-ID sends must preserve the message accepted by the database."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.agent_teams.team_workspace.session_file_store import CONTENT_IN_FILE, FileAddress
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.models import _get_message_model
from openjiuwen.core.common.exception.errors import BaseError


@pytest_asyncio.fixture
async def publishers(tmp_path):
    """Independent database handles share real SQLite and session content files."""
    configure_openjiuwen_home(str(tmp_path / "home"))
    token = set_session_id("S")
    config = DatabaseConfig(connection_string=str(tmp_path / "messages.db"))
    databases = [TeamDatabase(config), TeamDatabase(config)]
    try:
        for database in databases:
            await database.initialize()
        await databases[0].team.create_team("T", "Publication Team", "speaker")
        managers = [TeamMessageManager("T", "speaker", database, AsyncMock(spec=Messager)) for database in databases]
        yield SimpleNamespace(databases=databases, managers=managers)
    finally:
        for database in reversed(databases):
            await database.close()
        reset_session_id(token)
        reset_openjiuwen_home()


async def _send(manager, kind, content):
    if kind == "direct":
        return (await manager.send_message(content, "a", message_id="id_a"),)
    if kind == "broadcast":
        return (await manager.broadcast_message(content, message_id="id_a"),)
    return tuple(await manager.multicast_message(
        content, ["a", "b"], message_ids=["id_a", "id_b"],
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["direct", "broadcast", "multicast"])
@pytest.mark.parametrize("same_content", [False, True], ids=["conflicting", "identical"])
async def test_concurrent_stable_id_sends_preserve_accepted_content(publishers, monkeypatch, kind, same_content):
    """Both senders pass preflight before either can insert or spill content."""
    barrier = asyncio.Barrier(2)
    preflight_count = 2 if kind == "multicast" else 1

    def synchronize(manager):
        original = manager._publication_exists
        checks = 0

        async def checked(*args, **kwargs):
            nonlocal checks
            exists = await original(*args, **kwargs)
            checks += 1
            if checks <= preflight_count:
                assert not exists
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return exists

        monkeypatch.setattr(manager, "_publication_exists", checked)

    for manager in publishers.managers:
        synchronize(manager)
    contents = ["first body", "first body" if same_content else "second body"]
    results = await asyncio.gather(*(
        _send(manager, kind, content) for manager, content in zip(publishers.managers, contents)
    ), return_exceptions=True)
    accepted = [index for index, result in enumerate(results) if not isinstance(result, BaseException)]
    assert len(accepted) == (2 if same_content else 1), results
    expected_ids = ("id_a", "id_b") if kind == "multicast" else ("id_a",)
    for index in accepted:
        assert results[index] == expected_ids
    for result in results:
        if isinstance(result, BaseException):
            assert isinstance(result, BaseError)
            assert "different content" in str(result)

    database = publishers.databases[0]
    expected_content = contents[accepted[0]]
    model = _get_message_model()
    async with database.sessions.read() as session:
        rows = list(await session.scalars(select(model)))
    assert {row.message_id for row in rows} == set(expected_ids)
    for row in rows:
        assert (await database.message.get_message(row.message_id)).content == expected_content
        if row.content == CONTENT_IN_FILE:
            address = FileAddress("T", "S", "broadcast" if kind == "broadcast" else "direct",
                                  row.message_id, row.to_member_name)
            assert database.message._file_store.get(address) == expected_content


@pytest.mark.asyncio
async def test_failed_multicast_preserves_existing_content(publishers):
    database = publishers.databases[0]
    assert await database.message.create_message("id_a", "T", "speaker", "original", to_member_name="a")
    assert await database.message.create_direct_messages(
        team_name="T", from_member_name="speaker", content="conflict",
        recipients=[("id_a", "a"), ("id_b", "b")],
    ) == 0
    assert (await database.message.get_message("id_a")).content == "original"
    assert await database.message.get_message("id_b") is None


@pytest.mark.asyncio
async def test_orphan_file_does_not_override_new_message(publishers):
    database = publishers.databases[0]
    store = database.message._file_store
    address = FileAddress("T", "S", "direct", "id_a", "a")
    store.put("orphan body", address)
    assert await publishers.managers[0].send_message("accepted body", "a", message_id="id_a") == "id_a"
    assert (await database.message.get_message("id_a")).content == "accepted body"
    assert store.get(address) == "orphan body"
    assert await publishers.managers[1].send_message("accepted body", "a", message_id="id_a") == "id_a"


@pytest.mark.asyncio
async def test_unsupported_hardlinks_keep_content_inline(publishers, monkeypatch):
    def unsupported_link(*args, **kwargs):
        raise OSError("Hardlinks are not supported")

    monkeypatch.setattr("openjiuwen.agent_teams.team_workspace.session_file_store.os.link", unsupported_link)
    database = publishers.databases[0]
    assert await publishers.managers[0].send_message("inline body", "a", message_id="id_a") == "id_a"
    model = _get_message_model()
    async with database.sessions.read() as session:
        row = await session.get(model, "id_a")
    assert row.content == "inline body"
    assert (await database.message.get_message("id_a")).content == "inline body"
    address = FileAddress("T", "S", "direct", "id_a", "a")
    assert not list(database.message._file_store._resolve(address).parent.iterdir())
