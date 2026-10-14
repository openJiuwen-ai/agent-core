# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import (
    ABC,
    abstractmethod,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.graph.store import Store
from openjiuwen.core.session.constants import SINGLE_COMPONENT_EXECUTION_KEY
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class Checkpointer(ABC):
    @staticmethod
    def get_thread_id(session: BaseSession) -> str:
        return ":".join([session.session_id(), session.workflow_id()])

    async def validate_interactive_resume(
        self,
        session: BaseSession,
        workflow_store,
        graph_store: Store,
        storage_type: str,
    ) -> None:
        """InteractiveInput 恢复的共享前置校验（修复 6 的三后端统一实现）。

        交互恢复（InteractiveInput）必须有可恢复的 checkpoint。此前快照
        缺失时 recover() 静默返回，图从 start 节点全新起跑，在首个节点
        因拿不到用户输入以难以定位的校验错误崩溃（如 Message
        content=None），并连带 error 分支 release 删除会话数据。这里
        显式校验并抛错，把静默降级变成可诊断错误：
        1) io 侧 workflow state 必须存在；
        2) 图侧 graph checkpoint 必须存在且可续跑（与 PregelLoop
           _is_resume 同口径），否则引擎仍会全新起跑。

        所有后端（Persistence/InMemory/Redis）的 pre_workflow_execute
        InteractiveInput 分支必须在本校验通过后才调用 recover()，保证
        同一非法 resume 行为在所有后端上一致显式报错（默认 InMemory
        后端静默全新起跑即本校验要消除的原始 bug）。

        Args:
            session: 工作流会话。
            workflow_store: io 侧工作流状态存储（须提供 exists(session)）。
            graph_store: 图侧快照存储（须提供 get(session_id, ns)）。
            storage_type: 后端标识（persistence/in_memory/redis），仅用于日志。
        """
        workflow_id = session.workflow_id()
        log_message = dict(
            session_id=session.session_id(),
            workflow_id=workflow_id,
            metadata={"operation": "pre_execute", "storage_type": storage_type},
        )
        if not await workflow_store.exists(session):
            session_logger.error(
                "Interactive resume rejected: no workflow checkpoint",
                event_type=LogEventType.CHECKPOINT_ERROR,
                **log_message,
            )
            raise build_error(
                StatusCode.CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR,
                workflow=workflow_id,
                reason=(
                    "interactive resume requested but no workflow checkpoint "
                    f"exists for session {session.session_id()}"
                ),
            )
        graph_state = None
        if not session.config().get_env(SINGLE_COMPONENT_EXECUTION_KEY, False):
            # 单组件直调(execute_single_component)不经过 Pregel 图引擎,
            # 中断状态只存 io 侧、图侧快照本就不存在,图侧校验跳过;
            # 完整图执行的中断恢复必须有图侧快照(否则引擎全新起跑)。
            graph_state = await graph_store.get(session.session_id(), workflow_id)
        graph_resumable = graph_state is not None and (
            bool(graph_state.pending_node)
            or bool(graph_state.pending_buffer)
            or bool(graph_state.channel_values)
        )
        if not graph_resumable:
            if session.config().get_env(SINGLE_COMPONENT_EXECUTION_KEY, False):
                # io 侧校验已通过,单组件路径到此放行。
                return
            session_logger.error(
                "Interactive resume rejected: graph checkpoint not resumable",
                event_type=LogEventType.CHECKPOINT_ERROR,
                **log_message,
            )
            raise build_error(
                StatusCode.CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR,
                workflow=workflow_id,
                reason=(
                    "interactive resume requested but graph checkpoint is "
                    f"missing or empty for session {session.session_id()}"
                ),
            )

    @abstractmethod
    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        ...

    @abstractmethod
    async def post_workflow_execute(self, session: BaseSession, result, exception):
        ...

    @abstractmethod
    async def pre_agent_execute(self, session: BaseSession, inputs):
        ...

    @abstractmethod
    async def pre_agent_team_execute(self, session: BaseSession, inputs):
        ...

    @abstractmethod
    async def interrupt_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def post_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def post_agent_team_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def session_exists(self, session_id: str) -> bool:
        ...

    @abstractmethod
    async def release(self, session_id: str):
        ...

    @abstractmethod
    def graph_store(self) -> Store:
        ...


class Storage(ABC):
    @abstractmethod
    async def save(self, session: BaseSession):
        ...

    @abstractmethod
    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        ...

    @abstractmethod
    async def clear(self, session_id: str):
        ...

    @abstractmethod
    async def exists(self, session: BaseSession) -> bool:
        ...


# Key namespace constants
# Namespace for agent state under session
SESSION_NAMESPACE_AGENT = "agent"
# Namespace for agent team state under session
SESSION_NAMESPACE_AGENT_TEAM = "agent-team"
# Namespace for workflow state under session (workflow's own state)
SESSION_NAMESPACE_WORKFLOW = "workflow"
# Namespace for graph state under workflow (separated from workflow's own state)
WORKFLOW_NAMESPACE_GRAPH = "workflow-graph"


def build_key(*parts: str) -> str:
    """
    Build key by joining parts with colon separator.

    Args:
        *parts: Variable number of string parts to join into a key.

    Returns:
        Key string with parts joined by ':'.
    """
    return ":".join(parts)


def build_key_with_namespace(
        session_id: str,
        namespace: str,
        entity_id: str,
        *suffixes: str
) -> str:
    """
    Build key with namespace structure: session:namespace:entity_id:suffixes.

    Args:
        session_id: Session identifier.
        namespace: Namespace (e.g., 'agent', 'workflow').
        entity_id: Entity identifier (e.g., agent_id, workflow_id).
        *suffixes: Additional key suffixes.

    Returns:
        Key string.
    """
    parts = [session_id, namespace, entity_id] + list(suffixes)
    return build_key(*parts)
