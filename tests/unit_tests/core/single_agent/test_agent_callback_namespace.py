# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""rail 回调事件命名空间(per-instance)测试.

背景: AgentCallbackManager 的事件名此前为 f"{agent_id}_{event}",
agent_id 即 card.id —— 同一 bot 的所有会话共享事件键。每个新会话实例
注册的 rail 回调全部堆叠在同一批键上且从不清理, trigger() 遍历并 await
全部历史回调, 每轮成本 O(累计实例数): 上层应用(jiuwenclaw)长稳测试中
"每轮新建会话"场景的时延随时间线性增长(1.2s -> 7.9s)。

期望: BaseAgent 为每个实例生成独立 event_namespace, 事件名改为
f"{event_namespace}_{event}", 实例之间互不叠加; 显式传入 namespace 时
以传入值为准(向后兼容 card.id 缺省)。
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest_asyncio.fixture(autouse=True)
async def cleanup_callbacks():
    """清理测试注册到全局框架上的事件, 避免跨用例污染."""
    yield
    framework = Runner.callback_framework
    for event in list(framework.callbacks.keys()):
        await framework.unregister_event(event)


class _StubAgent(BaseAgent):
    """最小可实例化 BaseAgent(仅用到回调管理器)."""

    def configure(self, config) -> "_StubAgent":  # noqa: ANN001
        return self

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        yield None


class _ProbeRail(AgentRail):
    """记录触发次数的最小 rail."""

    def __init__(self) -> None:
        self.fired = 0

    async def before_invoke(self, ctx) -> None:  # noqa: ANN001
        self.fired += 1


def _callbacks_of(event_key: str) -> int:
    return len(Runner.callback_framework.list_callbacks(event_key))


@pytest.mark.asyncio
async def test_manager_namespace_defaults_to_agent_id():
    manager = AgentCallbackManager("card_shared")
    assert manager.event_namespace == "card_shared"


@pytest.mark.asyncio
async def test_manager_accepts_explicit_namespace():
    manager = AgentCallbackManager("card_shared", event_namespace="ns_explicit")
    assert manager.event_namespace == "ns_explicit"


@pytest.mark.asyncio
async def test_agent_instances_get_distinct_namespaces():
    card = AgentCard(id="card_shared")
    agent_session_1 = _StubAgent(card)
    agent_session_2 = _StubAgent(card)

    ns1 = agent_session_1.agent_callback_manager.event_namespace
    ns2 = agent_session_2.agent_callback_manager.event_namespace

    assert ns1 != ns2
    assert ns1 != "card_shared" and ns2 != "card_shared"


@pytest.mark.asyncio
async def test_rails_do_not_accumulate_on_shared_key():
    """回归: 同 card 两个实例先后注册 rail, 不得叠在同一个事件键上."""
    event_key_suffix = f"{AgentCallbackEvent.BEFORE_INVOKE}"
    card = AgentCard(id="card_shared")
    agent_session_1 = _StubAgent(card)
    agent_session_2 = _StubAgent(card)
    ns1 = agent_session_1.agent_callback_manager.event_namespace
    ns2 = agent_session_2.agent_callback_manager.event_namespace

    await agent_session_1.agent_callback_manager.register_rail(_ProbeRail(), agent_session_1)
    assert _callbacks_of(f"{ns1}_{event_key_suffix}") == 1

    await agent_session_2.agent_callback_manager.register_rail(_ProbeRail(), agent_session_2)
    assert _callbacks_of(f"{ns2}_{event_key_suffix}") == 1
    # 实例 1 的键不受影响, 旧的 card 级共享键上不应有任何回调
    assert _callbacks_of(f"{ns1}_{event_key_suffix}") == 1
    assert _callbacks_of(f"card_shared_{event_key_suffix}") == 0


@pytest.mark.asyncio
async def test_execute_fires_only_own_namespace_callbacks():
    """execute 只触发本实例命名空间下的回调, 不波及其他实例."""
    card = AgentCard(id="card_shared")
    agent_session_1 = _StubAgent(card)
    agent_session_2 = _StubAgent(card)

    rail_1 = _ProbeRail()
    rail_2 = _ProbeRail()
    await agent_session_1.agent_callback_manager.register_rail(rail_1, agent_session_1)
    await agent_session_2.agent_callback_manager.register_rail(rail_2, agent_session_2)

    await agent_session_1.agent_callback_manager.execute(
        AgentCallbackEvent.BEFORE_INVOKE, None
    )

    assert rail_1.fired == 1
    assert rail_2.fired == 0
