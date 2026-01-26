#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright c) Huawei Technologies Co. Ltd. 2025-2025

import asyncio

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.pregel import Interrupt, GraphInterrupt
from openjiuwen.core.graph.pregel.base import PregelNode
from openjiuwen.core.graph.pregel import PregelConfig
from openjiuwen.core.graph.pregel.constants import PARENT_NS, NS
from openjiuwen.core.graph.pregel.router import StaticRouter
from openjiuwen.core.graph.pregel.task import TaskExecutorPool


async def task_a_slow(config):
    # Slow task, needs 1 second
    logger.debug(">>> Node A start")
    await asyncio.sleep(1)
    logger.debug(">>> Node A end")
    assert config[NS] == 'root:A:1'


async def task_b_interrupt(config):
    logger.debug(">>> Node b_interrupt start")
    await asyncio.sleep(0.2)
    assert config[NS] == 'root:B:1'
    logger.debug(">>> Node b_interrupt end")
    raise GraphInterrupt(Interrupt("B_Interrupt"))


async def task_b_value_error(config):
    logger.debug(">>> Node B value_error start")
    await asyncio.sleep(0.2)
    assert config[NS] == 'root:B:1'
    logger.debug(">>> Node B value_error end")
    raise ValueError("Simulated Runtime Error in B")


async def task_c_fast(config):
    # Fast task
    logger.debug(">>> Node C start")
    assert config[NS] == 'root:C:1'
    logger.debug(">>> Node C end")
    return


class TestTaskExecutorPool:

    @pytest.mark.asyncio
    async def test_pool_runtime_exception(self):
        """
        Case 1: Runtime exception (ValueError) triggers FIRST_EXCEPTION semantics.
        Expected: B fails, A is cancelled, C succeeds.
        """
        # Node definition
        root_config: PregelConfig = {'ns': 'root', 'session_id': 'test_conv_1'}
        # Simulate ainvoke initialization of PARENT_NS
        root_config[PARENT_NS] = root_config[NS]

        router_c = StaticRouter(['Target_C'])
        router_a = StaticRouter(['Target_A'])
        router_b = StaticRouter(['Target_B'])

        node_a = PregelNode(name='A', func=task_a_slow, routers=[router_a])
        node_b = PregelNode(name='B', func=task_b_value_error, routers=[router_b])
        node_c = PregelNode(name='C', func=task_c_fast, routers=[router_c])

        # Execution
        pool = TaskExecutorPool(root_config)
        pool.submit(node_a, 1)
        pool.submit(node_b, 1)
        pool.submit(node_c, 1)

        # Verify B's runtime exception is propagated
        with pytest.raises(ValueError, match="Simulated Runtime Error in B"):
            await pool.wait_all()

        # Verify results
        # 1. B fails, recorded as __error__
        assert 'B' in pool.failed
        assert pool.failed['B'].status == '__error__'
        assert isinstance(pool.failed['B'].exception[0], ValueError)

        # 2. A is cancelled, recorded as __error__
        assert 'A' in pool.failed
        assert pool.failed['A'].status == '__error__'
        assert isinstance(pool.failed['A'].exception[0], asyncio.CancelledError)

        # 3. C succeeds, message is collected
        assert 'C' not in pool.failed
        assert len(pool.succeed_messages) == 1
        assert pool.succeed_messages[0].sender == 'C'
        assert pool.succeed_messages[0].target == 'Target_C'

    @pytest.mark.asyncio
    async def test_pool_interrupt_exception(self):
        """
        Case 2: Interrupt exception (GraphInterrupt) triggers FIRST_EXCEPTION semantics.
        Expected: B interrupts, A is cancelled, C succeeds.
        """
        # Node definition
        root_config: PregelConfig = {'ns': 'root', 'session_id': 'test_conv_2'}
        # Simulate ainvoke initialization of PARENT_NS
        root_config[PARENT_NS] = root_config[NS]

        router_c = StaticRouter(['Target_C'])
        router_a = StaticRouter(['Target_A'])
        router_b = StaticRouter(['Target_B'])
        node_a = PregelNode(name='A', func=task_a_slow, routers=[router_a])
        node_b = PregelNode(name='B', func=task_b_interrupt, routers=[router_b])
        node_c = PregelNode(name='C', func=task_c_fast, routers=[router_c])

        # Execution
        pool = TaskExecutorPool(root_config)
        pool.submit(node_a, 1)
        pool.submit(node_b, 1)
        pool.submit(node_c, 1)

        # Verify GraphInterrupt is propagated
        with pytest.raises(GraphInterrupt):
            await pool.wait_all()

        # Verify results
        # 1. B interrupts, recorded as __interrupt__
        assert 'B' in pool.failed
        assert pool.failed['B'].status == '__interrupt__'
        assert isinstance(pool.failed['B'].exception[0], GraphInterrupt)

        # 3. C succeeds, message is collected
        assert 'C' not in pool.failed
        assert 'A' not in pool.failed
        assert len(pool.succeed_messages) == 2
        senders = [msg.sender for msg in pool.succeed_messages]
        assert set(senders) == {"A", "C"}