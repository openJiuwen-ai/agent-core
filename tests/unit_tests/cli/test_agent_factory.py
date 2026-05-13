# coding: utf-8
"""Tests for CLI agent factory rails."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.agent.factory import create_agent
from openjiuwen.harness.rails import ConfirmInterruptRail


def test_create_agent_only_requires_confirm_for_file_mutations() -> None:
    """CLI should not prompt for bash approval, only file writes/edits."""
    captured: dict[str, object] = {}

    def _fake_create_deep_agent(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            deep_config=SimpleNamespace(
                workspace=SimpleNamespace(root_path="")
            )
        )

    cfg = CLIConfig(
        api_key="test-key",
        workspace="/tmp/openjiuwen-cli-test",
    )

    with patch(
        "openjiuwen.harness.cli.agent.factory.init_model",
        return_value=object(),
    ), patch(
        "openjiuwen.harness.cli.agent.factory.build_system_prompt",
        return_value="system prompt",
    ), patch(
        "openjiuwen.harness.cli.agent.factory._build_memory_rail",
        return_value=None,
    ), patch(
        "openjiuwen.harness.cli.agent.factory._load_mcp_configs",
        return_value=[],
    ), patch(
        "openjiuwen.harness.cli.agent.factory._load_vision_config",
        return_value=None,
    ), patch(
        "openjiuwen.harness.cli.agent.factory._load_audio_config",
        return_value=None,
    ), patch(
        "openjiuwen.harness.cli.agent.factory._build_subagents",
        return_value=[],
    ), patch(
        "openjiuwen.harness.cli.agent.factory.create_web_tools",
        return_value=[],
    ), patch(
        "openjiuwen.harness.cli.agent.factory.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_agent(cfg)

    rails = captured["rails"]
    confirm_rails = [
        rail for rail in rails
        if isinstance(rail, ConfirmInterruptRail)
    ]
    assert len(confirm_rails) == 1
    assert confirm_rails[0].get_tools() == {
        "write_file",
        "edit_file",
    }
