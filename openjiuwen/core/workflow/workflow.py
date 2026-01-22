# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import uuid

from collections import OrderedDict
from typing import Self, Union, AsyncIterator, List, Tuple

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.dict_utils import flatten_dict
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.workflow.base import WorkflowCard, WorkflowChunk, WorkflowExecutionState, \
    WorkflowOutput
from openjiuwen.core.workflow._workflow import BaseWorkflow
from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Router, INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.session import WORKFLOW_EXECUTE_TIMEOUT, \
    WORKFLOW_STREAM_FRAME_TIMEOUT, WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT, BaseSession, WorkflowSession
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session import Transformer
from openjiuwen.core.session import SubWorkflowSession, NodeSession
from openjiuwen.core.session.stream import StreamMode, BaseStreamMode, OutputSchema
from openjiuwen.core.session.stream import StreamEmitter
from openjiuwen.core.session.stream import StreamWriterManager
from openjiuwen.core.session.workflow import Session
from openjiuwen.core.graph.stream_actor.manager import ActorManager
from openjiuwen.core.session.tracer import Tracer
from openjiuwen.core.session.tracer import TracerWorkflowUtils
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.graph.graph import PregelGraph


class Workflow:
    """
    A workflow represents a directed graph of components that process data.

    The workflow orchestrates the execution of connected components, managing
    data flow, error handling, and streaming between components.
    """

    def __init__(self, card: WorkflowCard = None, **kwargs):
        """
        Initialize a new workflow.

        Args:
            card: Metadata describing the workflow (name, description, etc.)
            kwargs: workflow configs
        """
        self._card = card if card else WorkflowCard(id=uuid.uuid4().hex)
        self._internal = BaseWorkflow(WorkflowConfig(card=self._card, **kwargs), PregelGraph())
        self._end_comp_id: str = ""
        self._is_streaming = False

    @property
    def card(self):
        """Get the workflow metadata card."""
        return self._card

    def set_start_comp(
            self,
            start_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
    ) -> Self:
        """
        Set the starting component of the workflow.

        The start component is the entry point that receives initial inputs.

        Args:
            start_comp_id: Unique identifier for the start component
            component: The component instance to use as start
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure

        Returns:
            Self for method chaining
        """
        self._internal.add_workflow_comp(start_comp_id,
                                         component,
                                         wait_for_all=False,
                                         inputs_schema=inputs_schema,
                                         outputs_schema=outputs_schema)
        self._internal.start_comp(start_comp_id)
        return self

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable | Executable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        """
        Add a component to the workflow graph.

        Args:
            comp_id: Unique identifier for the component
            workflow_comp: The component instance to add
            wait_for_all: If True, wait for all predecessor outputs before executing
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure
            stream_inputs_schema: Schema for streaming inputs
            stream_outputs_schema: Schema for streaming outputs
            comp_ability: List of component capabilities (streaming, batching, etc.)

        Returns:
            Self for method chaining
        """
        self._validate_schemas(inputs_schema, outputs_schema, stream_inputs_schema, stream_outputs_schema)
        self._internal.add_workflow_comp(comp_id,
                                         workflow_comp,
                                         wait_for_all=wait_for_all,
                                         inputs_schema=inputs_schema,
                                         outputs_schema=outputs_schema,
                                         stream_inputs_schema=stream_inputs_schema,
                                         stream_outputs_schema=stream_outputs_schema,
                                         comp_ability=comp_ability)
        return self

    def set_end_comp(
            self,
            end_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            response_mode: str = None
    ) -> Self:
        """
        Set the ending component of the workflow.

        The end component produces the final output of the workflow.

        Args:
            end_comp_id: Unique identifier for the end component
            component: The component instance to use as end
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure
            stream_inputs_schema: Schema for streaming inputs
            stream_outputs_schema: Schema for streaming outputs
            response_mode: How the component should respond (e.g., "stream", "batch")

        Returns:
            Self for method chaining
        """
        self._validate_schemas(inputs_schema, outputs_schema, stream_inputs_schema, stream_outputs_schema)
        comp_ability = []
        if response_mode is not None and "streaming" == response_mode:
            self._is_streaming = True
            if inputs_schema is not None:
                comp_ability.append(ComponentAbility.STREAM)
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.TRANSFORM)
                if isinstance(component, End):
                    component.set_mix()
            if not comp_ability:
                comp_ability = [ComponentAbility.STREAM]
        else:
            comp_ability = [ComponentAbility.INVOKE]
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.COLLECT)
                if isinstance(component, End):
                    component.set_mix()
        wait_for_all = True if ((ComponentAbility.COLLECT in comp_ability)
                                or (ComponentAbility.TRANSFORM in comp_ability)) else False
        self._internal.add_workflow_comp(
            end_comp_id,
            component,
            wait_for_all=wait_for_all,
            comp_ability=comp_ability,
            inputs_schema=inputs_schema,
            outputs_schema=outputs_schema,
            stream_inputs_schema=stream_inputs_schema,
            stream_outputs_schema=stream_outputs_schema,
        )
        self._internal.end_comp(end_comp_id)
        self._end_comp_id = end_comp_id
        return self

    def add_connection(self, src_comp_id: str | list[str], target_comp_id: str) -> Self:
        """
        Add a data connection between components.

        Creates a directed edge for regular (non-streaming) data flow.

        Args:
            src_comp_id: Source component ID or set of IDs
            target_comp_id: Target component ID

        Returns:
            Self for method chaining
        """
        self._internal.add_connection(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        """
        Add a streaming connection between components.

        Creates a directed edge for streaming data flow.

        Args:
            src_comp_id: Source component ID
            target_comp_id: Target component ID

        Returns:
            Self for method chaining
        """
        self._internal.add_stream_connection(src_comp_id, target_comp_id)
        return self

    def add_conditional_connection(self, src_comp_id: str, router: Router) -> Self:
        """
        Add a conditional connection with routing logic.

        Creates a connection where the target is determined dynamically
        based on the router's logic.

        Args:
            src_comp_id: Source component ID
            router: Router instance that decides the target based on data

        Returns:
            Self for method chaining
        """
        self._internal.add_conditional_connection(src_comp_id, router)
        return self

    async def invoke(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            **kwargs
    ) -> WorkflowOutput:
        """
        Execute the workflow synchronously.

        Runs the entire workflow and returns the final output.

        Args:
            inputs: Input data for the workflow
            session: Workflow session for state management
            context: context engine
            **kwargs: Additional execution parameters
                - is_sub: Whether this is a sub-workflow execution
                - skip_inputs_validate: Whether to skip input validation

        Returns:
            WorkflowOutput containing results and metadata
        """
        if kwargs.get("is_sub"):
            return await self._sub_invoke(inputs, session, context, **kwargs)

        session.set_workflow_card(self._card)
        if self._card.input_params is not None:
            inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                    skip_validate=kwargs.get("skip_inputs_validate"))

        parent = session.get_parent()
        if parent is not None and not hasattr(parent, "base"):
            parent = getattr(parent, "_inner")
        workflow_session = WorkflowSession(workflow_id=self._card.id,
                                           parent=parent.base() if parent is not None else None,
                                           session_id=session.get_session_id(),
                                           callback_manager=session.get_callback_manager())
        workflow_session.config().set_envs(session.get_envs())

        async def _invoke_task():
            logger.info(f"begin to invoke, input: {inputs}")
            chunks = []
            async for chunk in self._stream(inputs, workflow_session, context=context,
                                            stream_modes=[BaseStreamMode.OUTPUT], skip_inputs_validate=True):
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
                    result = workflow_session.state().get_outputs(self._end_comp_id)
                output = WorkflowOutput(result=result, state=WorkflowExecutionState.COMPLETED)
            logger.info("end to invoke, results=%s", output)
            return output

        invoke_timeout = workflow_session.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        return await self._execute_with_timeout(_invoke_task, invoke_timeout, StatusCode.WORKFLOW_INVOKE_TIMEOUT)

    async def stream(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            stream_modes: list[StreamMode] = None,
            **kwargs
    ) -> AsyncIterator[WorkflowChunk]:
        """
        Execute the workflow with streaming output.

        Returns an async iterator that yields workflow chunks as they become available.

        Args:
            inputs: Input data for the workflow
            session: Workflow session for state management
            stream_modes: Type(s) of WorkflowChunk
            context: context engine
            **kwargs: Additional execution parameters
                - is_sub: Whether this is a sub-workflow execution
                - skip_inputs_validate: Whether to skip input validation

        Yields:
            WorkflowChunk: Stream chunks containing partial results, logs, or events
        """
        if kwargs.get("is_sub"):
            async for chunk in self._sub_stream(inputs, session, context, **kwargs):
                yield chunk
            return

        session.set_workflow_card(self._card)
        if self._card.input_params is not None:
            inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                    skip_validate=kwargs.get("skip_inputs_validate"))
        parent = session.get_parent()
        if parent is not None and not hasattr(parent, "base"):
            parent = getattr(parent, "_inner")
        workflow_session = WorkflowSession(workflow_id=self._card.id,
                                           parent=parent.base() if parent is not None else None,
                                           session_id=session.get_session_id(),
                                           callback_manager=session.get_callback_manager())
        workflow_session.config().set_envs(session.get_envs())
        async for chunk in self._stream(inputs, workflow_session, context, stream_modes, **kwargs):
            yield chunk

    def draw(
            self,
            title: str = "",
            output_format: str = "mermaid",  # "mermaid", "png", "svg"
            expand_subgraph: int | bool = False,
            enable_animation: bool = False,  # only works for "mermaid" format
            **kwargs
    ) -> str | bytes:
        """
        Generate a Mermaid diagram of the workflow.

        Visualizes the workflow structure as a flowchart.

        Args:
            title: Diagram title
            output_format: Output format ("mermaid", "png", or "svg")
            expand_subgraph: Level of subgraph expansion (False/True or integer depth)
            enable_animation: Enable animation in Mermaid diagram (Mermaid format only)
            **kwargs: Additional rendering options

        Returns:
            str: Mermaid syntax when output_format="mermaid"
            bytes: Image binary data when output_format="png" or "svg"
        """
        if output_format == "png":
            return self._internal.to_mermaid_png(title, expand_subgraph)
        if output_format == "svg":
            return self._internal.to_mermaid_svg(title, expand_subgraph)
        return self._internal.to_mermaid(title, expand_subgraph, enable_animation)

    async def _sub_invoke(self, inputs: Input, session: Session,
                          context: ModelContext = None, **kwargs) -> Output:
        logger.info(f"begin to sub_invoke, inputs: {inputs}")
        actor_manager, sub_workflow_session = self._prepare_sub_workflow_session(getattr(session, "_inner"))

        try:
            compiled_graph = self._internal.compile(sub_workflow_session, context)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: kwargs.get(CONFIG_KEY)},
                                        sub_workflow_session)
            if self._is_streaming:
                messages = []
                sub_end_ability = self._internal.config().spec.comp_configs.get(self._end_comp_id).abilities
                required_abilities = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
                stream_ability_count = sum(ability in sub_end_ability for ability in required_abilities)
                while stream_ability_count > 0:
                    frame = await actor_manager.sub_workflow_stream().receive(
                        session.get_env(WORKFLOW_EXECUTE_TIMEOUT))
                    if frame is None:
                        logger.warning("no frame received")
                        continue
                    if frame == StreamEmitter.END_FRAME:
                        logger.info("received end frame of sub_invoke")
                        stream_ability_count -= 1
                        continue
                    messages.append(frame)
                if messages:
                    logger.debug(f"sub workflow messages: {messages}")
                    return dict(stream=messages)

            node_session = NodeSession(sub_workflow_session, self._end_comp_id)
            output_key = self._end_comp_id

            results = node_session.state().get_outputs(output_key)
            logger.info(f"end to sub_invoke, result: {results}")
            return results
        finally:
            await sub_workflow_session.close()
            await self._internal.reset()

    async def _sub_stream(self, inputs: Input, session: Session, context: ModelContext = None, **kwargs) -> \
            AsyncIterator[Output]:
        logger.info(f"begin to sub_stream, input: {inputs}")
        actor_manager, sub_workflow_session = self._prepare_sub_workflow_session(getattr(session, "_inner"))

        try:
            compiled_graph = self._internal.compile(sub_workflow_session, context=context)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: kwargs.get(CONFIG_KEY)}, sub_workflow_session)
            if self._is_streaming:
                frame_count = 0
                stream_timeout = session.get_env(WORKFLOW_EXECUTE_TIMEOUT)
                sub_end_ability = self._internal.config().spec.comp_configs.get(self._end_comp_id).abilities
            required_abilities = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
            stream_ability_count = sum(ability in sub_end_ability for ability in required_abilities)
            while stream_ability_count > 0:
                logger.debug(f"waiting for frame {frame_count} with timeout {stream_timeout}")
                frame = await actor_manager.sub_workflow_stream().receive(stream_timeout)
                if frame is None:
                    logger.warning("no frame received")
                    continue
                if frame == StreamEmitter.END_FRAME:
                    stream_ability_count -= 1
                    logger.debug(f"received end frame of sub_stream after {frame_count} frames")
                    continue
                frame_count += 1
                logger.debug(f"yielding frame {frame_count}: {frame}")
                yield frame
        finally:
            await sub_workflow_session.close()
            await self._internal.reset()

    async def _execute_with_timeout(self, func, timeout, status_code):
        task = asyncio.create_task(func())
        try:
            return await asyncio.wait_for(task, timeout=timeout if (timeout and timeout > 0) else None)
        except asyncio.TimeoutError as e:
            raise JiuWenBaseException(status_code.code, status_code.errmsg.format
            (error_msg="timeout", timeout=timeout)) from e
        except JiuWenBaseException as e:
            raise e
        except Exception as e:
            if task.done() and not task.cancelled():
                if isinstance(task.exception(), JiuWenBaseException):
                    raise task.exception()
                else:
                    raise JiuWenBaseException(StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.code,
                                              StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.errmsg.format(
                                                  error_msg=task.exception())) from e
            else:
                raise JiuWenBaseException(StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.code,
                                          StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.errmsg.format(error_msg=e)) from e
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

    def _validate_and_init_session(self, session: WorkflowSession, stream_modes: list[StreamMode]):
        self._internal._auto_complete_abilities()
        mq_manager = ActorManager(self._internal._workflow_config.spec, self._internal._stream_actor, sub_graph=False,
                                  session=session)
        session.set_actor_manager(mq_manager)
        session.set_stream_writer_manager(StreamWriterManager(stream_emitter=StreamEmitter(), modes=stream_modes))
        if session.tracer() is None and (stream_modes is None or BaseStreamMode.TRACE in stream_modes):
            tracer = Tracer()
            tracer.init(session.stream_writer_manager(), session.callback_manager())
            session.set_tracer(tracer)

    def _prepare_sub_workflow_session(self, session: BaseSession) -> Tuple[ActorManager, BaseSession]:
        """
        Prepare common components for sub workflow execution.

        Args:
            session: The base session

        Returns:
            tuple: (actor_manager, sub_workflow_session)
        """
        self._internal._auto_complete_abilities()
        actor_manager = ActorManager(self._internal._workflow_config.spec, self._internal._stream_actor, sub_graph=True,
                                     session=session)
        sub_workflow_session = SubWorkflowSession(
            session,
            workflow_id=self._card.id,
            actor_manager=actor_manager
        )
        return actor_manager, sub_workflow_session

    async def _stream(self, inputs: Input,
                      session: WorkflowSession,
                      context: ModelContext = None,
                      stream_modes: list[StreamMode] = None,
                      **kwargs
                      ) -> AsyncIterator[WorkflowChunk]:
        self._validate_and_init_session(session, stream_modes)
        # workflow start tracer info
        await TracerWorkflowUtils.trace_workflow_start(session, inputs)
        # calculate timeout and frame_timeout
        timeout = session.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        frame_timeout = session.config().get_env(WORKFLOW_STREAM_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= frame_timeout:
            frame_timeout = timeout
        session.config().set_envs({WORKFLOW_STREAM_FRAME_TIMEOUT: frame_timeout})
        first_frame_timeout = session.config().get_env(WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= first_frame_timeout:
            first_frame_timeout = timeout
        session.config().set_envs({WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT: first_frame_timeout})

        async def stream_process():
            compiled_graph = self._internal.compile(session, context)
            try:
                await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: None}, session)
            finally:
                # workflow end tracer info
                outputs = session.state().get_outputs(self._end_comp_id)
                await TracerWorkflowUtils.trace_workflow_done(session, outputs)
                await session.stream_writer_manager().stream_emitter().close()

        task = asyncio.create_task(
            self._execute_with_timeout(stream_process, timeout, StatusCode.WORKFLOW_STREAM_EXECUTION_TIMEOUT))

        interaction_chuck_list = []
        chunks = []
        async for chunk in session.stream_writer_manager().stream_output(first_frame_timeout=first_frame_timeout,
                                                                         timeout=frame_timeout,
                                                                         need_close=True):
            yield chunk
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                interaction_chuck_list.append(chunk)
            chunks.append(chunk)
        try:
            await task
            results = session.state().get_outputs(self._end_comp_id)
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
            raise JiuWenBaseException(
                StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.code,
                StatusCode.WORKFLOW_EXECUTION_RUNTIME_ERROR.errmsg.format(error=e),
            ) from e

        finally:
            await session.close()
            await self._internal.reset()

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

        context.add_messages(user_messages + assistant_messages)

    @staticmethod
    def _validate_schemas(inputs_schema: dict | Transformer = None, outputs_schema: dict | Transformer = None,
                          stream_inputs_schema: dict | Transformer = None,
                          stream_outputs_schema: dict | Transformer = None):
        if isinstance(inputs_schema, dict) and isinstance(stream_inputs_schema, dict):
            flatten_inputs_schema = flatten_dict(inputs_schema)
            flatten_stream_inputs_schema = flatten_dict(stream_inputs_schema)
            for key in flatten_inputs_schema.keys():
                if key in flatten_stream_inputs_schema.keys():
                    raise JiuWenBaseException(StatusCode.WORKFLOW_INPUT_INVALID.code,
                        StatusCode.WORKFLOW_INPUT_INVALID.errmsg.format(
                        error_msg=f"duplicate key both exist in inputs_schema with stream_inputs_schema, key={key}"))
        if isinstance(outputs_schema, dict) and isinstance(stream_outputs_schema, dict):
            flatten_outputs_schema = flatten_dict(outputs_schema)
            flatten_stream_outputs_schema = flatten_dict(stream_outputs_schema)
            for key in flatten_outputs_schema.keys():
                if key in flatten_stream_outputs_schema.keys():
                    raise JiuWenBaseException(StatusCode.WORKFLOW_INPUT_INVALID.code,
                        StatusCode.WORKFLOW_INPUT_INVALID.errmsg.format(
                        error_msg=f"duplicate key both exist in outputs_schema with stream_outputs_schema, key={key}"))
