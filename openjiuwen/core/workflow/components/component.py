# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC
from typing import AsyncIterator, TypeVar

from openjiuwen.core.graph.base import Graph
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Executable
from openjiuwen.core.session import Session, BaseSession
from openjiuwen.core.session import NodeSession
from openjiuwen.core.session import WrappedNodeSession

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", contravariant=True)


class ComponentExecutable(Executable):
    """
    Interface for executable components in a workflow.

    Defines the four fundamental execution patterns for components:
    1. Invoke - Synchronous batch processing
    2. Stream - Streaming output from batch input
    3. Collect - Batch output from streaming input
    4. Transform - Streaming to streaming transformation

    This interface allows components to support different I/O patterns
    based on their capabilities and use cases.
    """
    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        if not isinstance(session, NodeSession):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.code,
                                      StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.errmsg)

        current_class = type(self)
        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'invoke') and
                callable(getattr(current_class, 'invoke')) and
                current_class.invoke is ComponentExecutable.invoke):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.code,
                                      StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                          ability='INVOKE', method='invoke', class_name=type(self).__name__))

        return await self.invoke(inputs, WrappedNodeSession(session), kwargs.get("context"))

    async def on_stream(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        if not isinstance(session, NodeSession):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.code,
                                      StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.errmsg)

        current_class = type(self)

        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'stream') and
                callable(getattr(current_class, 'stream')) and
                current_class.stream is ComponentExecutable.stream):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.code,
                                      StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                          ability='STREAM', method='stream', class_name=type(self).__name__))

        async for value in self.stream(inputs, WrappedNodeSession(session), kwargs.get("context")):
            yield value

    async def on_collect(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        if not isinstance(session, NodeSession):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.code,
                                      StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.errmsg)

        current_class = type(self)

        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'collect') and
                callable(getattr(current_class, 'collect')) and
                current_class.collect is ComponentExecutable.collect):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.code,
                                      StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                          ability='COLLECT', method='collect', class_name=type(self).__name__))

        return await self.collect(inputs, WrappedNodeSession(session, True), kwargs.get("context"))

    async def on_transform(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        if not isinstance(session, NodeSession):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.code,
                                      StatusCode.SESSION_COMPONENT_INVALID_SESSION_TYPE.errmsg)

        current_class = type(self)

        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'transform') and
                callable(getattr(current_class, 'transform')) and
                current_class.transform is ComponentExecutable.transform):
            raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.code,
                                      StatusCode.SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                          ability='TRANSFORM', method='transform', class_name=type(self).__name__))

        async for value in self.transform(inputs, WrappedNodeSession(session, True), kwargs.get("context")):
            yield value

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Execute component synchronously with batch input and output.

        Args:
            inputs: Batch input data
            session: Current execution session
            context: Context engine

        Returns:
            Output: Batch output data

        Note:
            This is the most common execution pattern for simple operations.
        """
        raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.code,
                                  StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Invoke'))

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """
        Execute component with batch input but streaming output.

        Args:
            inputs: Batch input data
            session: Current execution session
            context:  Context engine

        Returns:
            AsyncIterator[Output]: Async iterator that yields output chunks

        Note:
            Useful for long-running operations or real-time data generation.
        """
        raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.code,
                                  StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Stream'))

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Execute component with streaming input but batch output.

        Args:
            inputs: Streaming input data (async iterator)
            session: Current execution session
            context: Context engine

        Returns:
            Output: Batch output collected from all input chunks

        Note:
            Useful for aggregating streaming data into a single result.
        """
        raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.code,
                                  StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Collect'))

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """
         Execute component with streaming input and streaming output.

         Args:
             inputs: Streaming input data (async iterator)
             session: Current execution session
             context: Context engine

         Returns:
             AsyncIterator[Output]: Async iterator that yields transformed output chunks

         Note:
             This is the most general pattern for real-time data processing pipelines.
         """
        raise JiuWenBaseException(StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.code,
                                  StatusCode.SESSION_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Transform'))


class ComponentComposable(ABC):
    """
    Abstract base class for workflow graph construction.

    This class is responsible for defining how a component integrates
    into a workflow graph. It separates the construction logic from
    the execution logic defined in ComponentExecutable.

    Components implementing this interface can describe their
    graph structure independently of their runtime behavior.
    """

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        """
        Add this component to a workflow graph.

        Args:
            graph: The workflow graph to add this component to
            node_id: Unique identifier for this component node
            wait_for_all: If True, wait for all predecessor outputs before execution

        Note:
            This method defines the component's position and connections
            within the larger workflow graph.
        """
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> Executable:
        """
        Convert this workflow component to an executable instance.

        Returns:
            ComponentExecutable: An executable instance of this component

        Note:
            This method creates the runtime representation of the component
            that can actually execute the business logic.
        """
        if isinstance(self, Executable):
            return self
        raise JiuWenBaseException(
            StatusCode.WORKFLOW_EXECUTION_NOT_SUPPORT.code, "workflow component should implement Executable"
        )


class WorkflowComponent(ComponentExecutable, ComponentComposable):
    """
    Standard implementation combining both execution and graph construction.

    This class provides a complete component implementation that:
    1. Can be executed (implements ComponentExecutable)
    2. Can be added to workflow graphs (implements ComponentComposable)

    This is the most common base class for user-defined components
    that need both construction-time and runtime capabilities.

    Example:
        class MyComponent(WorkflowComponent):
            async def invoke(self, inputs, runtime, context):
                # Implementation here
                pass

            def add_component(self, graph, node_id, wait_for_all=False):
                # Add to graph logic here
                pass
    """
    ...
