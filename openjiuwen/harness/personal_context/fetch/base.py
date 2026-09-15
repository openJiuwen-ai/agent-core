"""Common contract for embedded PersonalContext fetch providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.models import FetchBatch


class ContextFetchService(ABC):
    """Base class for one configured personal-context source."""

    def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
        self._config = config
        self._home = home

    @abstractmethod
    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        """Return the complete in-memory candidate list for one run."""

        del run_id, run_started_at, cursor
        return ()

    @abstractmethod
    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        """Yield complete batches in prepared-candidate order.

        Every non-empty batch must consume the next contiguous candidate
        prefix.  Core uses that boundary to retain completed batches and
        advance only their matching cursor when an active run is stopped.
        """

        del run_id, cursor, candidates
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
