# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Long Term Memory Retrieval Workflow Component

Provides a workflow component that retrieves memories from long-term memory
"""

from dataclasses import field
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, workflow_logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.memory.long_term_memory import LongTermMemory, MemResult
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.base import ComponentConfig
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MemoryRetrievalCompConfig(ComponentConfig):
    memory: LongTermMemory
    scope_id: str = LongTermMemory.DEFAULT_VALUE
    user_id: str = LongTermMemory.DEFAULT_VALUE
    threshold: float = field(default=0.3)


class MemoryRetrievalInput(BaseModel):
    query: str
    top_k: int = Field(default=5)
    model_config = ConfigDict(extra="allow")


class MemoryRetrievalOutput(BaseModel):
    fragment_memory_results: List[MemResult] = Field(default_factory=list)
    summary_results: List[MemResult] = Field(default_factory=list)


class MemoryRetrievalExecutable(ComponentExecutable):
    def __init__(self, component_config: MemoryRetrievalCompConfig):
        super().__init__()
        self._config = component_config
        self._memory: LongTermMemory = component_config.memory
        self._session: Optional[Session] = None

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Retrieve memories from long-term memory based on query."""
        self._set_session(session)

        retrieval_input = self.validate_inputs(inputs)
        query = retrieval_input.query

        if not query.strip():
            raise build_error(
                StatusCode.COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR,
                error_msg="Query must be a non-empty string",
            )

        workflow_logger.info(
            "Memory retrieval started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=self._session.get_executable_id(),
            component_type_str="MemoryRetrievalComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "query_length": len(query),
                "top_k": retrieval_input.top_k,
                "threshold": self._config.threshold,
                "user_id": self._config.user_id,
                "scope_id": self._config.scope_id,
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        try:
            mem_results: List[MemResult] = await self._memory.search_user_mem(
                query=query,
                num=retrieval_input.top_k,
                user_id=self._config.user_id,
                scope_id=self._config.scope_id,
                threshold=self._config.threshold,
            )
            summary_results: List[MemResult] = await self._memory.search_user_history_summary(
                query=query,
                num=retrieval_input.top_k,
                user_id=self._config.user_id,
                scope_id=self._config.scope_id,
                threshold=self._config.threshold,
            )
        except Exception as e:
            workflow_logger.error(
                "Memory retrieval failed",
                event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                component_id=self._session.get_executable_id(),
                component_type_str="MemoryRetrievalComponent",
                session_id=self._session.get_session_id(),
            )
            raise build_error(
                StatusCode.COMPONENT_MEMORY_RETRIEVAL_INVOKE_CALL_FAILED,
                error_msg=f"Memory retrieval call failed: {e}",
                cause=e,
            ) from e

        output = self._format_output(mem_results, summary_results)

        workflow_logger.info(
            "Memory retrieval completed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=self._session.get_executable_id(),
            component_type_str="MemoryRetrievalComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "num_results": len(mem_results),
                "num_summary_results": len(summary_results),
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        return output

    def _set_session(self, session: Session):
        self._session = session

    @staticmethod
    def validate_inputs(inputs: Input) -> MemoryRetrievalInput:
        try:
            return MemoryRetrievalInput.model_validate(inputs)
        except ValidationError as e:
            raise build_error(
                StatusCode.COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR,
                error_msg=ExceptionUtils.format_validation_error(e),
                cause=e,
            ) from e

    def _format_output(self, results: List[MemResult], summary_results: List[MemResult]) -> dict:
        output = MemoryRetrievalOutput(
            fragment_memory_results=results,
            summary_results=summary_results,
        )
        return output.model_dump()


class MemoryRetrievalComponent(ComponentComposable):
    def __init__(self, component_config: Optional[MemoryRetrievalCompConfig] = None):
        super().__init__()
        self._config = component_config

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> MemoryRetrievalExecutable:
        return MemoryRetrievalExecutable(self._config)