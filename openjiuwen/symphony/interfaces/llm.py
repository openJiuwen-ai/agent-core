# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Minimal LLM boundary used by Symphony algorithms."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import JsonValue

SymphonyMessage: TypeAlias = dict[str, JsonValue]
SymphonyMessages: TypeAlias = str | list[SymphonyMessage]
# Observers receive response, stage, and operation as positional arguments.
ModelResponseObserver: TypeAlias = Callable[[Any, str, str | None], None | Awaitable[None]]


@runtime_checkable
class SymphonyLLM(Protocol):
    """Text-returning async LLM interface.

    The signature is intentionally a subset of
    :class:`openjiuwen.core.foundation.llm.Model.invoke`, so a core ``Model``
    can be injected directly. Implementations may return plain text or an
    assistant-message object exposing ``content`` or ``parser_content``.
    """

    async def invoke(
        self,
        messages: SymphonyMessages,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> object:
        """Invoke the model and return text or an assistant-message object."""

        ...
