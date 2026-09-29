"""Host-injected read interface for IM learning."""

from __future__ import annotations

from typing import Protocol

from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningTarget,
    ImMessageBatch,
)


class ImLearningSource(Protocol):
    """Read-only message source. Implementations live in the host, not here."""

    async def fetch_messages(
        self,
        target: ImLearningTarget,
        cursor: ImLearningCursor | None = None,
    ) -> ImMessageBatch:
        """Fetch one page of normalized messages for ``target``."""
        ...
