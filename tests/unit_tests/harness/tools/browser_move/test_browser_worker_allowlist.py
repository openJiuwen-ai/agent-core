# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for installing capability allowlists on Playwright workers."""

from unittest.mock import MagicMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import (
    build_browser_worker_agent,
)


def _mcp_config() -> McpServerConfig:
    return McpServerConfig(
        server_id="playwright_official_stdio",
        server_name="playwright-official",
        server_path="stdio://playwright",
        client_type="stdio",
    )


def _build_with_mocked_agent(
    config: McpServerConfig,
    allowed_tool_names: tuple[str, ...] | None,
) -> MagicMock:
    worker = MagicMock()
    worker.configure.return_value = worker
    with patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.agents.ReActAgent",
        return_value=worker,
    ):
        result = build_browser_worker_agent(
            provider="openai",
            api_key="key",
            api_base="https://example.invalid/v1",
            model_name="model",
            mcp_cfg=config,
            max_steps=3,
            allowed_tool_names=allowed_tool_names,
        )
    assert result is worker
    return worker


def test_worker_installs_task_allowlist_for_playwright_server() -> None:
    config = _mcp_config()
    allowed = ("browser_click", "browser_pdf_save")

    worker = _build_with_mocked_agent(config, allowed)

    worker.ability_manager.add.assert_called_once_with(config)
    worker.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        config,
        allowed,
    )


def test_worker_with_none_allowlist_preserves_legacy_unrestricted_mode() -> None:
    config = _mcp_config()

    worker = _build_with_mocked_agent(config, None)

    worker.ability_manager.add.assert_called_once_with(config)
    worker.ability_manager.set_mcp_tool_allowlist.assert_not_called()
