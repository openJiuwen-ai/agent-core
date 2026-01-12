# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, TYPE_CHECKING

from openjiuwen.core.session.callback.callback_manager import CallbackManager

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session as AgentSession


class Session:
    """
    Session is the main class for managing the workflow of a session.
    """

    def __init__(self, parent: "AgentSession" = None, session_id: str = None, envs: dict[str, Any] = None):
        self._session_id = session_id
        self._envs = envs
        self._callback_manager = CallbackManager()
        self._parent = parent

    def get_callback_manager(self) -> CallbackManager:
        return self._callback_manager

    def get_session_id(self) -> str:
        return self._session_id

    def get_envs(self):
        return self._envs

    def get_parent(self):
        return self._parent


def create_workflow_session(parent: "AgentSession" = None, session_id: str = None,
                            envs: dict[str, Any] = None) -> Session:
    return Session(parent=parent, session_id=session_id, envs=envs)

