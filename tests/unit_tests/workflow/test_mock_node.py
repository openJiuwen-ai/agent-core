#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import AsyncIterator

from jiuwen.core.common.logging import logger
from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.graph.executable import Input, Output
from jiuwen.core.context_engine.base import Context
from jiuwen.core.runtime.base import ComponentExecutable
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.stream.base import OutputSchema
from jiuwen.core.workflow.base import Workflow


class MockNodeBase(ComponentExecutable, WorkflowComponent):
    def __init__(self, node_id: str = ''):
        super().__init__()
        self.node_id = node_id


class MockStartNode(Start):
    def __init__(self, node_id: str):
        super().__init__({})

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return inputs


class MockEndNode(End):
    def __init__(self, node_id: str):
        super().__init__({"responseTemplate": "hello:{{end_input}}"})
        self.node_id = node_id

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return inputs


class Node1(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return inputs


class CountNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.times = 0

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self.times += 1
        result = {"count": self.times}
        return result


class SlowNode(MockNodeBase):
    def __init__(self, node_id: str, wait: int):
        super().__init__(node_id)
        self._wait = wait

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        await asyncio.sleep(self._wait)
        return inputs


class StreamNode(MockNodeBase):
    def __init__(self, node_id: str, datas: list[dict]):
        super().__init__(node_id)
        self._node_id = node_id
        self._datas: list[dict] = datas

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        for data in self._datas:
            await asyncio.sleep(0.1)
            await runtime.write_custom_stream(data)
        return inputs


class StreamNodeWithSubWorkflow(MockNodeBase):
    def __init__(self, node_id: str, sub_workflow: Workflow):
        super().__init__(node_id)
        self._node_id = node_id
        self._sub_workflow = sub_workflow

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        async for chunk in self._sub_workflow.stream({"a": 1, "b": "haha"}, runtime):
            await runtime.write_custom_stream(chunk)
        return inputs


class MockStartNode4Cp(Start):
    def __init__(self, node_id: str):
        super().__init__({})
        self.runtime = 0

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self.runtime += 1
        value = runtime.get_global_state("a")
        if value is not None:
            raise Exception("value is not None")
        runtime.update_global_state({"a": 10})
        return inputs


class Node4Cp(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.runtime = 0

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self.runtime += 1
        value = runtime.get_global_state("a")
        if value < 20:
            raise Exception("value < 20")
        return inputs


class AddTenNode4Cp(ComponentExecutable, WorkflowComponent):
    raise_exception = True

    def __init__(self, node_id: str):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        if self.raise_exception:
            self.raise_exception = False
            raise Exception("inner error")
        self.raise_exception = True
        return {"result": inputs["source"] + 10}


class InteractiveNode4Cp(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        await runtime.interact("Please enter any key")
        result = await runtime.interact("Please enter any key")
        return result


class InteractiveNode4StreamCp(MockNodeBase):
    def __init__(self, node_id):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        result = await runtime.interact("Please enter any key")
        await runtime.write_stream(OutputSchema(type="output", index=0, payload=(self.node_id, result)))
        return result


class StreamCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        if inputs is None:
            yield 1
        else:
            for i in range(1, 3):
                yield {"value": i * inputs["value"]}


class CollectCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def collect(self, inputs: AsyncIterator[Input], runtime: Runtime, context: Context) -> Output:
        logger.info(f"===CollectCompNode[{self._node_id}], input stream started")
        result = 0
        try:
            async for input in inputs:
                try:
                    value = input.get("value")
                    if value is None:
                        continue
                    result += value
                except Exception as e:
                    continue
            return {"value": result}
        except Exception as e:
            raise


class TransformCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def transform(self, inputs: AsyncIterator[Input], runtime: Runtime, context: Context) -> AsyncIterator[
        Output]:
        logger.debug(f"===TransformCompNode[{self._node_id}], input stream started")
        try:
            async for input in inputs:
                try:
                    value = input.get("value")
                    yield {"value": value}
                except Exception as e:
                    continue
        except Exception as e:
            raise


class MultiCollectCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def collect(self, inputs: AsyncIterator[Input], runtime: Runtime, context: Context) -> Output:
        a_collect = 0
        b_collect = 0
        try:
            async for input in inputs:
                a_value = input.get("value", {}).get("a")
                if a_value is not None:
                    a_collect += a_value

                b_value = input.get("value", {}).get("b")
                if b_value is not None:
                    b_collect += b_value
        except Exception as e:
            raise
        result = {"a_collect": a_collect, "b_collect": b_collect}
        return result
