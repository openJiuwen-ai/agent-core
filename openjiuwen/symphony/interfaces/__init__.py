# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public dependency-inversion protocols for Symphony integrations."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from openjiuwen.symphony.interfaces.capability import AtomicCapabilityProvider, CapabilityProvider
from openjiuwen.symphony.interfaces.llm import SymphonyLLM, SymphonyMessage, SymphonyMessages

_LLM_USAGE_CONTEXT: ContextVar[tuple[str, str | None]] = ContextVar(
    "symphony_llm_usage_context",
    default=("unknown", None),
)


class LLMClient(Protocol):
    """Minimal JSON-completion contract used by graph and plan algorithms."""

    async def complete_json_async(self, **kwargs: Any) -> str:
        """Return one structured model response as JSON text."""
        ...


class OrchestrationCapabilityProvider(Protocol):
    """Callable inventory source accepted by the orchestration service."""

    def __call__(self) -> Sequence[Any] | Awaitable[Sequence[Any]]:
        """Return the current orchestration capability inventory."""
        ...


def create_llm_client(config: Any) -> LLMClient:
    """Resolve an explicitly injected client/factory without reading app config."""

    if hasattr(config, "complete_json_async"):
        return config
    factory = getattr(config, "create_client", None)
    if callable(factory):
        return factory()
    if callable(config):
        return config()
    raise ValueError("Symphony requires an explicit llm_client or client factory.")


@contextmanager
def llm_usage_context(stage: str, operation: str | None = None):
    """Tag calls so an injected client can attribute usage without owning accounting."""

    token = _LLM_USAGE_CONTEXT.set((stage, operation))
    try:
        yield
    finally:
        _LLM_USAGE_CONTEXT.reset(token)


def current_llm_usage_context() -> tuple[str, str | None]:
    """Return the active Symphony usage tags for an integrating client."""

    return _LLM_USAGE_CONTEXT.get()


def thinking_disabled_request_overrides() -> dict[str, Any]:
    return {"extra_body": {"thinking": {"type": "disabled"}}}


LLMConfig = Any

__all__ = [
    "AtomicCapabilityProvider",
    "CapabilityProvider",
    "LLMClient",
    "LLMConfig",
    "OrchestrationCapabilityProvider",
    "SymphonyLLM",
    "SymphonyMessage",
    "SymphonyMessages",
    "create_llm_client",
    "current_llm_usage_context",
    "llm_usage_context",
    "thinking_disabled_request_overrides",
]
