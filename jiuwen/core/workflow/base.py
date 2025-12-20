#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import inspect
from abc import ABC, abstractmethod
from collections import OrderedDict
from enum import Enum
from typing import Self, Any, Union, AsyncIterator, List

from pydantic import BaseModel

from jiuwen.core.common.constants.constant import INTERACTION
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.component.branch_router import BranchRouter
from jiuwen.core.component.end_comp import End
from jiuwen.core.context_engine.base import Context
from jiuwen.core.graph.base import Graph, Router, INPUTS_KEY, CONFIG_KEY, ExecutableGraph
from jiuwen.core.graph.executable import Executable, Input, Output
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.mq_manager import MessageQueueManager
from jiuwen.core.runtime.runtime import BaseRuntime, ProxyRuntime
from jiuwen.core.runtime.state import Transformer
from jiuwen.core.runtime.utils import NESTED_PATH_SPLIT
from jiuwen.core.runtime.workflow import WorkflowRuntime, SubWorkflowRuntime, NodeRuntime
from jiuwen.core.runtime.wrapper import StateRuntime, RouterRuntime
from jiuwen.core.stream.base import StreamMode, BaseStreamMode, OutputSchema, CustomSchema, TraceSchema
from jiuwen.core.stream.emitter import StreamEmitter
from jiuwen.core.stream.manager import StreamWriterManager
from jiuwen.core.stream_actor.base import StreamActor
from jiuwen.core.tracer.tracer import Tracer
from jiuwen.core.utils.config.user_config import UserConfig
from jiuwen.core.utils.llm.messages import ToolInfo, Function, Parameters
from jiuwen.core.workflow.workflow_config import WorkflowConfig, ComponentAbility, \
    NodeSpec, CompIOConfig, WorkflowInputsSchema, WorkflowMetadata
from jiuwen.graph.pregel.graph import PregelGraph


class WorkflowExecutionState(Enum):
    COMPLETED = "COMPLETED"
    INPUT_REQUIRED = "INPUT_REQUIRED"


class WorkflowOutput(BaseModel):
    result: Any
    state: WorkflowExecutionState


WorkflowChunk = Union[OutputSchema, CustomSchema, TraceSchema]


class BaseWorkFlow:
    def __init__(self, workflow_config: WorkflowConfig = None, new_graph: Graph = None):
        self._graph = new_graph if new_graph else PregelGraph()
        self._workflow_config = workflow_config if workflow_config else WorkflowConfig()
        if not self._workflow_config.metadata:
            self._workflow_config.metadata = WorkflowMetadata()
        self._workflow_spec = self._workflow_config.spec
        self._stream_actor = StreamActor()
        self._runtime = ProxyRuntime()

    def config(self):
        return self._workflow_config

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: Union[Executable, WorkflowComponent],
            *,
            wait_for_all: bool = False,
            inputs_schema: dict = None,
            outputs_schema: dict = None,
            inputs_transformer: Transformer = None,
            outputs_transformer: Transformer = None,
            stream_inputs_schema: dict = None,
            stream_outputs_schema: dict = None,
            stream_inputs_transformer: Transformer = None,
            stream_outputs_transformer: Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        if not isinstance(workflow_comp, WorkflowComponent):
            workflow_comp = self._convert_to_component(workflow_comp)
        workflow_comp.add_component(graph=self._graph, node_id=comp_id, wait_for_all=wait_for_all)
        node_spec = NodeSpec(
            io_config=CompIOConfig(inputs_schema=inputs_schema, outputs_schema=outputs_schema,
                                   inputs_transformer=inputs_transformer, outputs_transformer=outputs_transformer),
            stream_io_configs=CompIOConfig(inputs_schema=stream_inputs_schema, outputs_schema=stream_outputs_schema,
                                           inputs_transformer=stream_inputs_transformer,
                                           outputs_transformer=stream_outputs_transformer),
            abilites=comp_ability if comp_ability is not None else [ComponentAbility.INVOKE])

        for ability in node_spec.abilites:
            if ability in [ComponentAbility.STREAM, ComponentAbility.TRANSFORM, ComponentAbility.COLLECT]:
                if not wait_for_all:
                    raise JiuWenBaseException(-1, "stream components need to wait for all")
        self._workflow_spec.comp_configs[comp_id] = node_spec
        return self

    def start_comp(
            self,
            start_comp_id: str,
    ) -> Self:
        self._graph.start_node(start_comp_id)
        return self

    def end_comp(
            self,
            end_comp_id: str,
    ) -> Self:
        self._graph.end_node(end_comp_id)
        return self

    def add_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        stream_executables = self._graph.get_nodes()
        self._stream_actor.add_stream_consumer(stream_executables[target_comp_id], target_comp_id)
        if src_comp_id not in self._workflow_spec.stream_edges:
            self._workflow_spec.stream_edges[src_comp_id] = [target_comp_id]
        else:
            self._workflow_spec.stream_edges[src_comp_id].append(target_comp_id)
        return self

    def add_conditional_connection(self, src_comp_id: str, router: Router) -> Self:
        if isinstance(router, BranchRouter):
            router.set_runtime(self._runtime)
            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=router)
        else:
            def new_router(state):
                sig = inspect.signature(router)
                if 'runtime' in sig.parameters:
                    return router(runtime=RouterRuntime(self._runtime))
                else:
                    return router()

            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=new_router)
        return self

    def compile(self, runtime: BaseRuntime) -> ExecutableGraph:
        runtime.config().add_workflow_config(self._workflow_config.metadata.id, self._workflow_config)
        self._runtime.set_runtime(runtime)
        return self._graph.compile(runtime)


