# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum
from typing import Self, Union, Callable, Any, Optional, Dict

from pydantic import BaseModel, Field

from openjiuwen.core.common.constants.constant import INDEX, CONFIG_KEY, LOOP_ID, FINISH_INDEX
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.component.break_comp import BreakComponent, LoopController
from openjiuwen.core.component.condition.array import ArrayConditionInRuntime
from openjiuwen.core.component.condition.condition import Condition, AlwaysTrue, FuncCondition
from openjiuwen.core.component.condition.expression import ExpressionCondition
from openjiuwen.core.component.condition.number import NumberConditionInRuntime
from openjiuwen.core.component.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from openjiuwen.core.component.loop_callback.loop_callback import LoopCallback, END_ROUND, START_ROUND, OUT_LOOP, \
    FIRST_LOOP
from openjiuwen.core.component.loop_callback.output import OutputCallback
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.atomic_node import AtomicNode
from openjiuwen.core.graph.base import Graph, INPUTS_KEY
from openjiuwen.core.graph.executable import Output, Input, Executable
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.constants import LOOP_NUMBER_MAX_LIMIT_DEFAULT, LOOP_NUMBER_MAX_LIMIT_KEY
from openjiuwen.core.runtime.runtime import BaseRuntime, Runtime
from openjiuwen.core.runtime.workflow import NodeRuntime, SubWorkflowRuntime
from openjiuwen.core.stream_actor.manager import ActorManager
from openjiuwen.core.workflow.base import BaseWorkFlow
from openjiuwen.core.workflow.workflow_config import ComponentAbility
from openjiuwen.graph.graph import PregelGraph
from openjiuwen.graph.pregel import GraphInterrupt, START, END


class EmptyExecutable(Executable):
    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        pass

    def skip_trace(self) -> bool:
        return True


class PostLoopBody(Executable):
    def __init__(self):
        self._finish_index = -1

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        finish_index = runtime.state().get(FINISH_INDEX)
        if finish_index is not None:
            self._finish_index = finish_index
        self._finish_index += 1
        runtime.state().update({FINISH_INDEX: self._finish_index})
        runtime.state().commit()
        return None

    def skip_trace(self) -> bool:
        return True

    def get_finish_index(self) -> int:
        return self._finish_index

    def set_finish_index(self, finish_index: int) -> None:
        self._finish_index = finish_index


class LoopGroup(BaseWorkFlow, Executable):

    def __init__(self):
        super().__init__()
        self.compiled_graph = None
        self.group_input_schema = {}
        self._break_components = []
        self._start_nodes = []
        self._end_nodes = []

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: Union[Executable, WorkflowComponent],
            *,
            comp_ability: list[ComponentAbility] = None,
            wait_for_all: bool = None,
            inputs_schema: dict = None,
            stream_inputs_schema: dict = None,
            outputs_schema: dict = None,
            stream_outputs_schema: dict = None,
            inputs_transformer=None,
            outputs_transformer=None,
            **kwargs
    ) -> Self:
        # Check for nested loop components
        if isinstance(workflow_comp, LoopComponent):
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_NESTED_LOOP_ERROR.code,
                                      StatusCode.LOOP_COMPONENT_NESTED_LOOP_ERROR.errmsg)
        if isinstance(workflow_comp, BreakComponent):
            self._break_components.append(workflow_comp)
        super().add_workflow_comp(comp_id, workflow_comp, wait_for_all=wait_for_all, inputs_schema=inputs_schema,
                                  stream_inputs_schema=stream_inputs_schema,
                                  stream_outputs_schema=stream_outputs_schema,
                                  outputs_schema=outputs_schema, inputs_transformer=inputs_transformer,
                                  outputs_transformer=outputs_transformer, comp_ability=comp_ability)
        if self._drawable and isinstance(workflow_comp, BreakComponent):
            self._drawable.set_break_node(comp_id)

    def start_nodes(self, nodes: list[str]) -> Self:
        for node in nodes:
            self.start_comp(node)
        self._start_nodes = nodes
        return self

    def start_comp(self, start_comp_id: str) -> Self:
        """Record start nodes even if caller uses BaseWorkFlow API directly."""
        super().start_comp(start_comp_id)
        if start_comp_id not in self._start_nodes:
            self._start_nodes.append(start_comp_id)
        return self

    def end_nodes(self, nodes: list[str]) -> Self:
        for node in nodes:
            self.end_comp(node)
        self._end_nodes = nodes
        return self

    def end_comp(self, end_comp_id: str) -> Self:
        """Record end nodes even if caller uses BaseWorkFlow API directly."""
        super().end_comp(end_comp_id)
        if end_comp_id not in self._end_nodes:
            self._end_nodes.append(end_comp_id)
        return self

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        if not self._start_nodes:
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_MISSING_START_NODES_ERROR.code,
                                      StatusCode.LOOP_COMPONENT_MISSING_START_NODES_ERROR.errmsg)
        if not self._end_nodes:
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_MISSING_END_NODES_ERROR.code,
                                      StatusCode.LOOP_COMPONENT_MISSING_END_NODES_ERROR.errmsg)
        self._auto_complete_abilities()
        actor_manager = ActorManager(self._workflow_spec, self._stream_actor, sub_graph=True, runtime=runtime)
        loop_runtime = SubWorkflowRuntime(runtime.parent(), self._workflow_config.metadata.id, actor_manager)
        self.compiled_graph = self.compile(loop_runtime)
        await self.compiled_graph.invoke(inputs, loop_runtime)
        return None

    def skip_trace(self) -> bool:
        return True

    def graph_invoker(self) -> bool:
        return True

    @property
    def break_components(self):
        return self._break_components

    @property
    def is_empty(self):
        """Check if loop group has no components"""
        try:
            nodes = self._graph.get_nodes()
            return len(nodes) == 0
        except Exception:
            # If we can't get nodes, assume empty
            return True


