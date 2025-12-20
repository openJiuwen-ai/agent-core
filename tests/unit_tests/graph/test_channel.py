#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright c) Huawei Technologies Co. Ltd. 2025-2025

from openjiuwen.graph.pregel.base import TriggerMessage, BarrierMessage
from openjiuwen.graph.pregel.channels import ChannelManager, TriggerChannel, BarrierChannel


class TestChannelManager:
    @classmethod
    def test_trigger_channel_reset(cls):
        """Test that TriggerChannel correctly resets Ready state after consume"""

        # Setup: Create Manager and Channels
        ch_start = TriggerChannel("start")
        ch_a = TriggerChannel("a")

        manager = ChannelManager([ch_start, ch_a])

        assert manager.get_ready_nodes() == []

        # Activate start
        manager.buffer_message(TriggerMessage(sender="__start__", target="start"))
        manager.flush()

        ready = manager.get_ready_nodes()
        assert "start" in ready
        assert ch_start.is_ready() is True

        # Consume start
        manager.consume("start")

        ready_after_consume = manager.get_ready_nodes()
        assert "start" not in ready_after_consume
        assert ch_start.is_ready() is False  # Key check: TriggerChannel must be empty

        # start produces message to a
        manager.buffer_message(TriggerMessage(sender="start", target="a"))
        # This flush should not activate start, only a
        manager.flush()

        ready_step1 = manager.get_ready_nodes()
        assert "a" in ready_step1
        assert "start" not in ready_step1  # If start is still here, it's an infinite loop

        # Consume a
        manager.consume("a")
        assert "a" not in manager.get_ready_nodes()

        # a produces message to a1 (no a1 channel in this example, simulate empty flush)
        manager.flush()

        ready_step2 = manager.get_ready_nodes()
        # Should have no ready nodes
        assert "a" not in ready_step2
        assert "start" not in ready_step2
        assert len(ready_step2) == 0

    @classmethod
    def test_barrier_lifecycle(cls):
        """
        Test complete lifecycle of BarrierChannel in ChannelManager:
        Waiting -> Partial arrival -> All arrived (Ready) -> Consumed (Reset) -> Waiting again
        """

        # Setup: Create a barrier waiting for "A" and "B", belonging to node "collect"
        barrier_target_node = "collect"
        expected_senders = {"A", "B"}

        barrier_ch = BarrierChannel(barrier_target_node, expected_senders)
        barrier_key = barrier_ch.key

        manager = ChannelManager([barrier_ch])

        # Initial state check
        assert manager.get_ready_nodes() == []
        assert barrier_ch.is_ready() is False

        # Partial arrival: Only "A" sends message
        msg_a = BarrierMessage(target=barrier_key, sender="A")
        manager.buffer_message(msg_a)
        manager.flush()

        # Check:
        # 1. Barrier received A
        assert "A" in barrier_ch.received
        # 2. But "B" hasn't arrived, so barrier should not be Ready
        assert barrier_ch.is_ready() is False
        # 3. Manager should not consider "collect" node Ready
        ready_nodes = manager.get_ready_nodes()
        assert barrier_target_node not in ready_nodes

        # Full arrival: "B" sends message
        msg_b = BarrierMessage(target=barrier_key, sender="B")
        manager.buffer_message(msg_b)
        manager.flush()

        # Check:
        # 1. Barrier received B
        assert "B" in barrier_ch.received
        # 2. Now complete, should be Ready
        assert barrier_ch.is_ready() is True
        # 3. Manager should consider "collect" Ready
        ready_nodes_full = manager.get_ready_nodes()
        assert barrier_target_node in ready_nodes_full

        # Consume: Execute node logic, consume channel
        manager.consume(barrier_target_node)

        # Check:
        # 1. Manager's Ready list should be empty
        assert barrier_target_node not in manager.get_ready_nodes()
        # 2. Barrier internal state should be cleared
        assert len(barrier_ch.received) == 0
        assert barrier_ch.is_ready() is False

        # Re-trigger: Send "A" again (simulate next cycle)
        manager.buffer_message(msg_a)
        manager.flush()

        # Check:
        # 1. State should be partially arrived, not Ready
        assert "A" in barrier_ch.received
        assert barrier_ch.is_ready() is False
        assert barrier_target_node not in manager.get_ready_nodes()

    @classmethod
    def test_barrier_duplicate_signals(cls):
        """Test that the same sender sending signals multiple times in one superstep (idempotency)"""
        barrier_ch = BarrierChannel("collect", {"A", "B"})
        manager = ChannelManager([barrier_ch])
        key = barrier_ch.key

        # A sends twice
        manager.buffer_message(BarrierMessage(sender="A", target=key))
        manager.buffer_message(BarrierMessage(sender="A", target=key))
        manager.flush()

        assert len(barrier_ch.received) == 1  # Set should deduplicate
        assert not barrier_ch.is_ready()
