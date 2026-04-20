"""IT-02: LocalBackend integration tests."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.cli.agent.config import CLIConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeChunk:
    """Minimal OutputSchema stand-in."""

    def __init__(
        self, type: str, index: int = 0, payload: Any = None
    ) -> None:
        self.type = type
        self.index = index
        self.payload = payload or {}


async def _async_iter(
    items: list[Any],
) -> AsyncIterator[Any]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLocalBackend:
    """Test LocalBackend lifecycle and streaming."""

    @pytest.mark.asyncio
    async def test_start_initializes_agent(self) -> None:
        """start() creates agent and calls Runner.start."""
        with patch(
            "openjiuwen.harness.cli.agent.factory.create_agent"
        ) as mock_create:
            mock_create.return_value = (
                MagicMock(),
                MagicMock(),
            )
            with patch(
                "openjiuwen.harness.cli.agent.factory.Runner"
            ) as mock_runner:
                mock_runner.start = AsyncMock()
                from openjiuwen.harness.cli.agent.factory import (
                    LocalBackend,
                )

                cfg = CLIConfig(api_key="test")
                backend = LocalBackend(cfg)
                await backend.start()
                assert backend.agent is not None
                assert backend.tracker is not None

    @pytest.mark.asyncio
    async def test_run_streaming_yields_chunks(
        self,
    ) -> None:
        """run_streaming correctly forwards SDK chunks."""
        fake_chunks = [
            FakeChunk("llm_output", 0, {"content": "hi"}),
            FakeChunk("answer", 1, {"output": "done"}),
        ]
        with patch(
            "openjiuwen.harness.cli.agent.factory.Runner"
        ) as mock_runner:
            mock_runner.run_agent_streaming = MagicMock(
                return_value=_async_iter(fake_chunks)
            )
            from openjiuwen.harness.cli.agent.factory import (
                LocalBackend,
            )

            cfg = CLIConfig(api_key="test")
            backend = LocalBackend(cfg)
            backend.agent = MagicMock()

            results = []
            async for chunk in backend.run_streaming("test"):
                results.append(chunk)
            assert len(results) == 2
            assert results[0].type == "llm_output"
            assert results[1].type == "answer"

    @pytest.mark.asyncio
    async def test_abort_calls_agent_abort(self) -> None:
        """abort() delegates to agent.abort()."""
        from openjiuwen.harness.cli.agent.factory import LocalBackend

        cfg = CLIConfig(api_key="test")
        backend = LocalBackend(cfg)
        backend.agent = MagicMock()
        backend.agent.abort = AsyncMock()
        await backend.abort()
        backend.agent.abort.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_abort_without_agent(self) -> None:
        """abort() does not crash when agent is None."""
        from openjiuwen.harness.cli.agent.factory import LocalBackend

        cfg = CLIConfig(api_key="test")
        backend = LocalBackend(cfg)
        assert backend.agent is None
        await backend.abort()  # should not raise
