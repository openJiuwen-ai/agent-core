# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import inspect
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Self, Any, Union, AsyncIterator, List, Tuple

from pydantic import BaseModel

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.component.branch_router import BranchRouter
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.base import Graph, Router, INPUTS_KEY, CONFIG_KEY, ExecutableGraph
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.runtime.constants import WORKFLOW_EXECUTE_TIMEOUT, \
    WORKFLOW_STREAM_FRAME_TIMEOUT, WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.runtime import BaseRuntime, ProxyRuntime
from openjiuwen.core.runtime.state import Transformer
from openjiuwen.core.runtime.workflow import WorkflowRuntime, SubWorkflowRuntime, NodeRuntime
from openjiuwen.core.runtime.wrapper import RouterRuntime
from openjiuwen.core.stream.base import StreamMode, BaseStreamMode, OutputSchema, CustomSchema, TraceSchema
from openjiuwen.core.stream.emitter import StreamEmitter
from openjiuwen.core.stream.manager import StreamWriterManager
from openjiuwen.core.stream_actor.base import StreamGraph
from openjiuwen.core.stream_actor.manager import ActorManager
from openjiuwen.core.tracer.tracer import Tracer
from openjiuwen.core.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.core.utils.tool.schema import Parameters, ToolInfo
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, ComponentAbility, \
    NodeSpec, CompIOConfig, WorkflowInputsSchema, WorkflowMetadata
from openjiuwen.graph.graph import PregelGraph
from openjiuwen.graph.visualization.drawable import Drawable

WORKFLOW_DRAWABLE = "WORKFLOW_DRAWABLE"


class ConnectionType(Enum):
    """Type of workflow connection."""
    CONNECTION = "connection"
    STREAM_CONNECTION = "stream_connection"


