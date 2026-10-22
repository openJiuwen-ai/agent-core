"""IT-01: Agent factory integration tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.rails.token_tracker import TokenTrackingRail


class TestAgentFactory:
    """Test create_agent() assembles the agent correctly."""

    @pytest.mark.asyncio
    async def test_create_agent_returns_tuple(self) -> None:
        """create_agent returns (agent, tracker) tuple."""
        cfg = CLIConfig(api_key="test-key", model="gpt-4o")
        with patch(
            "openjiuwen.harness.cli.agent.factory.init_model"
        ):
            with patch(
                "openjiuwen.harness.cli.agent.factory.create_deep_agent"
            ) as mock_create:
                mock_create.return_value = MagicMock()
                from openjiuwen.harness.cli.agent.factory import (
                    create_agent,
                )

                agent, tracker = create_agent(cfg)
                assert agent is not None
                assert isinstance(tracker, TokenTrackingRail)

    @pytest.mark.asyncio
    async def test_create_agent_passes_correct_params(
        self,
    ) -> None:
        """Factory forwards correct params to create_deep_agent."""
        cfg = CLIConfig(
            api_key="key",
            model="qwen-max",
            max_iterations=50,
        )
        with patch(
            "openjiuwen.harness.cli.agent.factory.init_model"
        ):
            with patch(
                "openjiuwen.harness.cli.agent.factory.create_deep_agent"
            ) as mock_create:
                mock_create.return_value = MagicMock()
                from openjiuwen.harness.cli.agent.factory import (
                    create_agent,
                )

                create_agent(cfg)
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["enable_task_loop"] is True
                assert call_kwargs["max_iterations"] == 50
                assert call_kwargs["language"] == "en"

    @pytest.mark.asyncio
    async def test_create_agent_mounts_jiuwen_memory_rail_when_enabled(
        self,
        monkeypatch,
    ) -> None:
        """JIUWEN_MEMORY_ENABLED=1 puts JiuwenMemoryRail on the rails list."""
        monkeypatch.setenv("JIUWEN_MEMORY_ENABLED", "1")
        monkeypatch.setenv("JIUWEN_MEMORY_BASE_URL", "http://127.0.0.1:9999")
        cfg = CLIConfig(api_key="key", model="gpt-4o")

        with patch(
            "openjiuwen.harness.cli.agent.factory.init_model"
        ):
            with patch(
                "openjiuwen.harness.cli.agent.factory.create_deep_agent"
            ) as mock_create:
                mock_create.return_value = MagicMock()
                from openjiuwen.harness.cli.agent.factory import (
                    create_agent,
                )
                from openjiuwen.harness.rails.memory.jiuwen_memory_rail import (
                    JiuwenMemoryRail,
                )

                create_agent(cfg)
                rails = mock_create.call_args.kwargs["rails"]
                mounted = [r for r in rails if isinstance(r, JiuwenMemoryRail)]
                assert len(mounted) == 1
                assert mounted[0]._provider._base_url == (
                    "http://127.0.0.1:9999"
                )

    @pytest.mark.asyncio
    async def test_create_agent_skips_jiuwen_memory_rail_when_disabled(
        self,
        monkeypatch,
    ) -> None:
        """Default (switch unset) keeps JiuwenMemoryRail off the rails list."""
        monkeypatch.delenv("JIUWEN_MEMORY_ENABLED", raising=False)
        cfg = CLIConfig(api_key="key", model="gpt-4o")

        with patch(
            "openjiuwen.harness.cli.agent.factory.init_model"
        ):
            with patch(
                "openjiuwen.harness.cli.agent.factory.create_deep_agent"
            ) as mock_create:
                mock_create.return_value = MagicMock()
                from openjiuwen.harness.cli.agent.factory import (
                    create_agent,
                )
                from openjiuwen.harness.rails.memory.jiuwen_memory_rail import (
                    JiuwenMemoryRail,
                )

                create_agent(cfg)
                rails = mock_create.call_args.kwargs["rails"]
                assert not any(
                    isinstance(r, JiuwenMemoryRail) for r in rails
                )
