# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore, BasedKVStorePipeline
from openjiuwen.core.graph.store import create_state
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.checkpointer.persistence import (
    AgentTeamStorage,
    AgentStorage,
    PersistenceCheckpointer,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.session.state.agent_state import StateCollection


class MockKVStore(BaseKVStore):
    def __init__(self):
        self._store = {}

    async def set(self, key: str, value: str | bytes):
        self._store[key] = value

    async def get(self, key: str) -> str | bytes | None:
        return self._store.get(key)

    async def delete(self, key: str):
        self._store.pop(key, None)

    async def exclusive_set(self, key: str, value: str | bytes, expiry: int | None = None) -> bool:
        if key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def get_by_prefix(self, prefix: str) -> dict[str, str | bytes]:
        return {key: value for key, value in self._store.items() if key.startswith(prefix)}

    async def delete_by_prefix(self, prefix: str, batch_size=None):
        keys_to_delete = [key for key in self._store if key.startswith(prefix)]
        for key in keys_to_delete:
            del self._store[key]

    async def mget(self, keys: list) -> list:
        return [self._store.get(key) for key in keys]

    async def batch_delete(self, keys: list, batch_size=None) -> int:
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                deleted += 1
        return deleted

    def pipeline(self):
        async def execute(operations):
            results = []
            for operation in operations:
                if operation[0] == "set":
                    self._store[operation[1]] = operation[2]
                    results.append(None)
                elif operation[0] == "get":
                    results.append(self._store.get(operation[1]))
                elif operation[0] == "exists":
                    results.append(operation[1] in self._store)
            return results

        return BasedKVStorePipeline(execute)


@pytest.mark.asyncio
async def test_persistence_agent_storage_save_recover_exists_and_clear():
    kv_store = MockKVStore()
    storage = AgentStorage(kv_store)
    session = AgentSession(session_id="session-agent", config=Config())
    session.agent_id = Mock(return_value="agent-1")
    session.state().update({"name": "alice"})
    session.state().update_global({"shared": "value"})

    await storage.save(session)

    assert await storage.exists(session) is True

    recovered = AgentSession(session_id="session-agent", config=Config())
    recovered.agent_id = Mock(return_value="agent-1")
    await storage.recover(recovered)

    assert recovered.state().get("name") == "alice"
    assert recovered.state().get_global("shared") == "value"

    await storage.clear("agent-1", "session-agent")
    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_persistence_agent_group_storage_save_recover_exists_and_clear():
    kv_store = MockKVStore()
    storage = AgentTeamStorage(kv_store)
    session = AgentTeamSession(session_id="session-team", team_id="team-1", config=Config())
    session.state().update({"agent_local": "should_not_be_restored"})
    session.state().update_global({"team": "alpha"})

    await storage.save(session)

    assert await storage.exists(session) is True

    recovered = AgentTeamSession(session_id="session-team", team_id="team-1", config=Config())
    recovered.state = Mock(return_value=StateCollection())
    await storage.recover(recovered)

    assert recovered.state().get_global("team") == "alpha"
    assert recovered.state().get("agent_local") is None

    await storage.clear("team-1", "session-team")
    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_persistence_storage_recover_nonexistent_is_noop():
    kv_store = MockKVStore()
    storage = AgentStorage(kv_store)
    session = AgentSession(session_id="session-agent", config=Config())
    session.agent_id = Mock(return_value="agent-1")

    await storage.recover(session)

    assert session.state().get_state() == {"global_state": {}, "agent_state": {}}


@pytest.mark.asyncio
async def test_pre_workflow_interactive_resume_without_checkpoint_raises():
    """修复6：InteractiveInput 恢复但无任何 checkpoint → 显式抛错。

    此前 recover() 静默返回，图从 start 节点全新起跑并在首个节点以
    难以定位的校验错误崩溃（如 Message content=None）。
    """
    kv_store = MockKVStore()
    checkpointer = PersistenceCheckpointer(kv_store)
    parent = AgentSession(session_id="session-resume", config=Config())
    session = WorkflowSession(
        workflow_id="wf-1", parent=parent, session_id="session-resume"
    )

    with pytest.raises(Exception, match="no workflow checkpoint"):
        await checkpointer.pre_workflow_execute(session, InteractiveInput())


@pytest.mark.asyncio
async def test_pre_workflow_interactive_resume_with_unresumable_graph_raises():
    """修复6：workflow state 存在但 graph checkpoint 缺失/空 → 显式抛错。"""
    kv_store = MockKVStore()
    checkpointer = PersistenceCheckpointer(kv_store)
    parent = AgentSession(session_id="session-resume2", config=Config())
    session = WorkflowSession(
        workflow_id="wf-2", parent=parent, session_id="session-resume2"
    )
    session.state().update({"k": "v"})
    await checkpointer._workflow_storage.save(session)

    with pytest.raises(Exception, match="graph checkpoint"):
        await checkpointer.pre_workflow_execute(session, InteractiveInput())


@pytest.mark.asyncio
async def test_pre_workflow_interactive_resume_with_resumable_graph_recovers():
    """正例：io 侧与图侧快照齐备且图可续跑 → recover 正常执行不抛错。"""
    kv_store = MockKVStore()
    checkpointer = PersistenceCheckpointer(kv_store)
    parent = AgentSession(session_id="session-resume3", config=Config())
    session = WorkflowSession(
        workflow_id="wf-3", parent=parent, session_id="session-resume3"
    )
    session.state().update({"k": "v"})
    # update 仅暂存到 _updates，save 读取的是已提交的 _state，
    # 必须先 commit 才会随快照落盘。
    session.state().commit()
    await checkpointer._workflow_storage.save(session)
    await checkpointer._graph_state.save(
        "session-resume3",
        "wf-3",
        create_state("wf-3", 3, {"chan": "value"}),
    )

    await checkpointer.pre_workflow_execute(session, InteractiveInput())

    assert session.state().get("k") == "v"
