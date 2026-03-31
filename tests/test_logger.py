# coding: utf-8
"""Loguru-backed test logger using the project logging system.

Initializes LogManager with the loguru backend (DEFAULT_INNER_LOG_CONFIG)
on first use, then exposes a LazyLogger named ``logger`` for tests and
example scripts.

Usage::

    from tests.test_logger import logger

    logger.info("something happened")
"""

from openjiuwen.core.common.logging import LazyLogger
from openjiuwen.core.common.logging.log_config import configure_log_config
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.manager import LogManager

# Apply loguru backend config globally so LogManager creates LoguruLogger instances.
configure_log_config(DEFAULT_INNER_LOG_CONFIG)

# Test logger — follows the same LazyLogger pattern as core loggers.
logger = LazyLogger(lambda: LogManager.get_logger("test"))
