# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persist colliding legacy targets and recover them through a fresh session."""

import json
from pathlib import Path

import pytest

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.session_controller.chain_session import ChainSession
from openjiuwen.core.session.session_controller.data_container import AgentSessionContainer, Permission, SharingPolicy
from openjiuwen.core.session.session_controller.scope import MainScope, SessionScope


def _session(directory: Path) -> ChainSession:
    return ChainSession(
        "source", SessionScope(MainScope()), "session",
        AgentSessionContainer(create_agent_session(session_id="session")), directory,
    )


@pytest.mark.asyncio
async def test_colliding_and_path_like_ids_survive_flush_load(tmp_path: Path) -> None:
    session = _session(tmp_path)
    targets = [("agent_worker", "s"), ("agent", "worker_s"), ("../中文", "a/b:c")]
    for index, (agent_id, session_id) in enumerate(targets):
        session.add_downstream(agent_id, session_id, SharingPolicy(
            permission=Permission.READ, field_scopes={f"field-{index}"},
        ))
    assert await session.flush()
    files = list((tmp_path / "downstreams").glob("*.link"))
    assert len(files) == 3
    assert {(json.loads(path.read_text())["target_agent"],
             json.loads(path.read_text())["target_session"]) for path in files} == set(targets)
    restored = _session(tmp_path)
    assert await restored.load()
    for index, target in enumerate(targets):
        assert restored.get_downstream_policy(*target).field_scopes == {f"field-{index}"}
    restored.remove_downstream(*targets[0])
    assert await restored.flush()
    reloaded = _session(tmp_path)
    assert await reloaded.load()
    assert set(reloaded.get_downstreams()) == set(targets[1:])


@pytest.mark.asyncio
async def test_legacy_link_migrates_without_duplicate_or_resurrected_relationship(tmp_path: Path) -> None:
    directory = tmp_path / "downstreams"
    directory.mkdir()
    old_file = directory / "agent_session.link"
    old_file.write_text(json.dumps({"permission": {"level": 1}}), encoding="utf-8")
    session = _session(tmp_path)
    assert await session.load()
    assert session.has_downstream("agent", "session")
    assert await session.flush()
    assert not old_file.exists()
    assert len(list(directory.glob("*.link"))) == 1
    session.remove_downstream("agent", "session")
    assert await session.flush()
    restored = _session(tmp_path)
    assert await restored.load()
    assert restored.get_downstreams() == {}
