from __future__ import annotations

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
        source={"root_dir": str(root)},
        credentials={},
    )


def test_context_fetch_service_is_abstract_and_default_lifecycle_is_side_effect_free(tmp_path: Path):
    config = _config(tmp_path / "source")

    with pytest.raises(TypeError):
        ContextFetchService(config, home=tmp_path / "home")

    class ConcreteFetchService(ContextFetchService):
        async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
            del run_id, cursor
            if False:
                yield

    home = tmp_path / "home"
    service = ConcreteFetchService(config, home=home)

    # The default hooks do not create a runtime directory or cursor state.
    assert service._config is config
    assert service._home == home
    assert not home.exists()

    import asyncio

    asyncio.run(service.commit_run(run_id="run-a"))
    asyncio.run(service.abort_run(run_id="run-a"))
    assert not home.exists()
