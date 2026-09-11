# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Image-input modality probing for harness agents."""

from __future__ import annotations

import asyncio
import base64
import struct
import zlib
from typing import Any, Iterable, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.call_scope import LlmObservationSuppression

_IMAGE_INPUT_SCAN_MAX_DEPTH = 8
_IMAGE_MODALITY_PROBE_TIMEOUT_SECONDS = 5.0
# The probe only needs the model to name one color, so the budget stays small
# enough to cap what a reasoning model can burn here. It is not squeezed down
# to the one token the answer needs, though: a model that prefaces the color
# with a few words would otherwise be cut off before saying it and get cached
# as image-blind forever. Truncated answers are treated as inconclusive on top
# of that -- see ``_interpret_probe_response``.
_IMAGE_MODALITY_PROBE_MAX_TOKENS = 64
# ``finish_reason`` value meaning the answer hit the token budget.
_LENGTH_FINISH_REASON = "length"
# Vendor-specific switches for turning reasoning off, merged into one body.
# Lenient gateways drop the keys they do not know; strict ones reject the whole
# request, which is why ``_run_probe`` retries once without the body.
_THINKING_DISABLED_EXTRA_BODY = {
    "thinking": {"type": "disabled"},
    "enable_thinking": False,
    "reasoning": {"enabled": False},
}
# Probe verdicts keyed by (api_base, model_name): one round-trip per endpoint
# and model for the lifetime of the process.
_probe_results: dict[tuple[str, str], bool] = {}
_probe_tasks: dict[tuple[str, str], "asyncio.Task[None]"] = {}
_IMAGE_INPUT_UNSUPPORTED_ERROR_CODES = (
    "invalid_image_input",
    "image_input_unsupported",
    "unsupported_content_type",
    "unsupported_image",
    "unsupported_image_input",
    "unsupported_message_content_type",
)
_IMAGE_INPUT_UNSUPPORTED_ERROR_PATTERNS = (
    "no endpoints found that support image input",
    "does not accept images",
    "does not support image",
    "doesn't accept images",
    "doesn't support image",
    "do not support image",
    "image input is not supported",
    "image input not supported",
    "image_url is not supported",
    "images are not supported",
    "multimodal input is not supported",
    "not a multimodal model",
    "not support image input",
    "unknown variant",
    "unsupported image",
    "vision is not supported",
)


