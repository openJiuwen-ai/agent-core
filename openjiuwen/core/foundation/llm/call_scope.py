# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Request-scoped identity for a single LLM call.

Every ``Model.invoke`` / ``Model.stream`` opens a scope that binds a fresh
call id for the duration of the request. Observers hanging off the callback
framework (``LLMCallEvents.*``) read that id to tell one in-flight request
from another instead of guessing by recency: the input event, every stream
chunk, the output event and the error event of one request all report the
same id, and no two concurrent requests ever share one.

Why the id is bound here and not inside the callback handlers:
``Model.stream`` drives the underlying async generator through
``asyncio.wait_for``, which runs each ``__anext__`` in its own task. A task
copies the context, so a ``ContextVar`` bound *inside* a chunk callback is
discarded the moment that chunk is delivered, and the next chunk starts from
the context that existed before the stream began. Binding the id in the
calling frame — before the first ``__anext__`` — puts it in exactly that
context, which every per-chunk task then inherits.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

# Id of the LLM request whose callbacks are currently firing, or "" outside
# any request. Read through :func:`get_current_llm_call_id`.
_current_llm_call_id: ContextVar[str] = ContextVar("_openjiuwen_llm_call_id", default="")
_unified_completion_expected: ContextVar[bool] = ContextVar(
    "_openjiuwen_unified_llm_completion_expected",
    default=False,
)
_llm_observation_suppressed: ContextVar[bool] = ContextVar(
    "_openjiuwen_llm_observation_suppressed",
    default=False,
)


def get_current_llm_call_id() -> str:
    """Return the id of the LLM request in flight on this context.

    Returns:
        The current call id, or an empty string when no request opened a
        scope in this context (a caller that bypasses ``Model``, or a
        callback triggered outside any LLM call).
    """
    return _current_llm_call_id.get()


def expects_unified_llm_completion() -> bool:
    """Return whether ``Model`` owns the current call's success completion.

    Older callback integrations that explicitly open with
    ``LLM_INVOKE_INPUT``/``LLM_STREAM_INPUT`` historically use ``LLM_OUTPUT``
    as their terminal event. Calls routed through ``Model``
    instead have a stronger lifecycle: ``LLM_INVOKE_OUTPUT`` terminates an
    invoke and ``LLM_STREAM_COMPLETED`` terminates a naturally exhausted
    stream. Observers use this bit to preserve the legacy terminal event
    without prematurely closing spans owned by that unified lifecycle.
    """
    return _unified_completion_expected.get()


def is_llm_observation_suppressed() -> bool:
    """Return whether this internal call is excluded from agent trajectories."""
    return _llm_observation_suppressed.get()


class LlmObservationSuppression:
    """Exclude internal model probes from user-visible agent trajectories."""

    def __init__(self) -> None:
        self._previous = False

    def __enter__(self) -> "LlmObservationSuppression":
        """Bind suppression for this context and tasks created inside it."""
        self._previous = _llm_observation_suppressed.get()
        _llm_observation_suppressed.set(True)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Restore the enclosing observation policy."""
        _llm_observation_suppressed.set(self._previous)


class LlmCallScope:
    """Bind a fresh call id for one LLM request.

    Used as a context manager around the whole request, input event through
    output event::

        with LlmCallScope():
            return await self._client.invoke(...)

    The previous id is restored on exit by rebinding rather than by
    ``ContextVar.reset``: an async generator's ``finally`` may run on the
    event loop's async-generator finalizer instead of the frame that opened
    the scope, and a token cannot be reset from a foreign context.

    Attributes:
        call_id: The id bound for the duration of the scope.
    """

    def __init__(
        self,
        call_id: Optional[str] = None,
        *,
        unified_completion: bool = False,
    ) -> None:
        """Initialize the scope.

        Args:
            call_id: Explicit id to bind. A fresh uuid4 hex is generated when
                omitted, which is the normal case.
            unified_completion: Whether the enclosing ``Model`` lifecycle
                will emit a dedicated success terminal event. Leave false for
                legacy manually-opened compatibility callback flows.
        """
        self.call_id = call_id or uuid.uuid4().hex
        self._previous: str = ""
        self._unified_completion = unified_completion
        self._previous_unified_completion = False

    def __enter__(self) -> "LlmCallScope":
        """Bind this scope's call id, remembering the enclosing one."""
        self._previous = _current_llm_call_id.get()
        self._previous_unified_completion = _unified_completion_expected.get()
        _current_llm_call_id.set(self.call_id)
        _unified_completion_expected.set(self._unified_completion)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Restore the enclosing call id."""
        _current_llm_call_id.set(self._previous)
        _unified_completion_expected.set(self._previous_unified_completion)