@dataclass
class EdgeTopology:
    """Edge topology context for ability inference."""
    source_map: dict[str, list[str]]
    target_map: dict[str, list[str]]
    source_stream_map: dict[str, list[str]]
    target_stream_map: dict[str, list[str]]

    def all_edge_nodes(self) -> set[str]:
        """Get all nodes referenced in edges."""
        return (
                set(self.source_map.keys()) |
                set(self.target_map.keys()) |
                set(self.source_stream_map.keys()) |
                set(self.target_stream_map.keys())
        )


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
        self._stream_actor = StreamGraph()
        self._runtime = ProxyRuntime()
        self._drawable = None
        if os.environ.get(WORKFLOW_DRAWABLE, "false").lower() == "true":
            self._drawable = Drawable()

    def config(self):
        return self._workflow_config

    @classmethod
    def _validate_comp_id(cls, comp_id: str) -> None:
        """validate compnent id"""
        if len(comp_id) > 100:
            raise JiuWenBaseException(-1, "workflow component id length must not exceed 100")
        if not re.match(r'^[A-Za-z0-9_-]+$', comp_id):
            raise JiuWenBaseException(-1, "workflow component id must contain only letters (a–z, A–Z), "
                                          "digits (0–9), underscores (_) or hyphens (-)")

    def _validate_connection_comp_ids(self, src_comp_id: str, target_comp_id: str,
                                      connection_type: ConnectionType = ConnectionType.CONNECTION) -> None:
        """Validate that component IDs exist in comp_configs before adding connection.

        This prevents KeyError in _auto_complete_abilities when edges reference non-existent components.
        """
        registered_comps = set(self._workflow_spec.comp_configs.keys())

        missing_comps = []
        if src_comp_id not in registered_comps:
            missing_comps.append(f"source '{src_comp_id}'")
        if target_comp_id not in registered_comps:
            missing_comps.append(f"target '{target_comp_id}'")

        if missing_comps:
            raise JiuWenBaseException(
                StatusCode.WORKFLOW_COMPONENT_CONFIG_ERROR.code,
                f"Cannot add {connection_type.value} from '{src_comp_id}' to '{target_comp_id}': "
                f"component(s) {', '.join(missing_comps)} not registered. "
                f"Please call add_workflow_comp/set_start_comp/set_end_comp first. "
                f"Currently registered components: {sorted(registered_comps) if registered_comps else '(none)'}"
            )

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: Union[Executable, WorkflowComponent],
            *,
            wait_for_all: bool = None,
            inputs_schema: dict = None,
            outputs_schema: dict = None,
            inputs_transformer: Transformer = None,
            outputs_transformer: Transformer = None,
            stream_inputs_schema: dict = None,
            stream_outputs_schema: dict = None,
            stream_outputs_transformer: Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        self._validate_comp_id(comp_id)
        if not isinstance(workflow_comp, WorkflowComponent):
            workflow_comp = self._convert_to_component(workflow_comp)
        node_spec = NodeSpec(
            io_config=CompIOConfig(inputs_schema=inputs_schema, outputs_schema=outputs_schema,
                                   inputs_transformer=inputs_transformer, outputs_transformer=outputs_transformer),
            stream_io_configs=CompIOConfig(inputs_schema=stream_inputs_schema, outputs_schema=stream_outputs_schema,
                                           outputs_transformer=stream_outputs_transformer),
            abilities=comp_ability if comp_ability is not None else [])

        for ability in node_spec.abilities:
            if ability in [ComponentAbility.TRANSFORM, ComponentAbility.COLLECT]:
                if wait_for_all is None:
                    wait_for_all = True
                if not wait_for_all:
                    raise JiuWenBaseException(-1, "stream components need to wait for all")
        self._workflow_spec.comp_configs[comp_id] = node_spec
        if wait_for_all is None:
            wait_for_all = False
        workflow_comp.add_component(graph=self._graph, node_id=comp_id, wait_for_all=wait_for_all)

        if self._drawable:
            self._drawable.add_node(comp_id, workflow_comp)
        return self

    def start_comp(
            self,
            start_comp_id: str,
    ) -> Self:
        self._graph.start_node(start_comp_id)

        if self._drawable:
            self._drawable.set_start_node(start_comp_id)
        return self

    def end_comp(
            self,
            end_comp_id: str,
    ) -> Self:
        self._graph.end_node(end_comp_id)

        if self._drawable:
            self._drawable.set_end_node(end_comp_id)
        return self

    def add_connection(self, src_comp_id: Union[str, list[str]], target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        if isinstance(src_comp_id, list):
            for source_id in src_comp_id:
                if source_id not in self._workflow_spec.edges:
                    self._workflow_spec.edges[source_id] = [target_comp_id]
                else:
                    self._workflow_spec.edges[source_id].append(target_comp_id)
                if self._drawable:
                    self._drawable.add_edge(source_id, target_comp_id)
        else:
            if src_comp_id not in self._workflow_spec.edges:
                self._workflow_spec.edges[src_comp_id] = [target_comp_id]
            else:
                self._workflow_spec.edges[src_comp_id].append(target_comp_id)
            if self._drawable:
                self._drawable.add_edge(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        stream_executables = self._graph.get_nodes()
        self._stream_actor.add_stream_consumer(stream_executables[target_comp_id], target_comp_id)
        if src_comp_id not in self._workflow_spec.stream_edges:
            self._workflow_spec.stream_edges[src_comp_id] = [target_comp_id]
        else:
            self._workflow_spec.stream_edges[src_comp_id].append(target_comp_id)

        if self._drawable:
            self._drawable.add_edge(src_comp_id, target_comp_id, False, True)
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

        if self._drawable:
            self._drawable.add_edge(source=src_comp_id, conditional=True, data=router)
        return self

    def compile(self, runtime: BaseRuntime) -> ExecutableGraph:
        if isinstance(runtime, WorkflowRuntime):
            runtime.set_workflow_id(self._workflow_config.metadata.id)
        runtime.config().add_workflow_config(self._workflow_config.metadata.id, self._workflow_config)

        if isinstance(runtime, SubWorkflowRuntime):
            main_workflow_config = runtime.config().get_workflow_config(
                runtime.main_workflow_id())
            if main_workflow_config is None:
                raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.code,
                                          StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.errmsg.format(
                                              detail=f"main workflow config is not exit,"
                                                     f" main workflow_id={runtime.main_workflow_id()}"))
            if runtime.workflow_nesting_depth() > main_workflow_config.workflow_max_nesting_depth:
                raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.code,
                                          StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.errmsg.format(
                                              detail=f"workflow nesting hierarchy is too big, must <= "
                                                     f"{main_workflow_config.workflow_max_nesting_depth}"))
        self._runtime.set_runtime(runtime)
        return self._graph.compile(runtime)

    @property
    def drawable(self):
        return self._drawable

    def to_mermaid(self, title: str = "", expand_subgraph: int | bool = False, enable_animation: bool = False) -> str:
        if self._drawable:
            return self._drawable.to_mermaid(title=title, expand_subgraph=expand_subgraph,
                                             enable_animation=enable_animation)
        return ""

    def to_mermaid_png(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_png(title=title, expand_subgraph=expand_subgraph)
        return b""

    def to_mermaid_svg(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_svg(title=title, expand_subgraph=expand_subgraph)
        return b""

    def _auto_complete_abilities(self):
        """Auto-complete component abilities based on edge topology."""
        edge_topology = self._build_edge_topology()
        self._validate_edge_nodes(edge_topology)

        user_provided = self._get_user_provided_abilities()

        self._complete_loop_node_abilities(edge_topology, user_provided)
        self._complete_stream_node_abilities(edge_topology, user_provided)
        self._complete_invoke_abilities(edge_topology, user_provided)

    def _build_edge_topology(self) -> EdgeTopology:
        """Build edge topology context for ability inference."""
        source_map = self._workflow_spec.edges
        source_stream_map = self._workflow_spec.stream_edges
        return EdgeTopology(
            source_map=source_map,
            target_map=self._source_to_target_map(source_map),
            source_stream_map=source_stream_map,
            target_stream_map=self._source_to_target_map(source_stream_map),
        )

    def _validate_edge_nodes(self, edge_topology: EdgeTopology) -> None:
        """DFX: Validate all nodes in edges exist in comp_configs."""
        registered_comps = set(self._workflow_spec.comp_configs.keys())
        all_edge_nodes = edge_topology.all_edge_nodes()
        missing_nodes = all_edge_nodes - registered_comps
        if not missing_nodes:
            return

        edge_details = self._collect_problematic_edges(edge_topology, missing_nodes)
        raise JiuWenBaseException(
            StatusCode.WORKFLOW_COMPONENT_CONFIG_ERROR.code,
            f"Component ID mismatch: nodes {sorted(missing_nodes)} are referenced in edges "
            f"but not registered via add_workflow_comp/set_start_comp/set_end_comp.\n"
            f"Registered components: {sorted(registered_comps)}\n"
            f"Problematic edges:\n" + "\n".join(edge_details)
        )

    @staticmethod
    def _collect_problematic_edges(edge_topology: EdgeTopology, missing_nodes: set) -> list[str]:
        """Collect edge details that reference missing nodes."""
        edge_details = []
        for connection_type, edge_map in [(ConnectionType.CONNECTION, edge_topology.source_map),
                                          (ConnectionType.STREAM_CONNECTION, edge_topology.source_stream_map)]:
            for src, targets in edge_map.items():
                for tgt in targets:
                    if src in missing_nodes or tgt in missing_nodes:
                        edge_details.append(f"  - {connection_type.value}: '{src}' -> '{tgt}'")
        return edge_details

    def _get_user_provided_abilities(self) -> dict[str, bool]:
        """Check which components have user-provided ability configurations."""
        return {
            comp_id: len(comp_conf.abilities) > 0
            for comp_id, comp_conf in self._workflow_spec.comp_configs.items()
        }

    def _complete_loop_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for loop start/end nodes."""
        loop_start_nodes = getattr(self, '_start_nodes', None) or []
        loop_end_nodes = getattr(self, '_end_nodes', None) or []

        for node in loop_start_nodes:
            if not user_provided[node] and node in edge_topology.source_stream_map:
                self._add_ability_to_node(node, ComponentAbility.STREAM)

        for node in loop_end_nodes:
            if not user_provided[node] and node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_stream_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for stream connection nodes (STREAM/TRANSFORM/COLLECT)."""
        # Nodes that output stream
        for node in edge_topology.source_stream_map:
            if user_provided[node]:
                continue
            # Has regular input + streaming output -> STREAM
            if node in edge_topology.target_map:
                self._add_ability_to_node(node, ComponentAbility.STREAM)
            # Has streaming input + streaming output -> TRANSFORM
            if node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.TRANSFORM)

        # Nodes that receive stream
        for node in edge_topology.target_stream_map:
            if user_provided[node]:
                continue
            # Has streaming input + regular output -> COLLECT
            if node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_invoke_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete INVOKE ability for regular connection nodes."""
        for node in edge_topology.target_map:
            if not user_provided[node] and node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.INVOKE)

    def _add_ability_to_node(self, comp_id: str, ability: ComponentAbility) -> None:
        """Add ability to a component if not already present."""
        abilities = self._workflow_spec.comp_configs[comp_id].abilities
        if ability not in abilities:
            abilities.append(ability)

    @staticmethod
    def _source_to_target_map(source_map: dict[str, list[str]]) -> dict[str, list[str]]:
        """Convert source->targets map to target->sources map."""
        target_map = {}
        for source, targets in source_map.items():
            for target in targets:
                if target not in target_map:
                    target_map[target] = []
                target_map[target].append(source)
        return target_map


class Workflow(BaseWorkFlow):
    def __init__(self, workflow_config: WorkflowConfig = None):
        super().__init__(workflow_config, PregelGraph())
        self.tool_info = self._convert_to_tool_info(self._workflow_config.workflow_inputs_schema)
        self._end_comp_id: str = ""
        self._end_comp = None
        self._is_streaming = False
        self.inputs_schema = self._convert_to_tool_info(self._workflow_config.workflow_inputs_schema)

    def _convert_to_tool_info(self, inputs_schema: WorkflowInputsSchema) -> ToolInfo:
        parameters = Parameters(
            type=inputs_schema.type,
            properties=inputs_schema.properties,
            required=inputs_schema.required
        )
        return ToolInfo(
            name=self._workflow_config.metadata.name,
            description=self._workflow_config.metadata.description,
            parameters=parameters,
        )

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
            stream_outputs_transformer: Transformer = None,
            response_mode: str = None
    ) -> Self:
        wait_for_all = False
        if response_mode is not None and "streaming" == response_mode:
            self._is_streaming = True
            comp_ability = []
            if inputs_schema is not None:
                comp_ability.append(ComponentAbility.STREAM)
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.TRANSFORM)
                if isinstance(component, End):
                    component.set_mix()
                wait_for_all = True
            if not comp_ability:
                comp_ability = [ComponentAbility.STREAM]
        else:
            comp_ability = [ComponentAbility.INVOKE]
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.COLLECT)
                if isinstance(component, End):
                    component.set_mix()
                wait_for_all = True
        self.add_workflow_comp(end_comp_id, component, wait_for_all=wait_for_all, inputs_schema=inputs_schema,
                               comp_ability=comp_ability,
                               outputs_schema=outputs_schema,
                               inputs_transformer=inputs_transformer,
                               outputs_transformer=outputs_transformer,
                               stream_inputs_schema=stream_inputs_schema,
                               stream_outputs_schema=stream_outputs_schema,
                               stream_outputs_transformer=stream_outputs_transformer
                               )
        self.end_comp(end_comp_id)
        self._end_comp_id = end_comp_id
        self._end_comp = component
        return self

    async def sub_invoke(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> Output:
        logger.info(f"begin to sub_invoke, inputs: {inputs}")
        actor_manager, sub_workflow_runtime = self._prepare_sub_workflow_runtime(runtime)

        try:
            compiled_graph = self.compile(sub_workflow_runtime)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: config}, runtime)
            if self._is_streaming:
                messages = []
                while True:
                    frame = await actor_manager.sub_workflow_stream().receive(
                        runtime.config().get_env(WORKFLOW_EXECUTE_TIMEOUT))
                    if frame is None:
                        logger.warning("no frame received")
                        continue
                    if frame == StreamEmitter.END_FRAME:
                        logger.info("received end frame of sub_invoke")
                        break
                    messages.append(frame)
                if messages:
                    logger.debug(f"sub workflow messages: {messages}")
                    return dict(stream=messages)

            node_runtime = NodeRuntime(runtime, self._end_comp_id)
            output_key = self._end_comp_id

            results = node_runtime.state().get_outputs(output_key)
            logger.info(f"end to sub_invoke, result: {results}")
            return results
        finally:
            await sub_workflow_runtime.close()
            await self._graph.reset()

    async def sub_stream(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> AsyncIterator[Output]:
        logger.info(f"begin to sub_stream, input: {inputs}")
        actor_manager, sub_workflow_runtime = self._prepare_sub_workflow_runtime(runtime)

        try:
            compiled_graph = self.compile(sub_workflow_runtime)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: config}, runtime)
            if self._is_streaming:
                frame_count = 0
                stream_timeout = runtime.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
                sub_end_ability = self._workflow_config.spec.comp_configs.get(self._end_comp_id).abilities
            required_abilities = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
            stream_ability_count = sum(ability in sub_end_ability for ability in required_abilities)
            while True:
                logger.debug(f"waiting for frame {frame_count} with timeout {stream_timeout}")
                frame = await actor_manager.sub_workflow_stream().receive(stream_timeout)
                if frame is None:
                    logger.warning("no frame received")
                    continue
                if frame == StreamEmitter.END_FRAME:
                    stream_ability_count -= 1
                    logger.debug(f"received end frame of sub_stream after {frame_count} frames")
                    if stream_ability_count == 0:
                        break
                frame_count += 1
                logger.debug(f"yielding frame {frame_count}: {frame}")
                yield frame
        finally:
            await sub_workflow_runtime.close()
            await self._graph.reset()

    async def invoke(self, inputs: Input, runtime: BaseRuntime, context: Context = None) -> WorkflowOutput:
        async def _invoke_task():
            logger.info(f"begin to invoke, input: {inputs}")
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
                if self._is_streaming:
                    result = chunks
                else:
                    result = runtime.state().get_outputs(self._end_comp_id)
                output = WorkflowOutput(result=result, state=WorkflowExecutionState.COMPLETED)
            logger.info("end to invoke, results=%s", output)
            return output

        invoke_timeout = runtime.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        return await self._execute_with_timeout(_invoke_task, invoke_timeout, StatusCode.WORKFLOW_INVOKE_TIMEOUT)

    async def stream(
            self,
            inputs: Input,
            runtime: BaseRuntime,
            context: Context = None,
            stream_modes: list[StreamMode] = None
    ) -> AsyncIterator[WorkflowChunk]:
        self._validate_and_init_runtime(runtime, stream_modes, context)
        # workflow start tracer info
        await TracerWorkflowUtils.trace_workflow_start(runtime, inputs)
        timeout = runtime.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        frame_timeout = runtime.config().get_env(WORKFLOW_STREAM_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= frame_timeout:
            frame_timeout = timeout
        runtime.config().set_envs({WORKFLOW_STREAM_FRAME_TIMEOUT: frame_timeout})
        first_frame_timeout = runtime.config().get_env(WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= first_frame_timeout:
            first_frame_timeout = timeout
        runtime.config().set_envs({WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT: first_frame_timeout})

        async def stream_process():
            compiled_graph = self.compile(runtime)
            try:
                await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: None}, runtime)
            finally:
                # workflow end tracer info
                outputs = runtime.state().get_outputs(self._end_comp_id)
                await TracerWorkflowUtils.trace_workflow_done(runtime, outputs)
                await runtime.stream_writer_manager().stream_emitter().close()

        task = asyncio.create_task(
            self._execute_with_timeout(stream_process, timeout, StatusCode.WORKFLOW_STREAM_TIMEOUT))

        interaction_chuck_list = []
        chunks = []
        async for chunk in runtime.stream_writer_manager().stream_output(first_frame_timeout=first_frame_timeout,
                                                                         timeout=frame_timeout,
                                                                         need_close=True):
            yield chunk
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                interaction_chuck_list.append(chunk)
            chunks.append(chunk)
        try:
            await task
            results = runtime.state().get_outputs(self._end_comp_id)
            if results:
                self._add_messages_to_context(inputs, results, context)
                yield OutputSchema(type="workflow_final", index=0, payload=results)
            elif interaction_chuck_list:
                self._add_messages_to_context(inputs, interaction_chuck_list, context)
            else:
                self._add_messages_to_context(inputs, chunks, context)
        except JiuWenBaseException as e:
            raise e
        except Exception as e:
            raise JiuWenBaseException(StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.code,
                                      StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.errmsg.format(error=e))
        finally:
            await runtime.close()
            await self._graph.reset()

    async def _execute_with_timeout(self, func, timeout, status_code):
        task = asyncio.create_task(func())
        try:
            return await asyncio.wait_for(task, timeout=timeout if (timeout and timeout > 0) else None)
        except asyncio.TimeoutError:
            raise JiuWenBaseException(status_code.code, status_code.errmsg.format(timeout=timeout))
        except JiuWenBaseException as e:
            raise e
        except Exception as e:
            if task.done() and not task.cancelled():
                if isinstance(task.exception(), JiuWenBaseException):
                    raise task.exception()
                else:
                    raise JiuWenBaseException(StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.code,
                                              StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.errmsg.format(
                                                  error=task.exception())) from e
            else:
                raise JiuWenBaseException(StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.code,
                                          StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.errmsg.format(error=e)) from e
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

    def _validate_and_init_runtime(self, runtime: BaseRuntime, stream_modes: list[StreamMode], context: Context):
        if isinstance(runtime, WorkflowRuntime):
            runtime.set_workflow_id(self._workflow_config.metadata.id)
            if context:
                runtime.set_context(context)
        self._auto_complete_abilities()
        mq_manager = ActorManager(self._workflow_config.spec, self._stream_actor, sub_graph=False, runtime=runtime)
        runtime.set_actor_manager(mq_manager)
        runtime.set_stream_writer_manager(StreamWriterManager(stream_emitter=StreamEmitter(), modes=stream_modes))
        if runtime.tracer() is None and (stream_modes is None or BaseStreamMode.TRACE in stream_modes):
            tracer = Tracer()
            tracer.init(runtime.stream_writer_manager(), runtime.callback_manager())
            runtime.set_tracer(tracer)

    def _prepare_sub_workflow_runtime(self, runtime: BaseRuntime) -> Tuple[ActorManager, BaseRuntime]:
        """
        Prepare common components for sub workflow execution.
        
        Args:
            runtime: The base runtime
            
        Returns:
            tuple: (actor_manager, sub_workflow_runtime)
        """
        self._auto_complete_abilities()
        actor_manager = ActorManager(self._workflow_config.spec, self._stream_actor, sub_graph=True, runtime=runtime)
        sub_workflow_runtime = SubWorkflowRuntime(
            runtime,
            workflow_id=self._workflow_config.metadata.id,
            actor_manager=actor_manager
        )
        return actor_manager, sub_workflow_runtime

    def _convert_to_component(self, executable: Executable) -> WorkflowComponent:
        pass

    def get_tool_info(self) -> ToolInfo:
        return self.tool_info

    @staticmethod
    def _add_messages_to_context(inputs: Input, results: Union[dict, List[OutputSchema]], context):
        if context is None:
            return

        user_messages = []
        if isinstance(inputs, dict):
            user_messages.append({"role": "user", "content": inputs.get("query", "")})
        elif isinstance(inputs, InteractiveInput):
            sorted_user_feedback = OrderedDict(inputs.user_inputs)
            user_feedback = "\n".join([str(feedback) for _, feedback in sorted_user_feedback.items()])
            user_messages.append({"role": "user", "content": user_feedback})

        assistant_messages = []
        if isinstance(results, dict):
            workflow_result = json.dumps(results, ensure_ascii=False)
            assistant_messages.append({"role": "assistant", "content": workflow_result})
        elif isinstance(results, list):
            sorted_user_feedback = OrderedDict()
            assistant_reply = ""
            questions = ""
            for item in results:
                if not isinstance(item, OutputSchema):
                    continue
                if item.type == INTERACTION:
                    sorted_user_feedback.update({item.payload.id: item.payload.value})
                    for _, question in sorted_user_feedback.items():
                        if isinstance(question, str):
                            questions += f"{question}\n"
                        elif isinstance(question, dict) and question.get("value", ""):
                            questions += f"{str(question.get('value'))}\n"
                    questions = questions.strip()
                else:
                    if isinstance(item.payload, dict):
                        answer = item.payload.get("answer", "")
                        if answer is not None:
                            assistant_reply += str(answer)
            if questions:
                assistant_messages.append({"role": "assistant", "content": questions})
            if assistant_reply:
                assistant_messages.append({"role": "assistant", "content": assistant_reply})

        context.batch_add_messages(user_messages + assistant_messages)
