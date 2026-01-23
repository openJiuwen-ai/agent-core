# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import Any, TYPE_CHECKING, Union, AsyncIterator

from openjiuwen.core.session import Config
from openjiuwen.core.session.internal.wrapper import TaskSession
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.workflow import Session as WorkflowSession

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard


class Session:
    def __init__(self, session_id: str = None, envs: dict[str, Any] = None, card: "AgentCard" = None):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = TaskSession(session_id=session_id, config=config, card=card)
        self._card = card

    def get_session_id(self) -> str:
        return self._session_id

    def get_envs(self):
        return self._inner.get_envs()

    def get_agent_id(self):
        return self._card.id

    def get_agent_name(self):
        return self._card.name

    def get_agent_description(self):
        return self._card.description

    async def write_stream(self, data: Union[dict, OutputSchema]):
        await self._inner.write_stream(data)

    async def write_custom_stream(self, data: dict):
        await self._inner.write_custom_stream(data)

    def stream_iterator(self) -> AsyncIterator[Any]:
        return self._inner.stream_iterator()

    async def post_run(self):
        await self._inner.post_run()

    def create_workflow_session(self) -> WorkflowSession:
        return WorkflowSession(parent=self, session_id=self.get_session_id())

    async def interact(self, value):
        await self._inner.interact(value)


def create_agent_session(session_id: str = None, envs: dict[str, Any] = None, card: "AgentCard" = None) -> Session:
    return Session(session_id=session_id, envs=envs, card=card)