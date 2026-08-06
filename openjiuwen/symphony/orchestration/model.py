"""Internal helpers for invoking the native openJiuwen model from orchestration."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from json_repair import repair_json

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.shared.identity import sanitize_metadata, stable_metadata_sha256

LOGGER = logging.getLogger(__name__)

ModelResponseObserver = Callable[[Any, str, str | None], None | Awaitable[None]]

_MODEL_USAGE_CONTEXT: ContextVar[tuple[str, str | None]] = ContextVar(
    "symphony_model_usage_context",
    default=("unknown", None),
)


@contextmanager
def model_usage_context(stage: str, operation: str | None = None):
    """Tag orchestration model calls for an optional integrating observer."""

    token = _MODEL_USAGE_CONTEXT.set((stage, operation))
    try:
        yield
    finally:
        _MODEL_USAGE_CONTEXT.reset(token)


async def invoke_json(
    model: Model,
    *,
    system_prompt: str,
    user_content: str,
    timeout: int | float | None = None,
    error_context: str = "Model",
    request_overrides: Mapping[str, Any] | None = None,
    response_observer: ModelResponseObserver | None = None,
) -> str:
    """Invoke ``Model`` and return repaired JSON text."""

    invoke_kwargs = dict(request_overrides or {})
    if timeout is not None:
        invoke_kwargs["timeout"] = timeout
    try:
        response = await model.invoke(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            **invoke_kwargs,
        )
        content = _extract_text_content(response)
        if not content:
            raise ValueError("response content is empty")
        await _observe_response(response_observer, response)
        return repair_json(content, return_objects=False)
    except Exception as exc:
        raise RuntimeError(f"{error_context} request failed: {exc}") from exc


def model_identity(model: Model) -> dict[str, Any]:
    """Return stable model identity fields without credentials or volatile IDs."""

    request_config = getattr(model, "model_config", None)
    client_config = getattr(model, "model_client_config", None)
    provider = getattr(client_config, "client_provider", None)
    provider_value = getattr(provider, "value", provider)
    request_identity = sanitize_metadata(_config_dict(request_config))
    client_values = _config_dict(client_config)
    client_values.pop("client_id", None)
    client_identity = sanitize_metadata(client_values)
    metadata = {
        "model": getattr(request_config, "model_name", None),
        "backend": str(provider_value) if provider_value is not None else None,
        "temperature": getattr(request_config, "temperature", None),
        "top_p": getattr(request_config, "top_p", None),
        "max_tokens": getattr(request_config, "max_tokens", None),
        "stop": getattr(request_config, "stop", None),
        "request_config_sha256": stable_metadata_sha256(request_identity),
        "client_config_sha256": stable_metadata_sha256(client_identity),
    }
    if "api_base_sha256" in client_identity:
        metadata["api_base_sha256"] = client_identity["api_base_sha256"]
    return metadata


def thinking_disabled_request_overrides() -> dict[str, Any]:
    return {"extra_body": {"thinking": {"type": "disabled"}}}


async def _observe_response(
    observer: ModelResponseObserver | None,
    response: Any,
) -> None:
    if observer is None:
        return
    stage, operation = _MODEL_USAGE_CONTEXT.get()
    try:
        observed = observer(response, stage, operation)
        if inspect.isawaitable(observed):
            await observed
    except Exception:
        LOGGER.warning("Symphony model response observer failed.", exc_info=True)


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


def _config_dict(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json", by_alias=True, exclude_none=False))
    if isinstance(config, Mapping):
        return dict(config)
    return {key: value for key, value in vars(config).items() if not key.startswith("_")}
