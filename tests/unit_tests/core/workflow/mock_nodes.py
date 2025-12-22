import asyncio
from typing import Any, AsyncIterator

from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.session import Session, is_ref_path, extract_origin_key
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import ComponentComposable, ComponentExecutable, WorkflowComponent


class MockNodeBase(WorkflowComponent):
    def __init__(self, node_id: str = ''):
        super().__init__()
        self.node_id = node_id


class MockStartNode(Start):
    def __init__(self, node_id: str):
        super().__init__({})

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs


class MockEndNode(End):
    def __init__(self, node_id: str):
        super().__init__({"responseTemplate": "hello:{{end_input}}"})
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs


class Node1(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs


class CountNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.times = 0

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self.times += 1
        result = {"count": self.times}
        logger.info(self.node_id + ": results = " + str(result))
        return result


class SlowNode(MockNodeBase):
    def __init__(self, node_id: str, wait: int):
        super().__init__(node_id)
        self._wait = wait

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        await asyncio.sleep(self._wait)
        return inputs


class StreamNode(MockNodeBase):
    def __init__(self, node_id: str, datas: list[dict]):
        super().__init__(node_id)
        self._node_id = node_id
        self._datas: list[dict] = datas

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        for data in self._datas:
            await asyncio.sleep(0.1)
            logger.info(f"StreamNode[{self._node_id}], stream frame: {data}")
            await session.write_custom_stream(data)
        logger.info(f"StreamNode[{self._node_id}], batch output: {inputs}")
        return inputs


class StreamNodeWithSubWorkflow(MockNodeBase):
    def __init__(self, node_id: str, sub_workflow: Workflow):
        super().__init__(node_id)
        self._node_id = node_id
        self._sub_workflow = sub_workflow

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        async for chunk in self._sub_workflow.stream({"a": 1, "b": "haha"}, session):
            logger.info(f"StreamNodeWithSubWorkflow[{self._node_id}], stream frame: {chunk}")
            await session.write_custom_stream(chunk)
        logger.info(f"StreamNodeWithSubWorkflow[{self._node_id}], batch output: {inputs}")
        return inputs


class MockStartNode4Cp(Start):
    def __init__(self, node_id: str):
        super().__init__({})
        self.runtime = 0

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self.runtime += 1
        value = session.get_global_state("a")
        if value is not None:
            raise Exception("value is not None")
        print("start: output = " + str(inputs))
        session.update_global_state({"a": 10})
        return inputs


class Node4Cp(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.runtime = 0

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self.runtime += 1
        value = session.get_global_state("a")
        if value < 20:
            raise Exception("value < 20")
        return inputs


class AddTenNode4Cp(WorkflowComponent):
    raise_exception = True

    def __init__(self, node_id: str):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self.raise_exception:
            self.raise_exception = False
            raise Exception("inner error: " + str(inputs["source"]))
        self.raise_exception = True
        return {"result": inputs["source"] + 10}


class InteractiveNode4Cp(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result1 = await session.interact("Please enter any key")
        print(result1)
        result = await session.interact("Please enter any key")
        return result


class InteractiveNode4StreamCp(MockNodeBase):
    def __init__(self, node_id):
        super().__init__(node_id)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await session.interact("Please enter any key")
        await session.write_stream(OutputSchema(type="output", index=0, payload=(self.node_id, result)))
        return result


class InteractiveNode4Collect(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await session.interact("Please enter any key")
        print(result)
        return result


class StreamCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        logger.debug(f"===StreamCompNode[{self._node_id}], input: {inputs}")
        if inputs is None:
            yield 1
        else:
            for i in range(1, 3):
                yield {"value": i * inputs["value"]}


class CollectCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        logger.info(f"===CollectCompNode[{self._node_id}], input stream started")
        result = 0
        input_generator = inputs.get("value")
        try:
            async for value in input_generator:
                try:
                    if value is None:
                        logger.warning(f"===CollectCompNode[{self._node_id}], missing 'value' in input: {value}")
                        continue
                    result += value
                    logger.info(f"===CollectCompNode[{self._node_id}], processed input: {value}")
                except Exception as e:
                    logger.error(f"===CollectCompNode[{self._node_id}], error processing input: {value}, error: {e}")
                    continue  # 可选：继续处理下一个输入
            return {"value": result}
        except Exception as e:
            logger.error(f"===CollectCompNode[{self._node_id}], critical error in collect: {e}")
            raise  # 重新抛出关键异常，如流中断


class TransformCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[
        Output]:
        logger.debug(f"===TransformCompNode[{self._node_id}], input stream started")
        input_generator = inputs.get("value")
        try:
            async for value in input_generator:
                try:
                    logger.debug(f"===TransformCompNode[{self._node_id}], processed input: {value}")
                    yield {"value": value}
                except Exception as e:
                    logger.error(f"===TransformCompNode[{self._node_id}], error processing input: {value}, error: {e}")
                    # 可选：继续处理下一个输入，或重新抛出异常以终止流
                    continue
        except Exception as e:
            logger.error(f"===TransformCompNode[{self._node_id}], critical error in transform: {e}")
            raise  # 重新抛出关键异常（如流中断）


class MultiCollectCompNode(MockNodeBase):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._node_id = node_id

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        logger.info(f"===CollectCompNode[{self._node_id}], input: {inputs}")
        a_collect = 0
        b_collect = 0
        input_generator = inputs.get("value")
        try:
            async for value in input_generator:
                logger.info(f"===CollectCompNode[{self._node_id}], input: {value}")
                a_value = value.get("a")
                if a_value is not None:
                    a_collect += a_value

                b_value = value.get("b")
                if b_value is not None:
                    b_collect += b_value
        except Exception as e:
            logger.error(f"Error during collection: {e}")
            raise
            # result = result + input["value"]
        result = {"a_collect": a_collect, "b_collect": b_collect}
        logger.info(f"===CollectCompNode243 [{self._node_id}], output: {result}")
        return result


class CommonNode(WorkflowComponent):

    def __init__(self, node_id: str):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, session, context)


class AddTenNode(WorkflowComponent):

    def __init__(self, node_id: str, check_map: dict = None):
        super().__init__()
        self.node_id = node_id
        self.check_map = check_map

    @staticmethod
    def generate_value(session: Session, value: Any):
        if isinstance(value, str) and is_ref_path(value):
            ref_str = extract_origin_key(value)
            return session.get_global_state(ref_str)
        return value

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self.check_map:
            for key, value in self.check_map.items():
                assert inputs.get(key) == self.generate_value(session, value)
        return {"result": inputs["source"] + 10}


class MockStreamNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs, session: Session, context: ModelContext = None):
        return inputs

    async def stream(
            self,
            inputs,
            session: Session,
            context: ModelContext = None
    ):
        yield inputs


class ComputeComponent2(ComponentComposable):
    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> Executable:
        return ComputeExecutor2()


class ComputeExecutor2(ComponentExecutable):
    def __init__(self):
        super().__init__()

    @staticmethod
    async def _iter_collect_field(iterator: AsyncIterator, data_source_key, data_key, step=1):
        result = 0
        async for data in iterator:
            print(f"collect step: {step}, {data_source_key}: {data_key} = {data}")
            if data_key == "result":
                result += int(data)
        return result

    @staticmethod
    async def _iter_transform_field(iterator: AsyncIterator, data_source_key, data_key="", step=1):
        results = []
        async for data in iterator:
            if not data_key:
                print(f"transform step: {step}, {data_source_key}: {data}")
                results.append({data_source_key: data})
            else:
                print(f"transform step: {step}, {data_source_key}: {data_key} = {data}")
                results.append({data_key: data})
        return results

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        exec_id = session.executable_id()
        a = int(inputs.get("a"))
        b = int(inputs.get("b"))
        return {"result": a + b}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        exec_id = session.executable_id()
        logger.info(f"{exec_id} start")

        inputs_a = inputs.get("a")
        if isinstance(inputs_a, list):
            await asyncio.sleep(0.1)
            yield {'b': inputs.get("b")}
            await asyncio.sleep(0.1)
            yield {'op': '+'}
            for a in inputs_a:
                yield {'a': a}
                await asyncio.sleep(0.1)
                yield {'result': int(a) + int(inputs.get("b"))}
        else:
            await asyncio.sleep(0.1)
            yield {'a': inputs_a}
            await asyncio.sleep(0.1)
            yield {'op': '+'}
            await asyncio.sleep(0.1)
            yield {'b': inputs.get("b")}
            yield {'result': int(inputs_a) + int(inputs.get("b"))}
        logger.info(f"{exec_id} stream done")

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        exec_id = session.executable_id()
        step = 1
        tasks = []
        for data_source_key, obj in inputs.items():
            if isinstance(obj, dict):
                for data_key, iterator in obj.items():
                    tasks.append(self._iter_collect_field(iterator, data_source_key, data_key, step))
                    step += 1
            else:
                tasks.append(self._iter_collect_field(obj, data_source_key, "result", step))
                step += 1
        results = await asyncio.gather(*tasks)
        result = sum(results)
        return {'result_collect': result}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        exec_id = session.executable_id()
        step = 1
        tasks = []
        for data_source_key, obj in inputs.items():
            if isinstance(obj, dict):
                for data_key, iterator in obj.items():
                    tasks.append(self._iter_transform_field(iterator, data_source_key, data_key, step))
                    step += 1
            else:
                tasks.append(self._iter_transform_field(obj, data_source_key, "", step))
                step += 1
        for coro in asyncio.as_completed(tasks):
            result = await coro
            for item in result:
                yield item
        print(f"{exec_id} transform done")


class DualAbilityWithErrorComponent(ComponentComposable):
    """
    A component with dual stream abilities (TRANSFORM + STREAM) that can be configured
    to raise exceptions in specific abilities for testing error handling.
    """
    def __init__(self, error_in_stream: bool = False, error_in_transform: bool = False):
        super().__init__()
        self._error_in_stream = error_in_stream
        self._error_in_transform = error_in_transform

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> Executable:
        return DualAbilityWithErrorExecutor(
            error_in_stream=self._error_in_stream,
            error_in_transform=self._error_in_transform
        )


class DualAbilityWithErrorExecutor(ComponentExecutable):
    """Executor that can raise exceptions in specific abilities."""
    def __init__(self, error_in_stream: bool = False, error_in_transform: bool = False):
        super().__init__()
        self._error_in_stream = error_in_stream
        self._error_in_transform = error_in_transform

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        a = int(inputs.get("a", 0))
        b = int(inputs.get("b", 0))
        return {"result": a + b}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        if self._error_in_stream:
            raise RuntimeError("Simulated error in STREAM ability")
        a = inputs.get("a", 0)
        b = inputs.get("b", 0)
        yield {'a': a}
        yield {'op': '+'}
        yield {'b': b}
        yield {'result': int(a) + int(b)}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        if self._error_in_transform:
            raise RuntimeError("Simulated error in TRANSFORM ability")
        for data_source_key, obj in inputs.items():
            if isinstance(obj, dict):
                for data_key, iterator in obj.items():
                    async for data in iterator:
                        yield {data_key: data}
            else:
                async for data in obj:
                    yield {data_source_key: data}
