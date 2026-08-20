import asyncio

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.workflow.components import Session
from tests.unit_tests.core.workflow.mock_nodes import MockNodeBase

# Simulated node latency. What the tracer tests actually depend on is the
# suspension point — the node must yield to the loop before and between
# chunks so the concurrent stream consumer interleaves with it. The wall
# time itself is never asserted, so keep these as short as possible.
_STARTUP_DELAY = 0.01
_CHUNK_DELAY = 0.01


class StreamNodeWithTracer(MockNodeBase):
    def __init__(self, node_id: str, datas: list[dict]):
        super().__init__(node_id)
        self._node_id = node_id
        self._datas: list[dict] = datas

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        try:
            await session.trace({"on_invoke_data": "mock with" + str(inputs)})

            # 运行时操作

        except Exception as e:
            await session.trace_error(e)
            raise e

        await asyncio.sleep(_STARTUP_DELAY)
        for data in self._datas:
            await asyncio.sleep(_CHUNK_DELAY)
            await session.write_custom_stream(data)
        print("StreamNode: output = " + str(inputs))
        return inputs

