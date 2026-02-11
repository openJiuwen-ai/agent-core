# coding: utf-8
# Copyright c) Huawei Technologies Co. Ltd. 2025-2025.
from abc import ABC, abstractmethod
from typing import Any, Union, Optional, List, Tuple

from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.callback.callback_manager import CallbackManager
from openjiuwen.core.session.state.base import State
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.stream.writer import StreamWriter
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import ToolInfo


class BaseSession(ABC):
    @abstractmethod
    def config(self) -> Config:
        ...

    @abstractmethod
    def state(self) -> State:
        ...

    @abstractmethod
    def tracer(self) -> Any:
        ...

    @abstractmethod
    def stream_writer_manager(self) -> StreamWriterManager:
        ...

    @abstractmethod
    def callback_manager(self) -> CallbackManager:
        ...

    @abstractmethod
    def session_id(self) -> str:
        ...

    @abstractmethod
    def checkpointer(self):
        ...

    def actor_manager(self) -> "ActorManager":
        pass

    async def close(self):
        pass


class ProxySession(BaseSession):
    def __init__(self, stub: BaseSession = None):
        self._stub = stub

    def set_session(self, stub: BaseSession):
        self._stub = stub

    def config(self) -> Config:
        return self._stub.config()

    def state(self) -> State:
        return self._stub.state()

    def tracer(self) -> Any:
        return self._stub.tracer()

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stub.stream_writer_manager()

    def callback_manager(self) -> CallbackManager:
        return self._stub.callback_manager()

    def session_id(self) -> str:
        return self._stub.session_id()

    def checkpointer(self):
        return self._stub.checkpointer()
