# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import Any, TYPE_CHECKING

from openjiuwen.core.session import BaseSession

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session as AgentSession


class Session:
    """
    Session is the main class for managing the workflow of a session.
    """

    def __init__(self, parent: BaseSession = None, session_id: str = None, envs: dict[str, Any] = None,
                 *, trace_id: str = None, execution_id: str = None, workspace_id: str = None,
                 environment_id: str = None, project_id: str = None):
        self._envs = envs
        self._parent = parent
        if parent is not None:
            self._session_id = session_id
            self._envs = parent.config().get_envs()
        elif session_id is not None:
            self._session_id = session_id
        else:
            self._session_id = str(uuid.uuid4())
        self._trace_id = trace_id
        if execution_id is None and parent is not None:
            getter = getattr(parent, "get_execution_id", None) or getattr(parent, "execution_id", None)
            if callable(getter):
                try:
                    execution_id = getter()
                except Exception:  # noqa: BLE001 - 旧会话无该能力时视为未提供
                    execution_id = None
        self._execution_id = execution_id
        # 请求级环境作用域（issue #1755）：同一 Runtime 服务多个工作空间，
        # 只能随执行写入 Span；子工作流从 parent 继承同值，缺失则保持 None。
        parent_scope = parent.get_env_scope() if hasattr(parent, "get_env_scope") else {}
        self._workspace_id = workspace_id if workspace_id else parent_scope.get("workspace_id")
        self._environment_id = environment_id if environment_id else parent_scope.get("environment_id")
        self._project_id = project_id if project_id else parent_scope.get("project_id")
        self._workflow_card = None

    def get_session_id(self) -> str:
        return self._session_id

    def get_trace_id(self) -> str | None:
        return self._trace_id

    def get_execution_id(self) -> str | None:
        """本次执行的执行实例 ID（宿主传入，跨 QA 恢复不变；可空）。"""
        return self._execution_id

    def get_env_scope(self) -> dict:
        """请求级环境作用域（工作空间/环境/项目），供 Span 属性写入（issue #1755）。"""
        return {
            "workspace_id": self._workspace_id,
            "environment_id": self._environment_id,
            "project_id": self._project_id,
        }

    def get_envs(self):
        return self._envs

    def get_parent(self):
        return self._parent

    def set_workflow_card(self, card):
        self._workflow_card = card

    def get_workflow_card(self):
        return self._workflow_card


def create_workflow_session(parent: "AgentSession" = None, session_id: str = None,
                            envs: dict[str, Any] = None, *, trace_id: str = None,
                            execution_id: str = None, workspace_id: str = None,
                            environment_id: str = None, project_id: str = None) -> Session:
    return Session(parent=parent, session_id=session_id, envs=envs, trace_id=trace_id,
                   execution_id=execution_id, workspace_id=workspace_id,
                   environment_id=environment_id, project_id=project_id)

