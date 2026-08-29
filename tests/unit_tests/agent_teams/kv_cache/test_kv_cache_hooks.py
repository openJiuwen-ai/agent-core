from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_hooks


class Session:
    def __init__(self) -> None:
        self.parent = None
        self.released = False

    def bind_parent_session_id(self, parent: str) -> None:
        self.parent = parent

    async def release_kvc(self) -> bool:
        self.released = True
        return True


class Harness:
    def __init__(self) -> None:
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=SimpleNamespace(
                enable_kv_cache_affinity=True,
            )
        )


@pytest.mark.asyncio
async def test_one_shot_hook_binds_parent_and_releases_session() -> None:
    harness = Harness()
    session = Session()

    assert kv_cache_hooks.configure_harness_session_hooks(
        harness,
        product_session_id="product",
        evict_on_finish=True,
    ) is True
    kv_cache_hooks.on_harness_session_created(harness, session)
    await kv_cache_hooks.after_harness_session_finished(harness, session)

    assert session.parent == "product"
    assert session.released is True
