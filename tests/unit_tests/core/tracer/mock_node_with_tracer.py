import asyncio
import random

from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.runtime import Runtime
from tests.unit_tests.core.workflow.mock_nodes import MockNodeBase


class StreamNodeWithTracer(MockNodeBase):
    def __init__(self, node_id: str, datas: list[dict]):
        super().__init__(node_id)
        self._node_id = node_id
        self._datas: list[dict] = datas

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        try:
            await runtime.trace({"on_invoke_data": "mock with" + str(inputs)})

            # 运行时操作

        except Exception as e:
            await runtime.trace_error(e)
            raise e

        await asyncio.sleep(random.randint(0, 2))
        for data in self._datas:
            await asyncio.sleep(0.5)
            await runtime.write_custom_stream(data)
        print("StreamNode: output = " + str(inputs))
        return inputs

