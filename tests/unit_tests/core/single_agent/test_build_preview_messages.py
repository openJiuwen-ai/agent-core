# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证 _build_preview_messages 返回浅拷贝只读快照。

回归性能优化：preview 不再 deepcopy 整段消息历史，改为浅拷贝。
list 级修改不污染 context buffer；消息对象共享（非 deepcopy）。
对象属性级修改不在此防御，由 before_model_call 只读契约约束。
"""
import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent import AgentCard, ReActAgent


@pytest.fixture
def engine():
    return ContextEngine(ContextEngineConfig(default_window_message_num=5))


@pytest.fixture
def session():
    return create_agent_session("preview_snapshot", card=AgentCard(id="t"))


@pytest.fixture
def agent():
    return ReActAgent(AgentCard(id="react"))


class TestBuildPreviewMessages:
    """preview 是浅拷贝只读快照：list 级隔离、不深拷贝消息对象。"""

    @pytest.mark.asyncio
    async def test_preview_is_shallow_copy_not_deepcopy(self, engine, session, agent):
        context = await engine.create_context(context_id="ctx", session=session)
        msgs = [UserMessage(content="hello"), AssistantMessage(content="hi")]
        await context.add_messages(msgs)

        preview = agent._build_preview_messages(context)
        buffer = context.get_messages()

        # list 级隔离：preview 与 buffer 是不同 list 对象
        assert preview is not buffer
        # 浅拷贝：元素共享同一消息对象（证明非 deepcopy）
        assert preview[0] is buffer[0]
        assert preview[1] is buffer[1]

    @pytest.mark.asyncio
    async def test_list_mutation_does_not_pollute_buffer(self, engine, session, agent):
        context = await engine.create_context(context_id="ctx", session=session)
        await context.add_messages([UserMessage(content="hello")])

        preview = agent._build_preview_messages(context)
        preview.append(AssistantMessage(content="injected"))

        # buffer 不受 preview 的 list 级修改影响
        assert len(context.get_messages()) == 1
