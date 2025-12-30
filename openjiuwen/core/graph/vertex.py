# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from asyncio import CancelledError
from typing import Any, Optional, AsyncIterator, Literal

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT, END_NODE_STREAM, INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.atomic_node import AsyncAtomicNode
from openjiuwen.core.graph.executable import Executable, Output
from openjiuwen.core.graph.graph_state import GraphState
from openjiuwen.core.runtime.constants import COMP_STREAM_CALL_TIMEOUT_KEY
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.utils import get_by_schema
from openjiuwen.core.runtime.workflow import NodeRuntime
from openjiuwen.core.stream.base import StreamSchemas, OutputSchema
from openjiuwen.core.stream.emitter import StreamEmitter
from openjiuwen.core.stream_actor.base import StreamConsumer
from openjiuwen.core.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.core.workflow.workflow_config import ComponentAbility
from openjiuwen.graph.pregel import GraphInterrupt

SUB_WORKFLOW_COMPONENT = "sub_workflow"


class Vertex(AsyncAtomicNode, StreamConsumer):
    def __init__(self, node_id: str, executable: Executable = None):
        self._node_id = node_id
        self._executable = executable
        self._runtime: NodeRuntime = None
        self._stream_called_timeout = 10
        # if stream_call is available, call should wait for it
        self._stream_done = asyncio.Future()
        self._call_count: int = 0
        self._stream_call_count: int = 0
        self.is_end_node = False
        self._is_started = asyncio.Event()
        self._is_call_started = asyncio.Event()
        self._node_config = None
        self._component_ability = None
        self._has_stream_call: bool = False
        self._source_id: list = []

    def init(self, runtime: BaseRuntime) -> bool:
        self._runtime = NodeRuntime(runtime, self._node_id, type(self._executable).__name__)
        self._stream_called_timeout = runtime.config().get_env(COMP_STREAM_CALL_TIMEOUT_KEY)
        self._node_config = self._runtime.node_config()
        self._component_ability = (
            self._node_config.abilities) if self._node_config and self._node_config.abilities else [
            ComponentAbility.INVOKE]
        self._has_stream_call = len(self._stream_abilities()) > 0
        self._has_call = len(self._component_ability) > len(self._stream_abilities())
        return True

    async def _run_executable(self, ability: ComponentAbility, is_subgraph: bool = False, config: Any = None,
                              event: asyncio.Event = None) -> bool:
        try:
            def set_event():
                if event is not None:
                    logger.debug(f"node {self._node_id} with ability {ability.ability_name} set event")
                    event.set()

            # Simplified strategy pattern using lambda functions wrapping async execution
            async def invoke_strategy():
                batch_inputs = await self._pre_invoke()
                if is_subgraph:
                    batch_inputs = {INPUTS_KEY: batch_inputs, CONFIG_KEY: config}
                results = await self._executable.on_invoke(batch_inputs, runtime=self._runtime)
                await self._post_invoke(results)

            async def stream_strategy():
                batch_inputs = await self._pre_invoke()
                if is_subgraph:
                    batch_inputs = {INPUTS_KEY: batch_inputs, CONFIG_KEY: config}
                result_iter = self._executable.on_stream(batch_inputs, runtime=self._runtime)
                await self._post_stream(result_iter, ComponentAbility.STREAM)

            async def collect_strategy():
                collect_iter = await self._pre_stream(ComponentAbility.COLLECT)
                set_event()
                batch_output = await self._executable.on_collect(collect_iter, self._runtime)
                await self._post_invoke(batch_output)

            async def transform_strategy():
                transform_iter = None
                try:
                    transform_iter = await self._pre_stream(ComponentAbility.TRANSFORM)
                    set_event()
                except Exception as e:
                    logger.error(f"failed to prepare transform for node {self._node_id}, error: {e}")
                output_iter = self._executable.on_transform(transform_iter, self._runtime)
                await self._post_stream(output_iter, ComponentAbility.TRANSFORM)

            ability_strategies = {
                ComponentAbility.INVOKE: invoke_strategy,
                ComponentAbility.STREAM: stream_strategy,
                ComponentAbility.COLLECT: collect_strategy,
                ComponentAbility.TRANSFORM: transform_strategy
            }

            # Execute strategy if found
            strategy = ability_strategies.get(ability)
            if strategy:
                await strategy()
            else:
                logger.error(f"error ComponentAbility: {ability.ability_name}")
            return True
        except GraphInterrupt:
            raise
        except JiuWenBaseException as e:
            if e.error_code == StatusCode.COMPONENT_EXECUTE_ERROR.code:
                raise e
            else:
                raise JiuWenBaseException(StatusCode.COMPONENT_EXECUTE_ERROR.code,
                                          StatusCode.COMPONENT_EXECUTE_ERROR.errmsg.format(node_id=self._node_id,
                                                                                           ability=ability.ability_name,
                                                                                           error=e.message))
        except Exception as e:
            raise JiuWenBaseException(StatusCode.COMPONENT_EXECUTE_ERROR.code,
                                      StatusCode.COMPONENT_EXECUTE_ERROR.errmsg.format(node_id=self._node_id,
                                                                                       ability=ability.ability_name,
                                                                                       error=e))
        finally:
            if event and not event.is_set():
                event.set()

    async def __call__(self, state: GraphState, config) -> Output:
        logger.debug(f"begin to call node [{self._node_id}]")
        try:
            if self._executable.post_commit():
                await self.atomic_invoke(config=config, runtime=self._runtime)
            else:
                await self.call(config)
            return {"source_node_id": [self._node_id]}
        except Exception as e:
            if self._runtime.tracer() is not None:
                await self.__trace_error__(e)
            raise e
        finally:
            self._call_count += 1
            self._is_started.clear()
            self._is_call_started.clear()

    async def _atomic_invoke(self, **kwargs) -> Any:
        return await self.call(kwargs.get("config", None))

    async def _pre_invoke(self) -> Optional[dict]:
        await self.__trace_component_begin__()
        inputs_transformer = self._node_config.io_config.inputs_transformer if self._node_config else None
        if inputs_transformer is None:
            inputs_schema = self._node_config.io_config.inputs_schema if self._node_config else None
            inputs = self._runtime.state().get_inputs(inputs_schema) if inputs_schema is not None else None
        else:
            inputs = self._runtime.state().get_inputs_by_transformer(inputs_transformer)
        await self.__trace_component_inputs__(inputs)
        return inputs

    async def _post_invoke(self, results: Optional[dict]) -> Any:
        output_transformer = self._node_config.io_config.outputs_transformer if self._node_config else None
        if output_transformer is None:
            output_schema = self._node_config.io_config.outputs_schema if self._node_config else None
            if output_schema:
                results = get_by_schema(output_schema, results)
                if (not self.is_end_node) and results and isinstance(results, dict):
                    results = {key: value for key, value in results.items() if value is not None}
        else:
            results = output_transformer(results)
        self._runtime.state().set_outputs(results)
        await self.__trace_component_outputs__(results)
        self._clear_interactive()
        return results

    async def _pre_stream(self, ability: ComponentAbility) -> dict:
        await self.__trace_component_begin__()
        actor_manager = self._runtime.actor_manager()
        inputs_schema = self._node_config.stream_io_configs.inputs_schema if self._node_config else None
        logger.debug(f"{ability} consumer handler inputs schema: {inputs_schema}")
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return await actor_manager.consume(self._node_id, ability, inputs_schema)

        async def stream_callable(chunk):
            await TracerWorkflowUtils.trace_component_stream_input(self._runtime, chunk, send=False)

        return await actor_manager.consume(self._node_id, ability, inputs_schema, stream_callable)

    async def _post_stream(self, results_iter: AsyncIterator, ability: ComponentAbility) -> None:
        is_end_node = self.is_end_node
        is_sub_graph = self._runtime.parent_id() != ''
        actor_manager = self._runtime.actor_manager()
        output_transformer = self._node_config.stream_io_configs.outputs_transformer if self._node_config else None
        output_schema = self._node_config.stream_io_configs.outputs_schema if self._node_config else None
        end_stream_index = 0
        async for chunk in results_iter:
            if output_transformer is None:
                message = actor_manager.stream_transform.get_by_default_transformer(chunk, output_schema) \
                    if output_schema else chunk
            else:
                message = actor_manager.stream_transform.get_by_defined_transformer(chunk, output_transformer)
            await self._process_chunk(message, is_end_node, end_stream_index, is_sub_graph, ability)
            end_stream_index += 1
        if is_end_node and is_sub_graph:
            await self._runtime.actor_manager().sub_workflow_stream().send(StreamEmitter.END_FRAME)
        else:
            await self._runtime.actor_manager().end_message(self._node_id, ability)
        self._clear_interactive()

    async def _process_chunk(self, message,
                             is_end_node: bool,
                             end_stream_index: int,
                             is_sub_graph: bool,
                             ability: ComponentAbility):
        if is_end_node and not is_sub_graph:
            if isinstance(message, StreamSchemas):
                message_stream_data = message
            else:
                message_stream_data = {
                    "type": END_NODE_STREAM,
                    "index": end_stream_index,
                    "payload": message
                }
            await self.__trace_component_stream_output__(message_stream_data)
            if self._runtime.stream_writer_manager().get_output_writer():
                await self._runtime.stream_writer_manager().get_output_writer().write(message_stream_data)
        elif is_end_node and is_sub_graph:
            message_stream_data = message.payload if isinstance(message, OutputSchema) else message
            await self.__trace_component_stream_output__(message_stream_data)
            await self._runtime.actor_manager().sub_workflow_stream().send(message_stream_data)
        else:
            first_frame = end_stream_index == 0
            logger.debug(f"sending message: {message}, first_frame: {first_frame}")
            await self.__trace_component_stream_output__(message)
            await self._runtime.actor_manager().produce(self._node_id, message, ability, first_frame=first_frame)

    def _clear_interactive(self) -> None:
        if self._runtime.state().get(INTERACTIVE_INPUT):
            self._runtime.state().update({INTERACTIVE_INPUT: None})

    async def call(self, config: Any = None):
        if self._runtime is None or self._executable is None:
            raise JiuWenBaseException(1, "vertex is not initialized, node is is " + self._node_id)

        is_subgraph = self._executable.graph_invoker()
        try:
            call_ability = [ability for ability in self._component_ability if
                            ability in [ComponentAbility.INVOKE, ComponentAbility.STREAM]]
            logger.debug(f"call ability: {call_ability}, node: {self._node_id}")
            for ability in call_ability:
                await self._run_executable(ability, is_subgraph, config)

        except JiuWenBaseException as e:
            raise e

        # wait only when stream_call called
        logger.debug(f"node [{self._node_id}] stream called: {self._stream_called()}")
        if self._stream_called():
            try:
                result = await asyncio.wait_for(
                    self._stream_done,
                    timeout=(
                        self._stream_called_timeout
                        if self._stream_called_timeout and self._stream_called_timeout > 0
                        else None
                    )
                )
                if isinstance(result, Exception):
                    raise result
            except asyncio.TimeoutError:
                raise JiuWenBaseException(StatusCode.STREAM_FRAME_TIMEOUT_FAILED.code,
                                          StatusCode.STREAM_FRAME_TIMEOUT_FAILED.errmsg.format(
                                              timeout=self._stream_called_timeout))
        elif self._has_stream_call and not self.is_end_node:
            raise JiuWenBaseException(StatusCode.STREAM_NO_INPUT_FAILED.code,
                                      StatusCode.STREAM_NO_INPUT_FAILED.errmsg.format(
                                          abilities=self._stream_abilities()))
        # when the component output is in streaming mode, send an end tracer frame with empty outputs.
        await self.__trace_component_done__()
        logger.debug("node [%s] call finished", self._node_id)

    def is_done(self) -> bool:
        logger.debug(f"call_count: {self._call_count}, stream_call_count: {self._stream_call_count}")
        return (self._call_count == self._stream_call_count
                or self._call_count == self._stream_call_count + 1)

    def _stream_called(self) -> bool:
        return self._stream_call_count == self._call_count + 1

    async def stream_call(self, event: asyncio.Event, error_callback):
        self._stream_call_count += 1
        self._stream_done = asyncio.Future()
        logger.debug(f"node [{self._node_id}] stream entrypoint has been called")

        if self._runtime is None or self._runtime.actor_manager() is None:
            error = JiuWenBaseException(1, "queue manager is not initialized")
            self._stream_done.set_result(error)
            error_callback(error)
            return
        error = None
        tasks = []
        try:
            call_ability = self._stream_abilities()
            for ability in call_ability:
                e = asyncio.Event()
                task = asyncio.create_task(self._run_executable(ability, event=e))
                tasks.append(task)
                await e.wait()
            event.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            logger.debug(f"node [{self._node_id}] all streaming tasks have been finished")
            for result in results:
                if isinstance(result, Exception):
                    raise result
        except asyncio.CancelledError:
            logger.warning(f"node [{self._node_id}] all streaming tasks have been cancelled")
            pending_tasks = []
            for task in tasks:
                if not task.done() and not task.cancelled():
                    task.cancel()
                    pending_tasks.append(task)
                results = await asyncio.gather(*pending_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(f"task with exception, {result}")
        except Exception as e:
            logger.error(f"failed to call node [{self._node_id}], error: {e}")
            error_callback(e)
            error = e
        finally:
            self._stream_done.set_result(error if error else True)
            await self.__trace_component_stream_input_send__()
            logger.debug(f"node [{self._node_id}] stream call finished")

    def _stream_abilities(self) -> list[Literal[ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]:
        call_ability = [ability for ability in self._component_ability if
                        ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]
        return call_ability

    def should_handle_message(self) -> bool:
        call_ability = self._stream_abilities()
        return len(call_ability) > 0

    async def __trace_component_inputs__(self, inputs: Optional[dict]) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        self._is_call_started.set()
        need_send = (not self._has_stream_call) or self._stream_done.done()
        await TracerWorkflowUtils.trace_component_inputs(self._runtime, inputs, send=need_send)
        if self._executable.component_type() == SUB_WORKFLOW_COMPONENT:
            self._runtime.tracer().register_workflow_span_manager(self._runtime.executable_id())

    async def __trace_component_outputs__(self, outputs: Optional[dict] = None) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_outputs(self._runtime, outputs)

    async def __trace_component_begin__(self) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        if not self._is_started.is_set():
            self._is_started.set()
            await TracerWorkflowUtils.trace_component_begin(self._runtime)

    async def __trace_component_done__(self) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_done(self._runtime)

    async def __trace_component_stream_output__(self, chunk) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_stream_output(self._runtime, chunk)

    async def __trace_error__(self, error: Exception) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_error(self._runtime, error)

    async def __trace_component_stream_input_send__(self) -> None:
        if (not self._runtime.tracer()) or self._executable.skip_trace():
            return
        if (not self._has_call) or self._is_call_started.is_set():
            await TracerWorkflowUtils.trace_component_stream_input(self._runtime, {}, send=True)

    async def reset(self):
        self._call_count = 0
        self._stream_call_count = 0
        self._stream_done.cancel()
        try:
            await self._stream_done
        except CancelledError:
            pass
        self._stream_done = asyncio.Future()
