# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Inner ReAct max_iterations: unconfigured is unbounded, configured takes effect."""

from __future__ import annotations

import sys
from unittest.mock import patch

from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
from openjiuwen.harness.deep_agent import _bind_inner_react_max_iterations
from openjiuwen.harness.schema.config import resolve_inner_react_max_iterations


def test_unconfigured_inner_react_max_iterations_is_unbounded() -> None:
    assert resolve_inner_react_max_iterations(None) == sys.maxsize


def test_configured_inner_react_max_iterations_is_used() -> None:
    assert resolve_inner_react_max_iterations(50) == 50


def test_bind_logs_unbounded_default() -> None:
    react_config = ReActAgentConfig()
    with patch("openjiuwen.harness.deep_agent.logger") as mock_logger:
        resolved = _bind_inner_react_max_iterations(react_config, None)

    assert resolved == sys.maxsize
    assert react_config.max_iterations == sys.maxsize
    mock_logger.info.assert_called_once_with(
        "[DeepAgent] inner ReAct max_iterations=unbounded (default)"
    )


def test_bind_logs_configured_value() -> None:
    react_config = ReActAgentConfig()
    with patch("openjiuwen.harness.deep_agent.logger") as mock_logger:
        resolved = _bind_inner_react_max_iterations(react_config, 42)

    assert resolved == 42
    assert react_config.max_iterations == 42
    mock_logger.info.assert_called_once_with(
        "[DeepAgent] inner ReAct max_iterations=%s (configured)",
        42,
    )
