# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for ToolOptimizerBase - Tool dimension optimizer base class."""
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.base import ToolOptimizerBase
from openjiuwen.agent_evolving.optimizer.tool_call import base as tool_base


def test_tool_optimizer_base_init_and_default_targets(tmp_path):
    cfg_eg = {"x": 1}
    cfg_desc = {"y": 2}
    optimizer = ToolOptimizerBase(
        max_turns=2,
        llm_api_key="k",
        config_eg=cfg_eg,
        config_desc=cfg_desc,
        path_save_dir=str(tmp_path),
        tool_name="search",
    )
    assert optimizer.default_targets() == ["enabled", "max_retries"]
    assert optimizer.config_eg["save_dir"].endswith("examples")
    assert optimizer.config_desc["save_dir"].endswith("descriptions")
    assert optimizer.config_desc["examples_dir"].endswith("examples")
    assert optimizer.config_desc["neg_ex_input_path"].endswith("search.json")


def test_tool_optimizer_optimize_tool_with_mocks(monkeypatch, tmp_path):
    class _FakeReviewer:
        def __init__(self, eval_model_id, llm_api_key):
            self.eval_model_id = eval_model_id
            self.llm_api_key = llm_api_key
            self.calls = []

        def process(self, data, ori_tool, steps):
            self.calls.append((data, ori_tool, steps))
            return {"processed": data, "ori": ori_tool}

        @staticmethod
        def format(schema, processed, example=None):
            return {"schema": schema, "processed": processed}

    desc_iter = iter(
        [
            [[{"description": "desc-1"}]],
            [[{"description": "desc-2"}]],
        ]
    )

    def fake_pipeline(stage, tool, tool_callable=None, config=None):
        if stage == "example":
            return [{"example": True}]
        return next(desc_iter)

    monkeypatch.setattr(tool_base, "customized_pipeline", fake_pipeline)
    monkeypatch.setattr(tool_base, "extract_schema", lambda ori_desc: {"name": ""})
    monkeypatch.setattr(tool_base, "ToolDescriptionReviewer", _FakeReviewer)

    optimizer = ToolOptimizerBase(
        max_turns=2,
        llm_api_key="api-key",
        config_eg=deepcopy({"eval_model_id": "x"}),
        config_desc=deepcopy({"eval_model_id": "eval-m"}),
        path_save_dir=str(tmp_path),
        tool_name="search",
    )
    tool = {"name": "search", "description": '{"name":"search"}'}
    out = optimizer.optimize_tool(tool, tool_callable=lambda x: x)
    assert out["schema"] == {"name": ""}
    assert out["processed"]["processed"] == "desc-2"


def make_mock_tool_operator(tunables=None, op_id="tool_op"):
    """Factory for creating mock Tool operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_tunables.return_value = tunables or {"enabled": True, "max_retries": 3}
    op.get_state.return_value = {"enabled": True, "max_retries": 3}
    return op


class TestToolOptimizerBaseInit:
    """Test ToolOptimizerBase initialization."""

    @staticmethod
    def test_domain_is_tool():
        """Domain is 'tool'."""
        optimizer = ToolOptimizerBase()
        assert optimizer.domain == "tool"

    @staticmethod
    def test_default_targets_enabled_and_max_retries():
        """Default targets are enabled and max_retries."""
        optimizer = ToolOptimizerBase()
        targets = optimizer.default_targets()
        assert targets == ["enabled", "max_retries"]


class TestToolOptimizerBaseFilterOperators:
    """Test filter_operators() method."""

    @staticmethod
    def test_filter_matches_tool_targets():
        """Filter operators with enabled and max_retries tunables."""
        optimizer = ToolOptimizerBase()
        op1 = make_mock_tool_operator({"enabled": True, "max_retries": 3})
        op2 = make_mock_tool_operator({"system_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["enabled", "max_retries"])

        assert "op1" in result
        assert "op2" not in result

    @staticmethod
    def test_filter_empty_targets():
        """Filter with empty targets returns empty dict."""
        optimizer = ToolOptimizerBase()
        operators = {"op1": make_mock_tool_operator()}

        result = optimizer.filter_operators(operators, [])

        assert result == {}

    @staticmethod
    def test_filter_skips_no_tunables():
        """Skip operators with no tunables."""
        optimizer = ToolOptimizerBase()
        op = make_mock_tool_operator()
        op.get_tunables.return_value = {}
        operators = {"op1": op}

        result = optimizer.filter_operators(operators, targets=["enabled", "max_retries"])

        assert result == {}

    @staticmethod
    def test_filter_multiple_tool_operators():
        """Filter multiple operators with enabled and max_retries."""
        optimizer = ToolOptimizerBase()
        op1 = make_mock_tool_operator({"enabled": True, "max_retries": 3}, op_id="tool1")
        op2 = make_mock_tool_operator({"enabled": False, "max_retries": 5}, op_id="tool2")
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["enabled", "max_retries"])

        assert "op1" in result
        assert "op2" in result


class TestToolOptimizerBaseBind:
    """Test bind() method via public API."""

    @staticmethod
    def test_bind_with_tool_operators():
        """Bind Tool operators matching enabled and max_retries targets."""
        optimizer = ToolOptimizerBase()
        op1 = make_mock_tool_operator({"enabled": True, "max_retries": 3})
        op2 = make_mock_tool_operator({"system_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators)

        assert count == 1

    @staticmethod
    def test_bind_with_no_matching_operators():
        """Bind with no matching operators returns zero."""
        optimizer = ToolOptimizerBase()
        op = make_mock_tool_operator({"other": "value"})
        operators = {"op1": op}

        count = optimizer.bind(operators)

        assert count == 0

    @staticmethod
    def test_bind_with_multiple_matching():
        """Bind multiple matching operators."""
        optimizer = ToolOptimizerBase()
        op1 = make_mock_tool_operator({"enabled": True, "max_retries": 3}, op_id="tool1")
        op2 = make_mock_tool_operator({"enabled": False, "max_retries": 5}, op_id="tool2")
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators)

        assert count == 2


class TestToolOptimizerBaseDefaultTargets:
    """Test default_targets() behavior."""

    @staticmethod
    def test_default_targets_returns_list():
        """default_targets returns a list."""
        optimizer = ToolOptimizerBase()
        result = optimizer.default_targets()
        assert isinstance(result, list)

    @staticmethod
    def test_default_targets_contains_enabled():
        """default_targets contains 'enabled'."""
        optimizer = ToolOptimizerBase()
        result = optimizer.default_targets()
        assert "enabled" in result

    @staticmethod
    def test_default_targets_contains_max_retries():
        """default_targets contains 'max_retries'."""
        optimizer = ToolOptimizerBase()
        result = optimizer.default_targets()
        assert "max_retries" in result

    @staticmethod
    def test_default_targets_count():
        """default_targets has two targets."""
        optimizer = ToolOptimizerBase()
        result = optimizer.default_targets()
        assert len(result) == 2