BROKEN = "_broken"
FIRST_IN_LOOP = "_first_in_loop"

CONDITION_NODE_ID = "condition"
BODY_NODE_ID = "body"
POST_BODY_NODE_ID = "post_body"


class AdvancedLoopComponent(WorkflowComponent, LoopController, Executable, AtomicNode):

    def __init__(self, body: Executable,
                 condition: Union[str, Callable[[], bool], Condition] = None, break_nodes: list[BreakComponent] = None,
                 callbacks: list[LoopCallback] = None, new_graph: Graph = None):
        super().__init__()
        self._node_id = None
        self._body = body
        self._post_body = PostLoopBody()

        self._condition: Condition
        if condition is None:
            self._condition = AlwaysTrue()
        elif isinstance(condition, Condition):
            self._condition = condition
        elif isinstance(condition, Callable):
            self._condition = FuncCondition(condition)
        elif isinstance(condition, str):
            self._condition = ExpressionCondition(condition)

        if break_nodes:
            for break_node in break_nodes:
                break_node.set_controller(self)

        self._callbacks: list[LoopCallback] = []
        if callbacks:
            for callback in callbacks:
                self.register_callback(callback)

        self._graph = new_graph if new_graph is not None else PregelGraph()
        self._graph.add_node(BODY_NODE_ID, self._body)
        self._graph.add_node(CONDITION_NODE_ID, EmptyExecutable())
        self._graph.add_node(POST_BODY_NODE_ID, self._post_body)
        self._graph.add_edge(START, CONDITION_NODE_ID)
        self._graph.add_edge(BODY_NODE_ID, POST_BODY_NODE_ID)
        self._graph.add_edge(POST_BODY_NODE_ID, CONDITION_NODE_ID)
        self._graph.add_conditional_edges(CONDITION_NODE_ID, self)

        self._in_loop = [BODY_NODE_ID]
        self._out_loop = [END]
        self._node_runtime = None

    def register_callback(self, callback: LoopCallback):
        self._callbacks.append(callback)

    def __call__(self, *args, **kwargs) -> list[str]:
        return self.atomic_invoke(runtime=self._node_runtime)

    def _atomic_invoke(self, **kwargs) -> Any:
        try:
            outputs = self._condition_invoke(runtime=self._node_runtime)
            return outputs
        except Exception as e:
            if isinstance(e, JiuWenBaseException):
                raise
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_EXECUTION_ERROR.code,
                                      StatusCode.LOOP_COMPONENT_EXECUTION_ERROR.errmsg.format(error_msg=str(e))) from e

    def _condition_invoke(self, runtime: BaseRuntime) -> Output:
        index = runtime.state().get(INDEX)
        if index is None:
            runtime.state().update({BROKEN: False, INDEX: 0})
            runtime.state().set_outputs({INDEX: 0})
            runtime.state().commit()
            index = 0

        finish_index = self._post_body.get_finish_index()
        if finish_index + 1 < index or finish_index > index:
            # resume from checkpoint
            finish_index = index - 1

        if finish_index == index:
            runtime.state().update({INDEX: index + 1})
            runtime.state().set_outputs({INDEX: index + 1})
            runtime.state().commit()


        continue_loop = False if self.is_broken() else self._condition(runtime=runtime)
        for callback in self._callbacks:
            if finish_index < 0:
                callback(FIRST_LOOP, runtime)
            elif finish_index == index:
                callback(END_ROUND, runtime, index + 1)
            if continue_loop:
                callback(START_ROUND, runtime)
            else:
                callback(OUT_LOOP, runtime)

        if not continue_loop:
            runtime.state().update({INDEX: 0, BROKEN: False})
            self._post_body.set_finish_index(-1)
            runtime.parent().state().update({POST_BODY_NODE_ID: None})
            runtime.state().set_outputs({INDEX: 0})

        return self._in_loop if continue_loop else self._out_loop

    def is_broken(self) -> bool:
        _is_broken = self._node_runtime.state().get(BROKEN)
        if isinstance(_is_broken, bool):
            return _is_broken
        return False

    def break_loop(self):
        self._node_runtime.state().update({BROKEN: True})

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        loop_runtime = runtime
        self._node_id = loop_runtime.node_id()
        self._node_runtime = NodeRuntime(loop_runtime, self._node_id)

        loop_runtime.state().set_outputs({LOOP_ID: self._node_id})
        state = loop_runtime.state()._io_state.get_state()
        if self._node_id in state:
            del state[self._node_id]
        loop_runtime.state().set_outputs(state)
        loop_runtime.state().commit()

        if loop_runtime.tracer() is not None:
            loop_runtime.tracer().register_workflow_span_manager(loop_runtime.executable_id())
        compiled = self._graph.compile(loop_runtime)
        await compiled.invoke(inputs, loop_runtime)
        result = self._node_runtime.state().get_outputs(self._node_id)
        loop_runtime.state()._io_state.update_by_id(self._node_id, {self._node_id: None})
        return result

    def graph_invoker(self) -> bool:
        return True

    @property
    def body(self) -> LoopGroup:
        return self._body


