"""Output of the command-line scripts, written through the logging module.

The scripts' stdout is their product (the report, the summary JSON) and their stderr is the progress. Neither
belongs in the framework's own log (whose destination the host configures), so each stream gets a standalone
logger: not registered globally, not propagated, one handler on the stream, the message as it is.
"""

import logging
from collections.abc import Callable
from typing import TextIO


def line_writer(stream: TextIO) -> Callable[[str], None]:
    """A function that writes one line to `stream` (flushed at once), the way print() would."""
    writer = logging.Logger("openjiuwen.agent_evolving.reward.console")   # standalone: no handler pile-up on reuse
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    writer.addHandler(handler)
    writer.setLevel(logging.INFO)
    return writer.info
