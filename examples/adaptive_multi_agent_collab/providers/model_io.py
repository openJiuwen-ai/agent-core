"""Preserve public SDK stream termination and usage through one complete call.

The installed nonstream clients collapse some provider finish reasons to ``stop``.
This module uses only public Model.stream and parser APIs; it never talks to a
provider client directly. Request admission, deadlines and accounting stay with
ResourceController.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from types import SimpleNamespace
from typing import Any, cast

from openjiuwen.core.foundation.llm import Model

from ..infrastructure.failure_policy import CALL_TIMING, FailureKind, ProviderFailure


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                raise TypeError("the text-only workflow cannot consume a nontext stream content block")
        return "".join(parts)
    raise TypeError("the text-only workflow requires textual stream content")


def _usage_fields(usage: Any) -> dict[str, Any]:
    if isinstance(usage, Mapping):
        return dict(usage)
    # model_fields_set is retained by the public SDK's Pydantic usage object.
    # Excluding unset fields avoids introducing defaults ourselves; SDK clients
    # may still explicitly synthesize zeros, hence sdk_usage_limited below.
    return dict(usage.model_dump(exclude_unset=True))


def _truncated(reason: Any) -> bool:
    normalized = str(reason or "").casefold().replace("_", "").replace("-", "")
    return normalized in {"length", "maxtokens", "maxoutputtokens", "maxcompletiontokens"}


async def invoke_model(model: Any, /, *, messages: Any, output_parser: Any = None, **settings: Any) -> Any:
    """Collect the complete public text stream without altering its finish reason."""

    if not isinstance(model, Model):
        return await model.invoke(messages=messages, output_parser=output_parser, **settings)
    provider = str(getattr(model.model_client_config, "client_provider", "")).casefold()
    if "anthropic" in provider:
        raise ValueError(
            "the installed public Anthropic Model APIs discard max_tokens finish reasons; "
            "this bounded experiment requires an SDK/provider exposing truncation faithfully"
        )

    content: list[str] = []
    reasoning: list[str] = []
    snapshots: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    gateway = provider == "adaptiveopenrouter"
    gateway_frames: list[dict[str, Any]] = []
    gateway_metadata: dict[str, Any] = {}
    gateway_truncated = False

    def collected() -> SimpleNamespace:
        record = dict(gateway_metadata)
        if "generation_id" not in record and record.get("header_generation_id"):
            record["generation_id"] = record["header_generation_id"]
        metadata = dict(usage) if snapshots else None
        if gateway:
            metadata = dict(usage)
            metadata.update(record)
            metadata["raw_gateway_frames"] = tuple(gateway_frames)
        effective_finish_reason = finish_reason
        if gateway_truncated:
            # Some compatibility routes normalize native token exhaustion to
            # stop. Preserve the native/raw value and expose truncation to the
            # existing bounded schema-repair path.
            effective_finish_reason = "length"
        return SimpleNamespace(
            content="".join(content),
            reasoning_content="".join(reasoning) or None,
            finish_reason=effective_finish_reason,
            usage_metadata=metadata,
            parser_content=None,
            parser_error=None,
            sdk_usage_limited=not gateway,
            sdk_usage_snapshots=tuple(snapshots),
            native_finish_reason=gateway_metadata.get("native_finish_reason"),
            generation_id=record.get("generation_id"),
            served_model=gateway_metadata.get("served_model"),
            served_provider=gateway_metadata.get("served_provider"),
            gateway_metadata=record,
        )

    try:
        # Never pass a streaming parser: partial JSON can look valid before the
        # final text or the provider's truncation reason has arrived.
        # The installed public Model.stream implementation yields an async
        # generator; its broader AsyncIterator annotation omits aclose().
        model_stream = cast(AsyncGenerator[Any, None], model.stream(messages=messages, output_parser=None, **settings))
        async with aclosing(model_stream) as stream:
            async for chunk in stream:
                content.append(_text(chunk.content))
                reasoning.append(_text(getattr(chunk, "reasoning_content", None)))
                reason = getattr(chunk, "finish_reason", None)
                if reason and str(reason).casefold() != "null":
                    finish_reason = str(reason)
                    if gateway:
                        gateway_metadata["gateway_finish_reason"] = finish_reason
                        gateway_truncated = gateway_truncated or _truncated(finish_reason)
                raw_metadata = getattr(chunk, "metadata", None) or {}
                native = raw_metadata.get("openrouter") if gateway else None
                if native is not None:
                    gateway_truncated = gateway_truncated or _truncated(native.get("native_finish_reason"))
                    if native["raw"] and "raw_generation_metadata" not in native:
                        gateway_frames.append(dict(native["raw"]))
                    for key, value in native.items():
                        if key not in {"raw", "usage"} and value is not None:
                            gateway_metadata[key] = value
                    snapshot = native.get("usage")
                    if snapshot is not None:
                        snapshots.append(dict(snapshot))
                        # Native snapshots are cumulative, with genuine zeros.
                        # Missing fields remain unknown; no SDK zero heuristic.
                        sources = {**usage.get("usage_sources", {}), **snapshot.get("usage_sources", {})}
                        usage.update({key: value for key, value in snapshot.items() if value is not None})
                        usage["usage_sources"] = sources
                    continue
                current_usage = getattr(chunk, "usage_metadata", None)
                if current_usage is not None:
                    snapshot = _usage_fields(current_usage)
                    snapshots.append(snapshot)
                    for key, value in snapshot.items():
                        if value is None:
                            continue
                        # SDK partial end-of-stream objects can explicitly turn
                        # an absent input/cache count into zero. Preserve earlier
                        # known positive totals; never sum cumulative snapshots.
                        previous = usage.get(key)
                        if value == 0 and isinstance(previous, (int, float)) and previous > 0:
                            continue
                        usage[key] = value
        if finish_reason is None:
            raise ProviderFailure(
                "public Model stream ended without a finish reason; completion cannot be verified",
                FailureKind.TRANSPORT,
            )
        result = collected()
        if output_parser is not None:
            try:
                result.parser_content = await output_parser.parse(result.content)
            except Exception as error:
                # This generation completed and remains fully chargeable. The
                # adapter validates the raw text and applies its bounded repair
                # budget; parser errors are not transport/provider failures.
                result.parser_error = f"{type(error).__name__}: {error}"
        return result
    except BaseException as error:
        # The resource controller can retain partial known usage on cancellation
        # or provider failure without treating a partial response as completed.
        setattr(error, "usage", collected())
        timing = CALL_TIMING.get() or {}
        setattr(error, "timing_phase", timing.get("phase", "generation"))
        setattr(error, "provider_completed", timing.get("provider_completed", False))
        raise
