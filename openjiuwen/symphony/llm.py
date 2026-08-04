"""Adapters for using openJiuwen model clients with Symphony."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from json_repair import repair_json

from openjiuwen.symphony.interfaces import current_llm_usage_context

logger = logging.getLogger(__name__)

LLMResponseObserver = Callable[[Any, str, str | None], None | Awaitable[None]]


class _ModelInvoker(Protocol):
    async def invoke(self, messages: Any, **kwargs: Any) -> Any:
        ...


class OpenJiuwenLLMClient:
    """Adapt :class:`openjiuwen.core.foundation.llm.Model` to Symphony's JSON contract."""

    def __init__(
        self,
        model: _ModelInvoker,
        *,
        response_observer: LLMResponseObserver | None = None,
    ) -> None:
        self._model = model
        self._response_observer = response_observer

    async def complete_json_async(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: int | float | None = None,
        error_context: str = "LLM",
        request_overrides: Mapping[str, Any] | None = None,
    ) -> str:
        """Invoke the native model and return repaired JSON text."""

        invoke_kwargs = dict(request_overrides or {})
        if timeout is not None:
            invoke_kwargs["timeout"] = timeout

        try:
            response = await self._model.invoke(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                **invoke_kwargs,
            )
            content = _extract_text_content(response)
            if not content:
                raise ValueError("response content is empty")
            await self._observe_response(response)
            return repair_json(content, return_objects=False)
        except Exception as exc:
            raise RuntimeError(f"{error_context} request failed: {exc}") from exc

    async def _observe_response(self, response: Any) -> None:
        if self._response_observer is None:
            return
        stage, operation = current_llm_usage_context()
        try:
            observed = self._response_observer(response, stage, operation)
            if inspect.isawaitable(observed):
                await observed
        except Exception:
            logger.warning("Symphony LLM response observer failed.", exc_info=True)


def _extract_text_content(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, Mapping):
        content = response.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        return "".join(_content_part_text(item) for item in content).strip()
    return ""


def _content_part_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        value = item.get("text") or item.get("content") or ""
    else:
        value = getattr(item, "text", None) or getattr(item, "content", None) or ""
    return value if isinstance(value, str) else ""


__all__ = ["LLMResponseObserver", "OpenJiuwenLLMClient"]
