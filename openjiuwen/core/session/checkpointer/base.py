# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod

from openjiuwen.core.graph.store import Store
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


class Checkpointer(ABC):
    @staticmethod
    def get_thread_id(session: BaseSession) -> str:
        return ":".join([session.session_id(), session.workflow_id()])

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
    async def interrupt_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def post_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def release(self, session_id: str):
        ...

    @abstractmethod
    def graph_store(self) -> Store:
        ...