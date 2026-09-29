"""IM learning contracts for PersonalContext.

Host runtimes inject an ``ImLearningSource``. This package does not contain
CLI binaries or platform connectors.  The scheduler and corpus/FTS modules
implement the learning pipeline over a dedicated ``<home>/im/im_context.db``;
``ImSearchPort`` and its SQLite implementation provide read-only keyword
search over that corpus.
"""

from openjiuwen.harness.personal_context.im.config_targets import build_im_learning_targets
from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningTarget,
    ImMessageBatch,
)
from openjiuwen.harness.personal_context.im.scheduler import (
    ImLearningScheduler,
    ImLearningSchedulerStatus,
    open_im_context_db,
)
from openjiuwen.harness.personal_context.im.search import (
    ImSearchHit,
    ImSearchPort,
    ImSearchQuery,
)
from openjiuwen.harness.personal_context.im.search_tool import ImSearchTool
from openjiuwen.harness.personal_context.im.source import ImLearningSource
from openjiuwen.harness.personal_context.im.sqlite_search import SqliteImSearchStore

__all__ = [
    "ImLearningCursor",
    "ImLearningMessage",
    "ImLearningScheduler",
    "ImLearningSchedulerStatus",
    "ImLearningSource",
    "ImLearningTarget",
    "ImMessageBatch",
    "ImSearchHit",
    "ImSearchPort",
    "ImSearchQuery",
    "ImSearchTool",
    "SqliteImSearchStore",
    "build_im_learning_targets",
    "open_im_context_db",
]
