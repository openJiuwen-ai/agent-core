#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
HierarchicalGroup Runner 测试

场景：
- 测试通过 Runner.run_agent_group 运行 HierarchicalGroup
- 测试通过 Runner.run_agent_group_streaming 运行 HierarchicalGroup
- 测试通过 Runner.add_agent_group 注册并按 ID 运行
"""

import os

from openjiuwen.core.multi_agent.legacy import GroupCard

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["RESTFUL_SSL_VERIFY"] = "false"

import unittest
from typing import Any, Dict, AsyncIterator

from openjiuwen.core.single_agent import AgentConfig, BaseAgent, ControllerAgent
from examples.groups.hierarchical_group import (
    HierarchicalGroup,
    HierarchicalGroupConfig
)
from examples.groups.hierarchical_group.agents.main_controller import (
    HierarchicalMainController
)
from openjiuwen.core.controller import Event
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream.base import OutputSchema


class SimpleEchoAgent(BaseAgent):
    """简单回显 Agent - 用于测试

    收到消息后返回带有 agent_id 标记的响应
    """

    def __init__(self, agent_config: AgentConfig):
        super().__init__(agent_config)
        self.received_messages = []
        self._stream_index = 0

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        """同步调用 - 回显消息"""
        content = inputs.get("content") or inputs.get("query", "")
        self.received_messages.append(content)

        return {
            "output": f"[{self.agent_config.id}] 收到: {content}",
            "agent_id": self.agent_config.id,
            "result_type": "answer"
        }

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        """流式调用 - 回显消息"""
        result = await self.invoke(inputs, session)

        if session:
            self._stream_index += 1
            output = OutputSchema(
                type="echo_response",
                index=self._stream_index,
                payload=result
            )
            await session.write_stream(output)

        yield result


class TestHierarchicalGroupRunner(unittest.IsolatedAsyncioTestCase):
    """Runner 运行 HierarchicalGroup 测试"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    def _create_echo_agent(self, agent_id: str, description: str) -> SimpleEchoAgent:
        """创建简单的回显 Agent"""
        config = AgentConfig(
            id=agent_id,
            description=description
        )
        return SimpleEchoAgent(config)

    def _create_hierarchical_group(
            self,
            group_id: str,
            leader_id: str = "leader"
    ) -> HierarchicalGroup:
        """创建 HierarchicalGroup 及其 Agents"""
        config = HierarchicalGroupConfig(
            group_id=group_id,
            leader_agent_id=leader_id
        )
        group = HierarchicalGroup(config)

        leader_config = AgentConfig(id=leader_id, description="Leader Agent")
        leader = ControllerAgent(
            leader_config, controller=HierarchicalMainController()
        )

        worker_a = self._create_echo_agent("worker_a", "Worker A")
        worker_b = self._create_echo_agent("worker_b", "Worker B")

        group.add_agent(leader_id, leader)
        group.add_agent("worker_a", worker_a)
        group.add_agent("worker_b", worker_b)

        return group

    @unittest.skip("skip system test")
    async def test_run_agent_group_with_instance(self):
        """测试 Runner.run_agent_group 直接传入 Group 实例"""
        print("\n=== 测试 Runner.run_agent_group (传入实例) ===")

        group = self._create_hierarchical_group("runner_test_instance")

        group.group_controller.subscribe("notification", ["worker_a"])

        message = Event.create_user_event(
            content="通过 Runner 发送的消息",
            conversation_id="runner_instance_001"
        )
        message.message_type = "notification"

        result = await Runner.run_agent_group(group, message)
        print(f"结果: {result}")

        self.assertIsInstance(result, dict)
        self.assertIn("worker_a", result.get("output", ""))
        print("✅ Runner.run_agent_group (传入实例) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_with_id(self):
        """测试 Runner.run_agent_group 通过 ID 运行已注册的 Group"""
        print("\n=== 测试 Runner.run_agent_group (通过 ID) ===")

        group = self._create_hierarchical_group("runner_test_by_id")

        group.group_controller.subscribe("alert", ["worker_b"])

        await Runner.resource_mgr.add_agent_group(GroupCard(id="runner_test_by_id"), group)

        message = Event.create_user_event(
            content="通过 Group ID 发送的消息",
            conversation_id="runner_by_id_001"
        )
        message.message_type = "alert"

        result = await Runner.run_agent_group("runner_test_by_id", message)
        print(f"结果: {result}")

        self.assertIsInstance(result, dict)
        self.assertIn("worker_b", result.get("output", ""))

        await Runner.resource_mgr.remove_agent_group("runner_test_by_id")
        print("✅ Runner.run_agent_group (通过 ID) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_broadcast(self):
        """测试 Runner.run_agent_group 广播到多个 Agent"""
        print("\n=== 测试 Runner.run_agent_group 广播 ===")

        group = self._create_hierarchical_group("runner_test_broadcast")

        group.group_controller.subscribe(
            "broadcast_msg", ["worker_a", "worker_b"]
        )

        message = Event.create_user_event(
            content="广播消息",
            conversation_id="runner_broadcast_001"
        )
        message.message_type = "broadcast_msg"

        result = await Runner.run_agent_group(group, message)
        print(f"结果类型: {type(result)}")
        print(f"结果: {result}")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        print("✅ Runner.run_agent_group 广播测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_streaming_with_instance(self):
        """测试 Runner.run_agent_group_streaming 直接传入 Group 实例"""
        print("\n=== 测试 Runner.run_agent_group_streaming (传入实例) ===")

        group = self._create_hierarchical_group("runner_stream_instance")

        group.group_controller.subscribe("stream_event", ["worker_a"])

        message = Event.create_user_event(
            content="流式消息",
            conversation_id="runner_stream_001"
        )
        message.message_type = "stream_event"

        chunks = []
        stream = Runner.run_agent_group_streaming(group, message)
        async for chunk in stream:
            chunks.append(chunk)
            chunk_type = chunk.type if hasattr(chunk, 'type') else type(chunk)
            print(f"  收到 chunk: {chunk_type}")

        print(f"总共收到 {len(chunks)} 个 chunks")
        self.assertTrue(len(chunks) > 0, "应该收到流式输出")
        print("✅ Runner.run_agent_group_streaming (传入实例) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_streaming_with_id(self):
        """测试 Runner.run_agent_group_streaming 通过 ID 运行"""
        print("\n=== 测试 Runner.run_agent_group_streaming (通过 ID) ===")

        group = self._create_hierarchical_group("runner_stream_by_id")

        group.group_controller.subscribe("stream_data", ["worker_b"])

        await Runner.resource_mgr.add_agent_group(GroupCard(id="runner_stream_by_id"), group)

        message = Event.create_user_event(
            content="通过 ID 发送流式消息",
            conversation_id="runner_stream_id_001"
        )
        message.message_type = "stream_data"

        chunks = []
        stream = Runner.run_agent_group_streaming(
            "runner_stream_by_id", message
        )
        async for chunk in stream:
            chunks.append(chunk)
            chunk_type = chunk.type if hasattr(chunk, 'type') else type(chunk)
            print(f"  收到 chunk: {chunk_type}")

        print(f"总共收到 {len(chunks)} 个 chunks")
        self.assertTrue(len(chunks) > 0, "应该收到流式输出")

        await Runner.resource_mgr.remove_agent_group("runner_stream_by_id")
        print("✅ Runner.run_agent_group_streaming (通过 ID) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_streaming_broadcast(self):
        """测试 Runner.run_agent_group_streaming 广播流式输出"""
        print("\n=== 测试 Runner.run_agent_group_streaming 广播 ===")

        group = self._create_hierarchical_group("runner_stream_broadcast")

        group.group_controller.subscribe(
            "stream_broadcast", ["worker_a", "worker_b"]
        )

        message = Event.create_user_event(
            content="广播流式消息",
            conversation_id="runner_stream_broadcast_001"
        )
        message.message_type = "stream_broadcast"

        chunks = []
        stream = Runner.run_agent_group_streaming(group, message)
        async for chunk in stream:
            chunks.append(chunk)
            chunk_type = chunk.type if hasattr(chunk, 'type') else type(chunk)
            print(f"  收到 chunk: {chunk_type}")

        print(f"总共收到 {len(chunks)} 个 chunks")
        self.assertTrue(len(chunks) > 0, "应该收到流式输出")

        worker_a = group.agents["worker_a"]
        worker_b = group.agents["worker_b"]
        self.assertEqual(len(worker_a.received_messages), 1)
        self.assertEqual(len(worker_b.received_messages), 1)
        print("✅ Runner.run_agent_group_streaming 广播测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_with_receiver_id(self):
        """测试 Runner.run_agent_group 通过 receiver_id 指定目标"""
        print("\n=== 测试 Runner.run_agent_group (receiver_id 路由) ===")

        group = self._create_hierarchical_group("runner_receiver_test")

        message = Event.create_user_event(
            content="直接发送给 worker_a",
            conversation_id="runner_receiver_001"
        )
        message.receiver_id = "worker_a"

        result = await Runner.run_agent_group(group, message)
        print(f"结果: {result}")

        self.assertIsInstance(result, dict)
        self.assertIn("worker_a", result.get("output", ""))

        worker_a = group.agents["worker_a"]
        worker_b = group.agents["worker_b"]
        self.assertEqual(len(worker_a.received_messages), 1)
        self.assertEqual(len(worker_b.received_messages), 0)
        print("✅ Runner.run_agent_group (receiver_id 路由) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_fallback_to_leader(self):
        """测试 Runner.run_agent_group 无订阅者时回退到 Leader"""
        print("\n=== 测试 Runner.run_agent_group (回退到 Leader) ===")

        config = HierarchicalGroupConfig(
            group_id="runner_fallback_test",
            leader_agent_id="leader"
        )
        group = HierarchicalGroup(config)

        leader = self._create_echo_agent("leader", "Leader Agent")
        worker = self._create_echo_agent("worker", "Worker Agent")

        group.add_agent("leader", leader)
        group.add_agent("worker", worker)

        message = Event.create_user_event(
            content="未知类型消息",
            conversation_id="runner_fallback_001"
        )
        message.message_type = "unknown_type"

        result = await Runner.run_agent_group(group, message)
        print(f"结果: {result}")

        self.assertEqual(len(leader.received_messages), 1)
        self.assertEqual(len(worker.received_messages), 0)
        print("✅ Runner.run_agent_group (回退到 Leader) 测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_multiple_message_types(self):
        """测试 Runner.run_agent_group 多种消息类型路由"""
        print("\n=== 测试 Runner.run_agent_group 多消息类型 ===")

        group = self._create_hierarchical_group("runner_multi_type")

        group.group_controller.subscribe("type_a", ["worker_a"])
        group.group_controller.subscribe("type_b", ["worker_b"])

        msg_a = Event.create_user_event(
            content="类型A消息",
            conversation_id="runner_multi_001"
        )
        msg_a.message_type = "type_a"

        result_a = await Runner.run_agent_group(group, msg_a)
        print(f"类型A结果: {result_a}")
        self.assertIn("worker_a", result_a.get("output", ""))

        msg_b = Event.create_user_event(
            content="类型B消息",
            conversation_id="runner_multi_002"
        )
        msg_b.message_type = "type_b"

        result_b = await Runner.run_agent_group(group, msg_b)
        print(f"类型B结果: {result_b}")
        self.assertIn("worker_b", result_b.get("output", ""))

        worker_a = group.agents["worker_a"]
        worker_b = group.agents["worker_b"]
        self.assertEqual(len(worker_a.received_messages), 1)
        self.assertEqual(len(worker_b.received_messages), 1)
        print("✅ Runner.run_agent_group 多消息类型测试通过")

    @unittest.skip("skip system test")
    async def test_run_agent_group_streaming_sequential(self):
        """测试 Runner.run_agent_group_streaming 连续多次调用"""
        print("\n=== 测试 Runner.run_agent_group_streaming 连续调用 ===")

        group = self._create_hierarchical_group("runner_stream_seq")

        group.group_controller.subscribe("seq_event", ["worker_a"])

        for i in range(3):
            message = Event.create_user_event(
                content=f"第 {i+1} 条消息",
                conversation_id=f"runner_stream_seq_{i}"
            )
            message.message_type = "seq_event"

            chunks = []
            stream = Runner.run_agent_group_streaming(group, message)
            async for chunk in stream:
                chunks.append(chunk)

            print(f"  第 {i+1} 次调用收到 {len(chunks)} 个 chunks")
            self.assertTrue(len(chunks) > 0)

        worker_a = group.agents["worker_a"]
        self.assertEqual(len(worker_a.received_messages), 3)
        print("✅ Runner.run_agent_group_streaming 连续调用测试通过")


if __name__ == "__main__":
    unittest.main()

