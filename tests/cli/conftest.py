"""Shared fixtures for CLI tests."""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.harness.cli.agent.config import CLIConfig


def _bootstrap_sdk_logging() -> None:
    """Initialise the SDK LogManager with a no-op config.

    Many SDK modules trigger ``LogManager.initialize()`` or
    ``LogManager.get_logger()`` at import time.  In test environments
    the ``extensions.common.configs.log_config`` entry-point is not
    installed, causing ``RuntimeError``.

    This helper monkey-patches ``get_logger`` so it returns a
    stdlib-backed fallback logger when the proper config is absent.
    """
    try:
        import logging

        from openjiuwen.core.common.logging import manager as log_mgr

        LogManager = log_mgr.LogManager  # noqa: N806

        class _StdlibFallback:
            """Minimal LoggerProtocol backed by stdlib logging."""

            def __init__(
                self, log_type: str, config: Any = None
            ) -> None:
                self._logger = logging.getLogger(
                    f"openjiuwen.{log_type}"
                )

            def __getattr__(self, name: str) -> Any:
                return getattr(self._logger, name)

            def logger(self) -> logging.Logger:
                return self._logger

        _original_get_logger = LogManager.get_logger.__func__

        @classmethod  # type: ignore[misc]
        def _safe_get_logger(
            cls: type, log_type: str = "default"
        ) -> Any:
            try:
                return _original_get_logger(cls, log_type)
            except RuntimeError:
                if log_type not in cls._loggers:
                    cls._loggers[log_type] = _StdlibFallback(
                        log_type
                    )
                return cls._loggers[log_type]

        LogManager.get_logger = _safe_get_logger
        LogManager._initialized = True
    except Exception:  # noqa: BLE001
        pass


# Run once at conftest load time so SDK module imports succeed.
_bootstrap_sdk_logging()


@pytest.fixture
def cli_config() -> CLIConfig:
    """Return a :class:`CLIConfig` with a mock API key."""
    return CLIConfig(api_key="mock-api-key")


@pytest.fixture
def cli_config_no_key() -> CLIConfig:
    """Return a :class:`CLIConfig` without an API key."""
    return CLIConfig(api_key="")
