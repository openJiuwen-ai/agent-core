"""Logging for CLI entry points: progress goes to stderr while the rendered result stays on stdout.

Each logger is private and bound to the current stream at creation time, so running a CLI does not change the
host application's logging configuration.
"""

import logging
import sys
from typing import TextIO


def _cli_logger(name: str, stream: TextIO) -> logging.Logger:
    """Create a CLI-owned logger writing bare messages to one stream, with no root-handler propagation."""
    logger = logging.Logger(name, level=logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def progress_logger() -> logging.Logger:
    """Create a CLI-owned logger bound to the current stderr."""
    return _cli_logger("reward.cli", sys.stderr)


def result_logger() -> logging.Logger:
    """Create a CLI-owned logger bound to the current stdout for the rendered result."""
    return _cli_logger("reward.result", sys.stdout)
