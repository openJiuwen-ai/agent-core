# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import TYPE_CHECKING, Any

from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.state.base import State
from openjiuwen.core.session.state.workflow_state import InMemoryState
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.tracer import Tracer

if TYPE_CHECKING:
    from openjiuwen.core.graph.stream_actor.manager import ActorManager


def _parent_execution_id(parent):
    """统一读取父会话的执行 ID（issue 1753 检视意见①）。

    只在此处兼容 get_execution_id / execution_id 两种历史命名，避免调用方
    各自探测造成遗漏；无该能力或旧会话异常时返回 None（视为未提供）。
    """
    getter = getattr(parent, "get_execution_id", None) or getattr(parent, "execution_id", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - 旧会话无该能力时视为未提供
        return None


class WorkflowSession(BaseSession):
    def __init__(self, workflow_id: str = '', parent: BaseSession = None, session_id: str = None,
                 state: State = None, execution_id: str = None, workspace_id: str = None,
                 environment_id: str = None, project_id: str = None):
        self._session_id = session_id
        if execution_id is None and parent is not None:
            execution_id = _parent_execution_id(parent)
        self._execution_id = execution_id
        # issue #1755：请求级环境作用域随会话链继承（与 execution_id 同模式）
        parent_scope = {}
        if parent is not None and hasattr(parent, "get_env_scope"):
            parent_scope = parent.get_env_scope()
        self._env_scope = {}
        for _key, _value in (
            ("workspace_id", workspace_id),
            ("environment_id", environment_id),
            ("project_id", project_id),
        ):
            _resolved = _value or parent_scope.get(_key)
            if _resolved:
                self._env_scope[_key] = _resolved
        self._parent = parent
        if parent is not None:
            if self._session_id is None:
                self._session_id = parent.session_id()
            self._config = parent.config()
            self._tracer = parent.tracer()
        else:
            if self._session_id is None:
                self._session_id = uuid.uuid4().hex
            self._config = Config()
            self._tracer = None

        self._state = state if state is not None else InMemoryState()
        self._stream_writer_manager = None  # type: StreamWriterManager
        self._actor_manager = None
        self._workflow_id = workflow_id

    def set_stream_writer_manager(self, stream_writer_manager: StreamWriterManager) -> None:
        if self._stream_writer_manager is not None:
            return
        self._stream_writer_manager = stream_writer_manager

    def set_tracer(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def set_actor_manager(self, queue_manager: "ActorManager"):
        if self._actor_manager is not None:
            return
        self._actor_manager = queue_manager

    def set_workflow_id(self, workflow_id):
        self._workflow_id = workflow_id

    def actor_manager(self) -> "ActorManager":
        return self._actor_manager

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def session_id(self) -> str:
        return self._session_id

    def get_env_scope(self) -> dict:
        """请求级环境作用域（workspace/environment/project），供 Span 属性写入。"""
        return dict(self._env_scope or {})

    def get_execution_id(self) -> str | None:
        """执行级 ID（与 execution_id() 同值，供统一探测使用）。"""
        return self._execution_id

    def execution_id(self) -> str | None:
        """兼容别名（保留给旧调用方），语义同 get_execution_id。"""
        return self.get_execution_id()

    def checkpointer(self):
        if self._parent is not None:
            return self._parent.checkpointer()
        # Lazy import to avoid circular import
        from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerFactory
        return CheckpointerFactory.get_checkpointer()

    def workflow_id(self):
        return self._workflow_id

    def main_workflow_id(self):
        return self.workflow_id()

    def workflow_nesting_depth(self):
        return 0

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()

    def parent(self):
        return self._parent


def create_parent_id(session: BaseSession):
    return session.executable_id() if isinstance(session, NodeSession) else ''


def create_executable_id(node_id: str, parent_id: str):
    return parent_id + "." + node_id if len(parent_id) != 0 else node_id


class NodeSession(BaseSession):
    def __init__(self, session: BaseSession, node_id: str, node_type: str = None, skip_trace: bool = False):
        self._node_id = node_id
        self._node_type = node_type
        parent_id = create_parent_id(session)
        executable_id = create_executable_id(node_id, parent_id)
        state = session.state().create_node_state(executable_id, parent_id)
        self._state = state
        self._parent_id = parent_id
        self._executable_id = executable_id
        self._session = session
        self._workflow_id = session.workflow_id()
        self._workflow_nesting_depth = session.workflow_nesting_depth()
        self._main_workflow_id = session.main_workflow_id()
        self._skip_trace = skip_trace
        # Cache node_name from NodeSpec; None when spec missing or name unset.
        # Tracer falls back to node_id() when name is None, preserving prior behavior.
        spec = self.node_config()
        self._node_name = spec.name if spec is not None else None

    def node_id(self):
        return self._node_id

    def node_type(self):
        return self._node_type

    def node_name(self):
        return self._node_name

    def executable_id(self):
        return self._executable_id

    def parent_id(self):
        return self._parent_id

    def workflow_id(self):
        return self._workflow_id

    def get_execution_id(self):
        """执行级 ID（委托外层会话；旧会话仅实现 execution_id 时兼容）。"""
        return _parent_execution_id(self._session)

    def execution_id(self):
        """兼容别名（保留给旧调用方），语义同 get_execution_id。"""
        return self.get_execution_id()

    def get_env_scope(self) -> dict:
        """委托外层会话的环境作用域（issue #1755）。"""
        try:
            return self._session.get_env_scope()
        except Exception:  # noqa: BLE001 - 旧会话无该能力时视为空
            return {}

    def main_workflow_id(self):
        return self._main_workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def actor_manager(self) -> "ActorManager":
        return self._session.actor_manager()

    def parent(self):
        return self._session

    def tracer(self) -> Tracer:
        return self._session.tracer()

    def span(self):
        """Span-protocol stub so the tracer decorators accept node sessions.

        Workflow tool calls reach ``decorate_tool_with_trace(tool, session)``
        either directly from components (e.g. studio's FlowApi/FlowCustomApi/
        FlowMcp nodes) or via ``ResourceMgr.get_tool(tool_id, session=...)``.
        Its ``_should_decorate`` gate requires the ``span`` protocol (the
        agent parent-span source on AgentSession); without this method every
        decoration attempt with a node session silently returned the tool
        undecorated, so tool calls inside workflows emitted no execute_tool
        span. Returning None leaves the
        TraceAgentSpan parentless — the OTel handler then falls back to the
        ambient context, which during node execution is the component's own
        node span (attached in on_call_start), yielding correct parenting.

        The gate is shared by the sibling decorators: once node sessions pass
        it, ``decorate_model_with_trace`` / ``decorate_workflow_with_trace``
        (reachable via ModelMgr.get_model / WorkflowMgr.get_workflow with a
        node session) also decorate and rely on the same ambient-context
        parenting instead of an explicit parent span.
        """
        return None

    def state(self) -> State:
        return self._state

    def config(self) -> Config:
        return self._session.config()

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._session.stream_writer_manager()

    def session_id(self) -> str:
        return self._session.session_id()

    def checkpointer(self):
        # NodeSession delegates to parent session's checkpointer
        return self._session.checkpointer()

    def node_config(self):
        workflow_config = self.config().get_workflow_config(self.workflow_id())
        if workflow_config:
            return workflow_config.spec.comp_configs.get(self._node_id)
        else:
            return None

    def skip_trace(self):
        return self._skip_trace


class SubWorkflowSession(NodeSession):
    def __init__(self, session: NodeSession, workflow_id: str, actor_manager: "ActorManager" = None):
        super().__init__(session=session.parent(), node_id=session.node_id(), node_type=session.node_type())
        # Inherit node_name from the host NodeSession so the sub-workflow root and
        # its internal components trace back to the host component's display name.
        self._node_name = session.node_name()
        self._workflow_id = workflow_id
        self._workflow_nesting_depth = session.workflow_nesting_depth() + 1
        self._main_workflow_id = session.main_workflow_id()
        self._actor_manager = actor_manager

    def workflow_id(self):
        return self._workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def main_workflow_id(self):
        return self._main_workflow_id

    def actor_manager(self) -> "ActorManager":
        return self._actor_manager

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()
