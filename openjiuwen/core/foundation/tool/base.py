# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABCMeta, abstractmethod
from typing import Any, AsyncIterator, Dict, Type
from typing import TypeVar
from pydantic import BaseModel, Field

from openjiuwen.core.common import BaseCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.schema import ToolInfo

Input = TypeVar('Input', contravariant=True)
Output = TypeVar('Output', contravariant=True)


class ToolCard(BaseCard):
    input_params: Dict[str, Any] | Type[BaseModel] = Field(default_factory=dict)
    properties: Dict[str, Any] = Field(default_factory=dict)

    def tool_info(self):
        return ToolInfo(name=self.name, description=self.description, parameters=self.input_params)


class _ToolMeta(ABCMeta):
    def __call__(cls, *args, **kwargs):
        instance = super().__call__(*args, **kwargs)
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.runner.callback.events import ToolCallEvents
        _fw = Runner.callback_framework
        instance.invoke = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_INVOKE_INPUT,
            output_event=ToolCallEvents.TOOL_INVOKE_OUTPUT,
        )(instance.invoke)
        instance.stream = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_STREAM_INPUT,
            output_event=ToolCallEvents.TOOL_STREAM_OUTPUT,
        )(instance.stream)
        return instance


class Tool(metaclass=_ToolMeta):
    """tool class that defined the data types and content for LLM modules"""

    def __init__(self, card: ToolCard):
        """Constructs a new tool instance with the given configuration.

        Args:
            card: ToolCard configuration defining tool behavior and parameters

        Note:
            The tool card is stored internally and used for validation and
            metadata purposes throughout the tool's lifecycle.
        """
        if card is None:
            raise build_error(StatusCode.TOOP_CARD_INVALID, card=card, reason="card is None")
        if not card.id:
            raise build_error(StatusCode.TOOP_CARD_INVALID, card=card, reason="card is is None or empty")
        self._card = card

    @property
    def card(self) -> ToolCard:
        return self._card

    @abstractmethod
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Execute the tool with provided inputs and return final result.

        This method performs complete tool execution in a single call,
        processing all inputs and returning the final output when the
        operation is fully completed.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters such as timeout,
                     retry policies, or tool-specific options

        Returns:
            Output: The complete result of tool execution

        """
        pass

    @abstractmethod
    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Execute the tool and stream incremental results.

        This method supports long-running operations by yielding partial
        results as they become available, enabling real-time processing
        and progress tracking.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters for streaming behavior

        Yields:
            Output: Incremental results during tool execution

        """
        pass
