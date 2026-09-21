"""IM learning contracts for PersonalContext.

Host runtimes inject an ``ImLearningSource``. This package does not contain
CLI binaries or platform connectors.  The scheduler and corpus/FTS modules
implement OJ-02..OJ-05 over a dedicated ``<home>/im/im_context.db``.
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
from openjiuwen.harness.personal_context.im.source import ImLearningSource

__all__ = [
    "ImLearningCursor",
    "ImLearningMessage",
    "ImLearningScheduler",
    "ImLearningSchedulerStatus",
    "ImLearningSource",
    "ImLearningTarget",
    "ImMessageBatch",
    "build_im_learning_targets",
    "open_im_context_db",
]
