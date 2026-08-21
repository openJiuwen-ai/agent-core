from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.memory.lite.config import MemorySettings
from openjiuwen.core.memory.lite.manager import MemoryIndexManager
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.lite.memory_tool_ops import memory_search_with_context


class _Workspace:
    def get_node_path(self, _name):
        return None


class _Provider:
    id = "test"
    model = "test-model"
    config_fingerprint = "test:fingerprint"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def embed_query(self, _text):
        if self.error:
            raise self.error
        return self.result


def _manager():
    manager = MemoryIndexManager("test", _Workspace(), MemorySettings())
    manager.embedding_config = SimpleNamespace(api_key="key")
    return manager


@pytest.mark.asyncio
async def test_embedding_provider_is_disabled_when_health_check_fails(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(
        "openjiuwen.core.memory.lite.manager.create_embedding_provider",
        AsyncMock(return_value=_Provider(error=RuntimeError("model not found"))),
    )

    await manager._initialize_provider()

    assert manager.provider is None
    assert manager.embedding_available is False
    assert manager.embedding_error == "embedding validation failed: RuntimeError"


@pytest.mark.asyncio
async def test_embedding_provider_is_available_after_successful_health_check(monkeypatch):
    manager = _manager()
    provider = _Provider(result=[0.1, 0.2])
    monkeypatch.setattr(
        "openjiuwen.core.memory.lite.manager.create_embedding_provider",
        AsyncMock(return_value=provider),
    )

    await manager._initialize_provider()

    assert manager.provider is provider
    assert manager.embedding_available is True
    assert manager.embedding_error is None


@pytest.mark.asyncio
async def test_runtime_embedding_failure_switches_manager_to_keyword_only():
    manager = _manager()
    manager.provider = _Provider(error=RuntimeError("service unavailable"))
    manager.embedding_available = True

    result = await manager._embed_query_with_timeout("find earlier work")

    assert result == []
    assert manager.provider is None
    assert manager.embedding_available is False
    assert manager.embedding_error == "embedding request failed: RuntimeError"


@pytest.mark.asyncio
async def test_memory_search_exposes_keyword_only_state_when_embedding_is_unavailable():
    class _Manager:
        closed = False

        async def search(self, _query, opts=None):
            return []

        def status(self):
            return {
                "provider": None,
                "model": None,
                "embedding": {"available": False, "error": "embedding request timed out"},
            }

    ctx = MemoryToolContext(manager=_Manager())

    result = await memory_search_with_context(ctx, "remember this")

    assert result["disabled"] is False
    assert result["search_mode"] == "keyword_only"
    assert result["embedding_available"] is False
    assert result["embedding_error"] == "embedding request timed out"
