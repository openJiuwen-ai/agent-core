# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Long Term Memory Write Workflow Component

Provides a workflow component that writes messages to long-term memory
"""

from dataclasses import field
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, workflow_logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.base import ComponentConfig
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MemoryWriteCompConfig(ComponentConfig):
    memory: LongTermMemory
    scope_id: str = LongTermMemory.DEFAULT_VALUE
    user_id: str = LongTermMemory.DEFAULT_VALUE
    session_id: str = LongTermMemory.DEFAULT_VALUE
    agent_config: AgentMemoryConfig = field(default_factory=AgentMemoryConfig)
    gen_mem: bool = field(default=True)
    gen_mem_with_history_msg_num: int = field(default=2)


class MemoryWriteInput(BaseModel):
    messages: List[BaseMessage]
    timestamp: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(extra="allow")


class MemoryWriteOutput(BaseModel):
    success: bool = Field(default=True)


class MemoryWriteExecutable(ComponentExecutable):
    def __init__(self, component_config: MemoryWriteCompConfig):
        super().__init__()
        self._config = component_config
        self._memory: LongTermMemory = component_config.memory
        self._session: Optional[Session] = None

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Write messages to long-term memory."""
        self._set_session(session)

        write_input = self.validate_inputs(inputs)
        messages = write_input.messages

        if not messages:
            raise build_error(
                StatusCode.COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR,
                error_msg="Messages list cannot be empty",
            )

        workflow_logger.info(
            "Long-term memory write started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=self._session.get_executable_id(),
            component_type_str="LongTermMemoryWriteComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "message_count": len(messages),
                "scope_id": self._config.scope_id,
                "user_id": self._config.user_id,
                "gen_mem": self._config.gen_mem,
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        try:
            await self._memory.add_messages(
                messages=messages,
                agent_config=self._config.agent_config,
                user_id=self._config.user_id,
                scope_id=self._config.scope_id,
                session_id=self._config.session_id,
                timestamp=write_input.timestamp,
                gen_mem=self._config.gen_mem,
                gen_mem_with_history_msg_num=self._config.gen_mem_with_history_msg_num,
            )
        except Exception as e:
            workflow_logger.error(
                "Long-term memory write failed",
                event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                component_id=self._session.get_executable_id(),
                component_type_str="LongTermMemoryWriteComponent",
                session_id=self._session.get_session_id(),
            )
            raise build_error(
                StatusCode.COMPONENT_MEMORY_WRITE_INVOKE_CALL_FAILED,
                error_msg=f"Memory write call failed: {e}",
                cause=e,
            ) from e

        output = {
            "success": True,
        }

        workflow_logger.info(
            "Long-term memory write completed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=self._session.get_executable_id(),
            component_type_str="LongTermMemoryWriteComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "message_count": len(messages),
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        return output

    def _set_session(self, session: Session):
        self._session = session

    @staticmethod
    def validate_inputs(inputs: Input) -> MemoryWriteInput:
        try:
            return MemoryWriteInput.model_validate(inputs)
        except ValidationError as e:
            raise build_error(
                StatusCode.COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR,
                error_msg=ExceptionUtils.format_validation_error(e),
                cause=e,
            ) from e


class MemoryWriteComponent(ComponentComposable):
    def __init__(self, component_config: Optional[MemoryWriteCompConfig] = None):
        super().__init__()
        self._config = component_config

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> MemoryWriteExecutable:
        return MemoryWriteExecutable(self._config)
