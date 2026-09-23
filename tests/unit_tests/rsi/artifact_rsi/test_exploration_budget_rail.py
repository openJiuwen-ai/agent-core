"""Tests for ExplorationBudgetRail: nudges the coding agent to stop
exploring and attempt an implementation once too many tool calls have
passed with no write under output/. See the module docstring for the
repeated failure pattern (environment/SDK exploration burning an entire
attempt's iteration budget before run.py ever gets written) this addresses.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.exploration_budget_rail import (
    ExplorationBudgetRail,
    _is_output_write,
)


def _tool_call_ctx(tool_name: str, tool_args):
    return SimpleNamespace(inputs=SimpleNamespace(tool_name=tool_name, tool_args=tool_args))


def test_is_output_write_true_for_dict_args_with_output_segment():
    assert _is_output_write("write_file", {"file_path": "C:/run/output/run.py"})


def test_is_output_write_true_for_windows_backslash_path():
    assert _is_output_write("write_file", {"file_path": r"C:\run\output\run.py"})


def test_is_output_write_false_for_scratch_path():
    assert not _is_output_write("write_file", {"file_path": "C:/run/agent_workspace/scratch/probe.py"})


def test_is_output_write_false_for_non_write_tool():
    assert not _is_output_write("bash", {"file_path": "C:/run/output/run.py"})


def test_is_output_write_handles_object_args():
    args = SimpleNamespace(file_path="output/run.py")
    assert _is_output_write("edit_file", args)


@pytest.mark.asyncio
async def test_after_tool_call_increments_for_non_output_write():
    rail = ExplorationBudgetRail(threshold=25)
    await rail.after_tool_call(_tool_call_ctx("bash", {"command": "ls"}))
    await rail.after_tool_call(_tool_call_ctx("write_file", {"file_path": "scratch/probe.py"}))
    assert rail._calls_since_write == 2


@pytest.mark.asyncio
async def test_after_tool_call_resets_on_output_write():
    rail = ExplorationBudgetRail(threshold=25)
    rail._calls_since_write = 10
    await rail.after_tool_call(_tool_call_ctx("write_file", {"file_path": "output/run.py"}))
    assert rail._calls_since_write == 0


@pytest.mark.asyncio
async def test_before_model_call_adds_section_once_threshold_reached():
    rail = ExplorationBudgetRail(threshold=3)
    rail.system_prompt_builder = MagicMock(language="cn")
    rail._calls_since_write = 3

    await rail.before_model_call(SimpleNamespace())

    rail.system_prompt_builder.add_section.assert_called_once()
    rail.system_prompt_builder.remove_section.assert_not_called()


@pytest.mark.asyncio
async def test_before_model_call_removes_section_below_threshold():
    rail = ExplorationBudgetRail(threshold=25)
    rail.system_prompt_builder = MagicMock(language="cn")
    rail._calls_since_write = 1

    await rail.before_model_call(SimpleNamespace())

    rail.system_prompt_builder.remove_section.assert_called_once_with("exploration_budget_warning")
    rail.system_prompt_builder.add_section.assert_not_called()


@pytest.mark.asyncio
async def test_before_model_call_noop_without_system_prompt_builder():
    rail = ExplorationBudgetRail(threshold=1)
    rail._calls_since_write = 5
    rail.system_prompt_builder = None

    await rail.before_model_call(SimpleNamespace())  # must not raise


def test_init_sets_system_prompt_builder_from_agent():
    rail = ExplorationBudgetRail()
    agent = SimpleNamespace(system_prompt_builder=MagicMock())

    rail.init(agent)

    assert rail.system_prompt_builder is agent.system_prompt_builder


def test_uninit_removes_section():
    rail = ExplorationBudgetRail()
    rail.system_prompt_builder = MagicMock()

    rail.uninit(SimpleNamespace())

    rail.system_prompt_builder.remove_section.assert_called_once_with("exploration_budget_warning")


def test_uninit_safe_without_system_prompt_builder():
    rail = ExplorationBudgetRail()
    rail.system_prompt_builder = None

    rail.uninit(SimpleNamespace())  # must not raise


def test_priority_is_90():
    assert ExplorationBudgetRail().priority == 90
