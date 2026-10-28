# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Single-round rails must share the inner ReAct steering channel."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.deep_agent import DeepAgent


@pytest.mark.asyncio
async def test_single_round_forwards_queued_rail_reminder_to_inner_loop():
    class Inner:
        async def invoke(self, inputs, session):
            queue = inputs.get("_steering_queue")
            return {"output": queue.get_nowait() if queue is not None else "missing reminder"}

    agent = DeepAgent.__new__(DeepAgent)
    agent._react_agent = Inner()
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="solve"))
    ctx.bind_steering_queue(asyncio.Queue())
    ctx.push_steering("Finish the report before more refinement.")
    result = await agent._run_single_round_invoke(ctx, None)
    assert result == {"output": "Finish the report before more refinement."}


@pytest.mark.asyncio
async def test_single_round_creates_a_separate_steering_channel_per_invocation():
    class Inner:
        async def invoke(self, inputs, session):
            queue = inputs.get("_steering_queue")
            if queue is None:
                return {"output": "no channel"}
            queue.put_nowait(inputs["query"])
            await asyncio.sleep(0)
            return {"output": queue.get_nowait()}

    agent = DeepAgent.__new__(DeepAgent)
    agent._react_agent = Inner()
    contexts = [AgentCallbackContext(agent=agent, inputs=InvokeInputs(query=query)) for query in ("one", "two")]
    results = await asyncio.gather(*(agent._run_single_round_invoke(ctx, None) for ctx in contexts))
    assert results == [{"output": "one"}, {"output": "two"}]


@pytest.mark.asyncio
async def test_single_round_stream_forwards_the_same_rail_reminder():
    class Inner:
        async def stream(self, inputs, session, stream_modes):
            queue = inputs.get("_steering_queue")
            yield queue.get_nowait() if queue is not None else "missing reminder"

    agent = DeepAgent.__new__(DeepAgent)
    agent._react_agent = Inner()
    agent._deep_config = SimpleNamespace(stream_task_lifecycle=None)
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="solve"))
    ctx.bind_steering_queue(asyncio.Queue())
    ctx.push_steering("Finish the streamed report.")
    chunks = [item async for item in agent._run_single_round_stream(ctx, None, None)]
    assert chunks == ["Finish the streamed report."]


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['completed', 'failed', 'closed', 'cancelled'])
async def test_persistent_stream_forwards_rsi_controls_and_drains_on_every_exit(ending):
    observed = {}
    blocker = asyncio.Event()
    lifecycle = SimpleNamespace(cancel_and_wait=AsyncMock())

    class Inner:
        async def stream(self, inputs, session, stream_modes, **kwargs):
            observed['inputs'] = inputs
            observed['lifecycle'] = kwargs.get('_background_task_lifecycle')
            try:
                yield inputs['_steering_queue'].get_nowait()
                if ending == 'failed':
                    raise RuntimeError('controlled stream failure')
                if ending == 'cancelled':
                    await blocker.wait()
                yield 'finished'
            finally:
                observed['closed'] = True

    agent = DeepAgent.__new__(DeepAgent)
    agent._react_agent = Inner()
    agent._deep_config = SimpleNamespace(stream_task_lifecycle=lifecycle)
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query='solve'))
    queue = asyncio.Queue()
    ctx.bind_steering_queue(queue)
    ctx.push_steering('Finish the report.')
    stream = agent._run_single_round_stream(ctx, None, None, host_iteration_limit=5)
    assert await stream.__anext__() == 'Finish the report.'
    if ending == 'failed':
        with pytest.raises(RuntimeError, match='controlled stream failure'):
            await stream.__anext__()
    elif ending == 'closed':
        await stream.aclose()
    elif ending == 'cancelled':
        pending = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        assert [item async for item in stream] == ['finished']
    assert observed['inputs']['_steering_queue'] is queue
    assert observed['inputs']['_host_max_iterations'] == 5
    assert observed['lifecycle'] is lifecycle
    assert observed['closed']
    lifecycle.cancel_and_wait.assert_awaited_once_with()
