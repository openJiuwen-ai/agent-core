from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService


def _config(root: Path) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="notes",
        provider="local_files",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=None,
        time_range={"mode": "all"},
        source={"root_dir": str(root)},
        credentials={},
    )


def test_context_fetch_service_is_abstract_and_default_lifecycle_is_side_effect_free(tmp_path: Path):
    config = _config(tmp_path / "source")

    with pytest.raises(TypeError):
        ContextFetchService(config, home=tmp_path / "home")

    class FetchOnlyService(ContextFetchService):
        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            if False:
                yield

    with pytest.raises(TypeError):
        FetchOnlyService(config, home=tmp_path / "home")

    class ConcreteFetchService(FetchOnlyService):
        async def prepare_run(
            self,
            *,
            run_id: str,
            run_started_at: datetime,
            cursor: dict[str, object] | None,
        ) -> tuple[dict[str, object], ...]:
            del run_id, run_started_at, cursor
            return ()

    home = tmp_path / "home"
    service = ConcreteFetchService(config, home=home)

    # The default hooks do not create a runtime directory or cursor state.
    assert service._config is config
    assert service._home == home
    assert not home.exists()

    assert (
        asyncio.run(
            service.prepare_run(
                run_id="run-a",
                run_started_at=datetime.now(UTC),
                cursor=None,
            )
        )
        == ()
    )
    asyncio.run(service.commit_run(run_id="run-a"))
    asyncio.run(service.abort_run(run_id="run-a"))
    assert not home.exists()
