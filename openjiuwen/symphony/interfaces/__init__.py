"""Integration protocols for :mod:`openjiuwen.symphony`."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Protocol, Sequence


_LLM_USAGE_CONTEXT: ContextVar[tuple[str, str | None]] = ContextVar(
    "symphony_llm_usage_context",
    default=("unknown", None),
)


class LLMClient(Protocol):
    """Minimal JSON-completion contract used by graph and plan algorithms."""

    async def complete_json_async(self, **kwargs: Any) -> str:
        ...


class CapabilityProvider(Protocol):
    """Return the current capability inventory."""

    def __call__(self) -> Sequence[Any] | Awaitable[Sequence[Any]]:
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
    "CapabilityProvider",
    "LLMClient",
    "LLMConfig",
    "current_llm_usage_context",
    "create_llm_client",
    "llm_usage_context",
    "thinking_disabled_request_overrides",
]