class LoopType(str, Enum):
    Array = "array"
    Number = "number"
    AlwaysTrue = "always_true"
    Expression = "expression"


class LoopInput(BaseModel):
    loop_type: Optional[str] = Field("")
    loop_number: Optional[int] = Field(0)
    loop_array: Optional[Dict[str, Any]] = Field(default_factory=dict)
    bool_expression: Optional[Union[str, bool]] = Field("")
    intermediate_var: Dict[str, Union[str, Any]] = Field(default_factory=dict)


class LoopComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, loop_group: LoopGroup, output_schema: dict):
        super().__init__()
        self._loop_group = loop_group
        self._output_schema = output_schema
        if loop_group.is_empty:
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_EMPTY_GROUP_ERROR.code,
                                      "empty loop group has no components to execute")

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        try:
            if not isinstance(inputs, dict):
                raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_INPUT_TYPE_ERROR.code,
                                          f"Inputs must be a dictionary, got {type(inputs).__name__}")

            if INPUTS_KEY not in inputs:
                raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_MISSING_INPUT_KEY_ERROR.code,
                                          f"Invalid inputs: missing required key {INPUTS_KEY}")

            loop_input = LoopInput.model_validate(inputs.get(INPUTS_KEY))
            condition: Condition
            if loop_input.loop_type == LoopType.Array.value:
                condition = ArrayConditionInRuntime(loop_input.loop_array)
            elif loop_input.loop_type == LoopType.Number.value:
                max_loop_limit = runtime.get_env(LOOP_NUMBER_MAX_LIMIT_KEY) or LOOP_NUMBER_MAX_LIMIT_DEFAULT
                try:
                    max_loop_limit = int(max_loop_limit)
                except (TypeError, ValueError):
                    max_loop_limit = LOOP_NUMBER_MAX_LIMIT_DEFAULT

                if loop_input.loop_number is None:
                    raise JiuWenBaseException(StatusCode.NUMBER_CONDITION_ERROR.code,
                                              "loop_number variable not found or is None")

                if loop_input.loop_number > max_loop_limit:
                    raise JiuWenBaseException(
                        StatusCode.NUMBER_CONDITION_ERROR.code,
                        f"loop_number exceeds maximum limit {max_loop_limit}"
                    )

                condition = NumberConditionInRuntime(loop_input.loop_number)
            elif loop_input.loop_type == LoopType.AlwaysTrue.value:
                condition = AlwaysTrue()
            elif loop_input.loop_type == LoopType.Expression.value:
                if isinstance(loop_input.bool_expression, bool):
                    condition = FuncCondition(lambda: loop_input.bool_expression)
                else:
                    condition = ExpressionCondition(loop_input.bool_expression)
            else:
                raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_INVALID_LOOP_TYPE_ERROR.code,
                                          f"Invalid loop type '{loop_input.loop_type}' for LoopComponent")

            if self._loop_group.is_empty:
                raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_EMPTY_GROUP_ERROR.code,
                                          "Loop group is empty, no components to execute")

            output_callback = OutputCallback(self._output_schema)
            callbacks: list = [output_callback]
            if loop_input.intermediate_var:
                callbacks.append(IntermediateLoopVarCallback(loop_input.intermediate_var))

            loop_component = AdvancedLoopComponent(self._loop_group, condition, self._loop_group.break_components,
                                                   callbacks)
            return await loop_component.on_invoke({INPUTS_KEY: {}, CONFIG_KEY: inputs.get(CONFIG_KEY)},
                                                  runtime.base())
        except GraphInterrupt:
            raise
        except JiuWenBaseException:
            raise
        except Exception as e:
            raise JiuWenBaseException(StatusCode.LOOP_COMPONENT_EXECUTION_ERROR.code,
                                      f"LoopComponent error: {str(e)}") from e

    def graph_invoker(self) -> bool:
        return True

    @property
    def loop_group(self) -> LoopGroup:
        return self._loop_group