class WorkflowExecutable(ABC):
    @abstractmethod
    async def invoke(self, inputs, runtime: BaseRuntime, context: Context = None) -> WorkflowOutput:
        pass

    @abstractmethod
    async def sub_invoke(self, inputs, runtime: BaseRuntime, config: Any = None) -> WorkflowOutput:
        pass

    @abstractmethod
    async def stream(
            self,
            inputs,
            runtime: BaseRuntime,
            context: Context = None,
            stream_modes: list[StreamMode] = None
    ) -> AsyncIterator[WorkflowChunk]:
        pass

    def get_tool_info(self) -> ToolInfo:
        pass


class Workflow(BaseWorkFlow, WorkflowExecutable):

    def __init__(self, workflow_config: WorkflowConfig = None):
        super().__init__(workflow_config, PregelGraph())
        self.tool_info = self._convert_to_tool_info(self._workflow_config.workflow_inputs_schema)
        self._end_comp_id: str = ""
        self._end_comp = None
        self.inputs_schema = self._convert_to_tool_info(self._workflow_config.workflow_inputs_schema)

    def _convert_to_tool_info(self, inputs_schema: WorkflowInputsSchema) -> ToolInfo:
        parameters = Parameters(
            type=inputs_schema.type,
            properties=inputs_schema.properties,
            required=inputs_schema.required
        )
        function = Function(
            name=self._workflow_config.metadata.name,
            parameters=parameters,
            description=self._workflow_config.metadata.description,
        )
        return ToolInfo(function=function)

    def set_start_comp(
            self,
            start_comp_id: str,
            component: Union[Executable, WorkflowComponent],
            inputs_schema: dict = None,
            outputs_schema: dict = None,
            inputs_transformer: Transformer = None,
            outputs_transformer: Transformer = None
    ) -> Self:
        self.add_workflow_comp(start_comp_id, component, wait_for_all=False, inputs_schema=inputs_schema,
                               outputs_schema=outputs_schema,
                               inputs_transformer=inputs_transformer,
                               outputs_transformer=outputs_transformer)
        self.start_comp(start_comp_id)
        return self

    def set_end_comp(
            self,
            end_comp_id: str,
            component: Union[Executable, WorkflowComponent],
            inputs_schema: dict = None,
            outputs_schema: dict = None,
            inputs_transformer: Transformer = None,
            outputs_transformer: Transformer = None,
            stream_inputs_schema: dict = None,
            stream_outputs_schema: dict = None,
            stream_inputs_transformer: Transformer = None,
            stream_outputs_transformer: Transformer = None,
            response_mode: str = None
    ) -> Self:
        comp_ability = None
        wait_for_all = False
        if response_mode is not None:
            if "streaming" == response_mode:
                comp_ability = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
                wait_for_all = True
            else:
                comp_ability = [ComponentAbility.INVOKE]
        self.add_workflow_comp(end_comp_id, component, wait_for_all=wait_for_all, inputs_schema=inputs_schema,
                               comp_ability=comp_ability,
                               outputs_schema=outputs_schema,
                               inputs_transformer=inputs_transformer,
                               outputs_transformer=outputs_transformer,
                               stream_inputs_schema=stream_inputs_schema,
                               stream_outputs_schema=stream_outputs_schema,
                               stream_inputs_transformer=stream_inputs_transformer,
                               stream_outputs_transformer=stream_outputs_transformer
                               )
        self.end_comp(end_comp_id)
        self._end_comp_id = end_comp_id
        self._end_comp = component
        return self

    async def sub_invoke(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> Output:
        logger.info("begin to sub_invoke")
        runtime.config().add_workflow_config(self._workflow_config.metadata.id, self._workflow_config)
        sub_workflow_runtime = SubWorkflowRuntime(runtime, workflow_id=self._workflow_config.metadata.id)
        main_workflow_config = sub_workflow_runtime.config().get_workflow_config(
            sub_workflow_runtime.main_workflow_id())
        if main_workflow_config is None:
            raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.code,
                                      StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.errmsg.format(
                                          detail=f"main workflow config is not exit,"
                                                 f" main workflow_id={sub_workflow_runtime.main_workflow_id()}"))
        if sub_workflow_runtime.workflow_nesting_depth() > main_workflow_config.workflow_max_nesting_depth:
            raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.code,
                                      StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.errmsg.format(
                                          detail=f"workflow nesting hierarchy is too big, must <= "
                                                 f"{main_workflow_config.workflow_max_nesting_depth}"))
        self._runtime.set_runtime(runtime)
        compiled_graph = self._graph.compile(sub_workflow_runtime)
        await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: config}, sub_workflow_runtime)
        node_runtime = NodeRuntime(runtime, self._end_comp_id)
        output_key = self._end_comp_id
        if isinstance(self._end_comp, End):
            output_key = self._end_comp_id + NESTED_PATH_SPLIT + "output"
        results = node_runtime.state().get_outputs(output_key)
        if UserConfig.is_sensitive():
            logger.info("end to sub_invoke")
        else:
            logger.info("end to sub_invoke, results=%s", results)

        return results

    async def invoke(self, inputs: Input, runtime: BaseRuntime, context: Context = None) -> Output:
        if UserConfig.is_sensitive():
            logger.info("begin to invoke")
        else:
            logger.info("begin to invoke, input=%s", inputs)
        chunks = []
        async for chunk in self.stream(inputs, runtime, context=context, stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)

        is_interaction = False
        for chunk in chunks:
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                is_interaction = True
                break
        if is_interaction:
            output = WorkflowOutput(result=[chunk for chunk in chunks],
                                    state=WorkflowExecutionState.INPUT_REQUIRED)
        else:
            output = WorkflowOutput(result=runtime.state().get_outputs(self._end_comp_id),
                                    state=WorkflowExecutionState.COMPLETED)
        if UserConfig.is_sensitive():
            logger.info("end to invoke")
        else:
            logger.info("end to invoke, results=%s", output)
        return output

    async def stream(
            self,
            inputs: Input,
            runtime: BaseRuntime,
            context: Context = None,
            stream_modes: list[StreamMode] = None
    ) -> AsyncIterator[WorkflowChunk]:
        self._validate_and_init_runtime(runtime, stream_modes, context)
        compiled_graph = self.compile(runtime)
        self._stream_actor.init(runtime)

        async def stream_process():
            try:
                await self._stream_actor.run()
                await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: None}, runtime)
            finally:
                await runtime.stream_writer_manager().stream_emitter().close()

        task = asyncio.create_task(stream_process())
        interaction_chuck_list = []
        async for chunk in runtime.stream_writer_manager().stream_output(self._workflow_config.stream_timeout):
            yield chunk
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                interaction_chuck_list.append(chunk)

        results = runtime.state().get_outputs(self._end_comp_id)
        if results:
            yield OutputSchema(type="workflow_final", index=0, payload=results)
            self._add_messages_to_context(inputs, results, context)
        elif interaction_chuck_list:
            self._add_messages_to_context(inputs, interaction_chuck_list, context)

        try:
            await task
        except Exception as e:
            raise e

    def _validate_and_init_runtime(self, runtime: BaseRuntime, stream_modes: list[StreamMode], context: Context):
        if isinstance(runtime, WorkflowRuntime):
            runtime.set_workflow_id(self._workflow_config.metadata.id)
            if context:
                runtime._context = context
        mq_manager = MessageQueueManager(self._workflow_spec, False)
        runtime.set_queue_manager(mq_manager)
        runtime.set_stream_writer_manager(StreamWriterManager(stream_emitter=StreamEmitter(), modes=stream_modes))
        if runtime.tracer() is None and (stream_modes is None or BaseStreamMode.TRACE in stream_modes):
            tracer = Tracer()
            tracer.init(runtime.stream_writer_manager(), runtime.callback_manager())
            runtime.set_tracer(tracer)

    def _convert_to_component(self, executable: Executable) -> WorkflowComponent:
        pass

    def get_tool_info(self) -> ToolInfo:
        return self.tool_info

    @staticmethod
    def _add_messages_to_context(inputs, results: Union[dict, List[OutputSchema]], context):
        if context is None:
            return

        user_messages = []
        if isinstance(inputs, dict):
            user_messages.append({"role": "user", "content": inputs.get("query", "")})
        elif isinstance(inputs, InteractiveInput):
            sorted_user_feedback = OrderedDict(inputs.user_inputs)
            user_feedback = "\n".join([feedback for _, feedback in sorted_user_feedback.items()])
            user_messages.append({"role": "user", "content": user_feedback})

        assistant_messages = []
        if isinstance(results, dict):
            workflow_result = results.get("responseContent") or results.get("output")
            assistant_messages.append({"role": "assistant", "content": str(workflow_result)})
        elif isinstance(results, list):
            sorted_user_feedback = OrderedDict()
            for item in results:
                if isinstance(item, OutputSchema):
                    sorted_user_feedback.update({item.payload.id: item.payload.value})
            questions = "\n".join([question for _, question in sorted_user_feedback.items()])
            assistant_messages.append({"role": "assistant", "content": questions})

        context.batch_add_messages(user_messages + assistant_messages)