def _make_red_png_b64() -> str:
    """Generate a small red PNG, base64-encoded."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    width = height = 32
    red_row = b"\x00" + (b"\xff\x00\x00" * width)
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(red_row * height))
    iend = _chunk(b"IEND", b"")
    png = b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend
    return base64.b64encode(png).decode()


DUMMY_IMAGE_B64: str = _make_red_png_b64()


def _iter_exception_error_values(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > _IMAGE_INPUT_SCAN_MAX_DEPTH or value is None:
        return

    if isinstance(value, str):
        yield value
        return

    if isinstance(value, (int, float)):
        yield str(value)
        return

    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_exception_error_values(child, depth + 1)
        return

    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_exception_error_values(child, depth + 1)
        return


def _extract_exception_error_values(exc: BaseException) -> List[str]:
    values = [str(exc)]

    for attr in ("code", "status_code", "message", "body"):
        attr_value = getattr(exc, attr, None)
        values.extend(_iter_exception_error_values(attr_value))

    response = getattr(exc, "response", None)
    if response is not None:
        values.extend(
            _iter_exception_error_values(
                getattr(response, "status_code", None)
            )
        )
        json_fn = getattr(response, "json", None)
        if callable(json_fn):
            try:
                values.extend(_iter_exception_error_values(json_fn()))
            except (TypeError, ValueError):
                pass
        text = getattr(response, "text", None)
        values.extend(_iter_exception_error_values(text))

    return values


def is_image_modality_rejection(exc: BaseException) -> bool:
    """Return True if *exc* is a deterministic client-side rejection of the image."""
    seen: set[int] = set()
    values: list[str] = []
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.extend(_extract_exception_error_values(current))
        current = current.__cause__ or current.__context__

    lowered_values = [value.lower() for value in values if value]
    for value in lowered_values:
        normalized = value.replace("-", "_").replace(" ", "_")
        for code in _IMAGE_INPUT_UNSUPPORTED_ERROR_CODES:
            if normalized == code or normalized.endswith(f"_{code}"):
                return True

    text = "\n".join(lowered_values)
    for pattern in _IMAGE_INPUT_UNSUPPORTED_ERROR_PATTERNS:
        if pattern in text:
            return True

    return False


def probe_cache_key(llm) -> Optional[tuple[str, str]]:
    """Return the (api_base, model_name) cache key for *llm*, if resolvable."""
    client_config = getattr(llm, "model_client_config", None)
    model_config = getattr(llm, "model_config", None)
    model_name = str(getattr(model_config, "model_name", "") or "").strip()
    if not model_name:
        return None
    api_base = str(getattr(client_config, "api_base", "") or "").strip()
    return api_base, model_name


def get_cached_image_support(llm) -> Optional[bool]:
    """Return the cached probe verdict for *llm*, or None when not probed yet."""
    key = probe_cache_key(llm)
    if key is None:
        return None
    return _probe_results.get(key)


def reset_image_support_cache() -> None:
    """Drop cached verdicts and pending probes. For tests and reconfiguration."""
    _probe_results.clear()
    for task in list(_probe_tasks.values()):
        task.cancel()
    _probe_tasks.clear()


def schedule_image_support_probe(llm) -> None:
    """Probe *llm* in the background, at most once per (api_base, model_name).

    The caller is not meant to wait for the result: until the verdict lands,
    read_file stays in metadata-only mode, and the next agent built on the same
    endpoint and model picks the answer up from the cache.

    The probe is an ``asyncio`` task, so it lives and dies with the loop that
    scheduled it. A process that runs one ``asyncio.run`` and exits therefore
    never sees a verdict at all -- it stays metadata-only throughout. Such a
    caller should either set ``enable_read_image_multimodal`` explicitly or
    await :func:`probe_image_support` once during startup.

    Args:
        llm: The model to probe.
    """
    key = probe_cache_key(llm)
    if key is None or key in _probe_results or key in _probe_tasks:
        return

    try:
        task = asyncio.get_running_loop().create_task(_probe_and_cache(llm, key))
    except RuntimeError:
        # Configured outside a running loop; there is nothing to schedule onto
        # and nothing waiting on the verdict either.
        logger.debug(
            "[ImageModalityProbe] no running event loop; skipping background probe for %s",
            key,
        )
        return
    _probe_tasks[key] = task
    task.add_done_callback(lambda _task: _probe_tasks.pop(key, None))


async def _probe_and_cache(llm, key: tuple[str, str]) -> None:
    """Run one probe for *key* and cache a conclusive verdict."""
    # Cancellation needs no clause of its own: CancelledError derives from
    # BaseException, so the handler below never swallows it.
    try:
        supported = await _run_probe(llm)
    except Exception as exc:
        logger.warning("[ImageModalityProbe] background image modality probe failed: %s", exc)
        return
    if supported is None:
        return
    _probe_results[key] = supported
    logger.info(
        "[ImageModalityProbe] image modality probed: api_base=%s model=%s supported=%s",
        key[0],
        key[1],
        supported,
    )


async def _invoke_probe(llm, *, extra_body: Optional[dict]):
    """Send the probe request, optionally carrying reasoning-off switches."""
    kwargs = {"extra_body": extra_body} if extra_body else {}
    with LlmObservationSuppression():
        return await asyncio.wait_for(
            llm.invoke(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{DUMMY_IMAGE_B64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": "What color is this image? Reply with one word.",
                            },
                        ],
                    }
                ],
                max_tokens=_IMAGE_MODALITY_PROBE_MAX_TOKENS,
                temperature=0,
                **kwargs,
            ),
            timeout=_IMAGE_MODALITY_PROBE_TIMEOUT_SECONDS,
        )


async def _run_probe(llm) -> Optional[bool]:
    """Send the probe request and interpret its outcome."""
    try:
        response = await _invoke_probe(llm, extra_body=_THINKING_DISABLED_EXTRA_BODY)
    except asyncio.TimeoutError:
        logger.warning(
            "[ImageModalityProbe] image modality probe timed out after %.0fs; "
            "leaving image support undetermined",
            _IMAGE_MODALITY_PROBE_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:
        if is_image_modality_rejection(exc):
            logger.info(
                "[ImageModalityProbe] model rejected image input; treating read_file "
                "image multimodal as unsupported: %s",
                exc,
            )
            return False
        # A strict gateway rejects the reasoning-off switches themselves; retry
        # once with a plain request before giving up on the probe. Common
        # enough on gateways that validate their request body, so it is not
        # worth a warning.
        logger.debug(
            "[ImageModalityProbe] probe with reasoning disabled failed, retrying "
            "without vendor switches: %s",
            exc,
        )
        try:
            response = await _invoke_probe(llm, extra_body=None)
        except asyncio.TimeoutError:
            logger.warning(
                "[ImageModalityProbe] image modality probe timed out after %.0fs; "
                "leaving image support undetermined",
                _IMAGE_MODALITY_PROBE_TIMEOUT_SECONDS,
            )
            return None
        except Exception as retry_exc:
            if is_image_modality_rejection(retry_exc):
                logger.info(
                    "[ImageModalityProbe] model rejected image input; treating read_file "
                    "image multimodal as unsupported: %s",
                    retry_exc,
                )
                return False
            logger.warning(
                "[ImageModalityProbe] image modality probe call failed: %s",
                retry_exc,
            )
            return None

    return _interpret_probe_response(response)


def _interpret_probe_response(response) -> Optional[bool]:
    """Map a probe response to a verdict, or None when it says nothing.

    A verdict is cached for the rest of the process, so anything short of a
    real answer has to stay undetermined: caching ``False`` off a truncated or
    empty reply would leave a perfectly capable model image-blind for good.

    Args:
        response: The assistant message the probe request returned.

    Returns:
        True when the model named the color it was shown in either its answer
        or reasoning, False when it answered without naming it, and None when
        the reply carries no verdict either way.
    """
    raw_content = getattr(response, "content", None)
    content = raw_content if isinstance(raw_content, str) else ""
    raw_reasoning = getattr(response, "reasoning_content", None)
    reasoning_content = raw_reasoning if isinstance(raw_reasoning, str) else ""
    response_text = "\n".join(part for part in (content, reasoning_content) if part)
    if "red" in response_text.lower():
        return True

    if not response_text.strip():
        # A reasoning model that spent the whole (tiny) budget thinking says
        # nothing about image support.
        logger.warning(
            "[ImageModalityProbe] image modality probe returned no content; "
            "leaving image support undetermined",
        )
        return None

    if getattr(response, "finish_reason", None) == _LENGTH_FINISH_REASON:
        # Cut off mid-answer: the color may well have been the next word.
        logger.warning(
            "[ImageModalityProbe] image modality probe answer hit the %s token budget "
            "before naming a color; leaving image support undetermined",
            _IMAGE_MODALITY_PROBE_MAX_TOKENS,
        )
        return None

    return False


async def probe_image_support(llm) -> Optional[bool]:
    """Detect whether *llm* accepts native image input, reusing the cache.

    Returns:
        True if the model named the color it was shown, False if it responded
        without naming it or deterministically rejected the image (e.g. a 404
        "no endpoints found that support image input"), and None if the result
        is inconclusive (timeout, another call failure such as auth / rate
        limit / 5xx, or the model answered nothing) and should not be cached.
    """
    key = probe_cache_key(llm)
    if key is not None and key in _probe_results:
        return _probe_results[key]

    supported = await _run_probe(llm)
    if supported is not None and key is not None:
        _probe_results[key] = supported
    return supported
