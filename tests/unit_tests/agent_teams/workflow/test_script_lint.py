# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool
from openjiuwen.agent_teams.workflow.engine.errors import MetaError


_OLD = 'META = {"name": "abc", "phases": []}\nasync def run(args): pass\n'

def _make_tool():
    return object.__new__(SwarmflowTool)


def test_lint_blocks_meta_name_change():
    tool = _make_tool()
    new_src = 'META = {"name": "xyz", "phases": []}\nasync def run(args): pass\n'
    with pytest.raises(MetaError):
        tool._lint_rerun(old_source=_OLD, new_source=new_src)


def test_lint_passes_unchanged_name():
    tool = _make_tool()
    new_src = 'META = {"name": "abc", "phases": []}\nasync def run(args):\n    await agent("x")\n'
    # 不抛即通过
    tool._lint_rerun(old_source=_OLD, new_source=new_src)


def test_structural_diff_detects_agent_add():
    tool = _make_tool()
    old = 'META = {"name": "abc"}\nasync def run(args):\n    await agent("a")\n'
    # Structural diff keys on await-call node names, so the added node must
    # introduce a NEW callable name (a second `agent` call would be invisible).
    new = 'META = {"name": "abc"}\nasync def run(args):\n    await agent("a")\n    await parallel()\n'
    diff = tool._compute_structural_diff(old, new)
    assert diff.changed_nodes  # 有结构变更


def test_invoke_rerun_lint_blocks_meta_name_change_and_releases_ticket(tmp_path):
    from openjiuwen.agent_teams.workflow.concurrency import (
        ConcurrencyGovernor,
        ConcurrencyLimits,
    )

    old_script = tmp_path / "old_flow.py"
    old_script.write_text(_OLD, encoding="utf-8")

    tool = object.__new__(SwarmflowTool)
    tool._governor = ConcurrencyGovernor(ConcurrencyLimits(), agents_per_run_cap=1)

    out = asyncio.run(
        tool.invoke(
            {
                "script_path": str(old_script),
                "script": 'META = {"name": "xyz", "phases": []}\nasync def run(args): pass\n',
            }
        )
    )
    assert out.success is False
    assert "META.name" in (out.error or "")
    # 拒绝后不泄漏 L1 额度
    assert tool._governor.snapshot().active_workflows == 0
