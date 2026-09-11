# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo


@dataclass(frozen=True)
class TokenMeasurement:
    """Token count plus the method and quality metadata used by telemetry."""

    tokens: int
    source: str
    estimated: bool = True
    tokenizer: str | None = None
    model: str | None = None
    fallback_reason: str | None = None
    fallback_tokenizer_model: str | None = None


class TokenCounter(ABC):
    """
    Abstract base class for unified token counting.
    A concrete implementation only needs to override `count`;
    `count_messages` can be reused or overridden as required.
    """

    measurement_source = "not_reported"
    measurement_estimated = True
    measurement_tokenizer = None
    measurement_fallback_reason = None
    measurement_fallback_tokenizer_model = None

    def measure(self, text: str, *, model: str = "", **kwargs) -> TokenMeasurement:
        """Count text and expose the counter's provenance metadata."""
        return self._measurement(self.count(text, model=model, **kwargs), model=model)

    def measure_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> TokenMeasurement:
        """Count messages and expose the counter's provenance metadata."""
        return self._measurement(self.count_messages(messages, model=model, **kwargs), model=model)

    def measure_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> TokenMeasurement:
        """Count tools and expose the counter's provenance metadata."""
        return self._measurement(self.count_tools(tools, model=model, **kwargs), model=model)

    def _measurement(self, tokens: int, *, model: str = "") -> TokenMeasurement:
        return TokenMeasurement(
            tokens=max(int(tokens), 0),
            source=self.measurement_source,
            estimated=self.measurement_estimated,
            tokenizer=self.measurement_tokenizer,
            model=model or None,
            fallback_reason=self.measurement_fallback_reason,
            fallback_tokenizer_model=self.measurement_fallback_tokenizer_model,
        )

    @abstractmethod
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        """
        Count tokens in a single text.

        Args:
            text: The input text to tokenize.
            model: The model name that determines the tokenization rule.

        Returns:
            The number of tokens in `text`.
        """

    @abstractmethod
    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        """
        Count tokens for a list of chat messages.
        The default convention is OpenAI-style: <|start|>{role}\n{content}<|end|>.

        Args:
            messages: List of message objects (with role/content).
            model: The model name that determines the tokenization rule.

        Returns:
            The total estimated token count for `messages`.
        """

    @abstractmethod
    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        """
        Count the number of tokens that a list of tool-calling metadata will consume.

        Args:
            tools: List of ToolInfo objects describing the tools to be injected
                   into the prompt.
            model: The target model name, which determines the tokenization rule.

        Returns:
            int: Total tokens required to represent the tools in the prompt.
        """
