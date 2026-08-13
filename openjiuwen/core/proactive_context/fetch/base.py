"""Common contract for embedded proactive context fetch providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

from openjiuwen.core.proactive_context.config import PCSFetchServiceConfig
from openjiuwen.core.proactive_context.models import FetchBatch


class ContextFetchService(ABC):
    """Base class for one configured proactive-context source."""

    def __init__(self, config: PCSFetchServiceConfig, *, home: Path) -> None:
        self._config = config
        self._home = home

    @abstractmethod
    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
    ) -> AsyncIterator[FetchBatch]:
        """Yield the bounded batches for one fetch run."""

        if False:
            yield FetchBatch(batch_id="unreachable")

    async def commit_run(self, *, run_id: str) -> None:
        """Commit temporary provider state after the whole run succeeds."""

        del run_id
        return None

    async def abort_run(self, *, run_id: str) -> None:
        """Discard temporary provider state after the run is aborted."""

        del run_id
        return None
