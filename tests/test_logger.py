# coding: utf-8
"""Loguru-backed test logger using the project logging system.

Loads logging config from ``tests/log_config.yaml`` on first use, then
exposes a LazyLogger named ``logger`` for tests and example scripts.

Usage::

    from tests.test_logger import logger

    logger.info("something happened")
"""

from pathlib import Path

from openjiuwen.core.common.logging import LazyLogger
from openjiuwen.core.common.logging.log_config import configure_log
from openjiuwen.core.common.logging.manager import LogManager

_LOG_CONFIG_PATH = str(Path(__file__).parent / "log_config.yaml")

configure_log(_LOG_CONFIG_PATH)

# Test logger — follows the same LazyLogger pattern as core loggers.
logger = LazyLogger(lambda: LogManager.get_logger("test"))
