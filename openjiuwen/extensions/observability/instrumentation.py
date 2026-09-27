# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-wide OTel HTTP instrumentation for W3C trace-context propagation.

Single authority for the ``global_instrument_enable`` switch: it patches
``httpx`` / ``requests`` / ``aiohttp`` once per process so outbound requests
carry a valid ``traceparent`` header. Both tracer entry points reach it —
``ObservabilityRuntime.initialize`` after setting the global TracerProvider,
and ``extensions.tracer_otel.setup.init_otel_tracer`` through a provider
factory — so swarm-style and studio-style deployments behave identically.

Instrumentation requires a global SDK ``TracerProvider`` to be in place
first: the instrumentors resolve their tracer from the process-global
provider at ``instrument()`` time. ``ensure_global_http_instrumentation``
never installs one behind the caller's back unless a ``provider_factory``
was supplied for exactly that purpose.

The instrumentor dependencies are optional (``openjiuwen[otel-instrument]``);
a missing library downgrades to a ``RuntimeWarning`` and the process starts
normally.
"""

from __future__ import annotations

import importlib
import os
import threading
import warnings
from collections.abc import Callable

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.core.common.logging import logger

#: Environment variable overriding the config-file switch (highest priority).
ENV_FLAG = "OPENJIUWEN_OTEL_GLOBAL_INSTRUMENT_ENABLE"

_TRUTHY = {"1", "true", "yes", "on"}

# Import path -> human-readable library name, imported lazily so a missing
# optional dependency never breaks process startup.
_INSTRUMENTORS: dict[str, str] = {
    "opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor": "httpx",
    "opentelemetry.instrumentation.requests.RequestsInstrumentor": "requests",
    "opentelemetry.instrumentation.aiohttp_client.AioHttpClientInstrumentor": "aiohttp",
}

_lock = threading.Lock()
_instrumented = False


def resolve_global_instrument_flag(config_value: bool) -> bool:
    """Merge the environment flag with the configured value (env wins)."""

    raw = os.environ.get(ENV_FLAG)
    if raw is not None and raw.strip():
        return raw.strip().lower() in _TRUTHY
    return config_value


def ensure_global_http_instrumentation(
    provider_factory: Callable[[], TracerProvider] | None = None,
) -> bool:
    """Instrument the HTTP libraries once per process, provider first.

    Args:
        provider_factory: Called to build and register a global SDK provider
            when none is set yet. The factory must return a configured
            ``TracerProvider``; ``trace.set_tracer_provider`` stays here so
            global-state ownership never leaves this module.

    Returns:
        True when the HTTP libraries are (or already were) instrumented.
    """

    global _instrumented

    with _lock:
        if _instrumented:
            return True

        if not _ensure_sdk_provider(provider_factory):
            return False

        for import_path, library in _INSTRUMENTORS.items():
            _instrument_one(import_path, library)
        _instrumented = True
        return True


def _ensure_sdk_provider(provider_factory: Callable[[], TracerProvider] | None) -> bool:
    """Guarantee a global SDK provider exists before any instrument() call."""

    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return True

    if provider_factory is None:
        logger.warning(
            "otel: global HTTP instrumentation skipped - no global SDK TracerProvider "
            "(enable observability or supply a provider factory)"
        )
        return False

    try:
        provider = provider_factory()
        trace.set_tracer_provider(provider)
    except Exception as exc:
        logger.warning("otel: global HTTP instrumentation provider setup failed - {}", exc)
        return False
    return True


def _instrument_one(import_path: str, library: str) -> None:
    """Instrument one HTTP library; a missing optional dep is a warning only."""

    try:
        module_path, _, class_name = import_path.rpartition(".")
        module = importlib.import_module(module_path)
        instrumentor = getattr(module, class_name)()
    except ImportError as exc:
        message = f"otel: {library} instrumentation unavailable - install 'openjiuwen[otel-instrument]' ({exc})"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        logger.warning(message)
        return

    try:
        instrumentor.instrument()
        logger.info("otel: {} instrumentation enabled", library)
    except Exception as exc:
        # One library's failure must not block the others, nor startup.
        logger.warning("otel: {} instrumentation failed - {}", library, exc)


def reset_global_http_instrumentation_for_tests() -> None:
    """Reset the once-per-process latch (test isolation only)."""

    global _instrumented
    with _lock:
        _instrumented = False


__all__ = [
    "ENV_FLAG",
    "ensure_global_http_instrumentation",
    "resolve_global_instrument_flag",
]
