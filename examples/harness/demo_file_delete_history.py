# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end demo: file operation history tracking including delete.

Validates that agent_id and session_id are correctly retrieved through the
real BashTool / AgentSession interface (not mocks).

Two phases:
  Phase 1 — path check
    Creates a real AgentSession and BashTool, prints the actual agent_id,
    session_id, and history path built by _build_history_path(session).

  Phase 2 — delete recording
    Pre-populates write/edit history at that real path, then exercises all
    five delete scenarios using the helper functions (rm explicit, rm glob,
    rm -rf, PS Remove-Item explicit, PS Remove-Item wildcard).

JSON output: _demo_workspace/.agent_history/file_ops_<agent_id>_<session_id>.json

Run:
    python examples/harness/demo_file_delete_history.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation.cwd import set_workspace
from openjiuwen.harness.tools import BashTool
from openjiuwen.harness.tools.filesystem import (
    _append_op_history,
    _detect_and_record_deletions,
    _parse_ps_remove_targets,
    _parse_rm_targets,
    _record_rm_targets_before_deletion,
)

WORKSPACE = os.path.join(os.path.dirname(__file__), "_demo_workspace")
AGENT_ID = "demo_agent"
SESSION_ID = "demo_session_001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_operation():
    """Minimal mock SysOperation backed by real files."""

    async def _read_file(path, mode="text", **_kwargs):
        res = MagicMock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            res.code = StatusCode.SUCCESS.code
            res.data = MagicMock()
            res.data.content = content
        except OSError as e:
            res.code = -1
            res.message = str(e)
            res.data = None
        return res

    mock_fs = MagicMock()
    mock_fs.read_file = AsyncMock(side_effect=_read_file)
    mock_op = MagicMock()
    mock_op.fs = MagicMock(return_value=mock_fs)
    return mock_op


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _print_history(history_path: str) -> None:
    if not os.path.exists(history_path):
        print("  (history file not found)")
        return
    with open(history_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for file_path, entries in data.items():
        print(f"\n  {os.path.basename(file_path)}")
        for i, e in enumerate(entries, 1):
            preview = (e.get("new_content") or e.get("old_content") or "")[:50].replace("\n", "\\n")
            print(f"    [{i}] {e['action']:<6}  content={preview!r}")


def _assert_actions(history_path: str, file_path: str, expected: list[str]) -> None:
    with open(history_path) as f:
        data = json.load(f)
    actual = [e["action"] for e in data[file_path]]
    assert actual == expected, f"{os.path.basename(file_path)}: expected {expected}, got {actual}"


# ---------------------------------------------------------------------------
# Phase 1: verify agent_id and session_id are accessible
# ---------------------------------------------------------------------------

def phase1_check_ids(bash_tool: BashTool, session) -> str:
    """Print and verify agent_id / session_id are correctly retrieved."""
    print("=" * 65)
    print("Phase 1 — agent_id / session_id retrieval check")
    print("=" * 65)

    actual_agent_id = session.get_agent_id()
    actual_session_id = session.get_session_id()
    history_path = bash_tool._build_history_path(session)

    print(f"  agent_id   = {actual_agent_id!r}")
    print(f"  session_id = {actual_session_id!r}")
    print(f"  history    = {history_path}")

    assert actual_agent_id == AGENT_ID, f"agent_id mismatch: {actual_agent_id!r}"
    assert actual_session_id == SESSION_ID, f"session_id mismatch: {actual_session_id!r}"
    assert AGENT_ID in history_path
    assert SESSION_ID in history_path
    assert history_path.endswith(f"file_ops_{AGENT_ID}_{SESSION_ID}.json")

    print("  [OK] agent_id 和 session_id 均正确取到\n")
    return history_path


# ---------------------------------------------------------------------------
# Phase 2: delete recording scenarios
# ---------------------------------------------------------------------------

async def scenario_bash_explicit(history_path: str, op) -> str:
    path = os.path.join(WORKSPACE, "hello.py")
    v1 = 'def hello():\n    print("hello")\n'
    v2 = 'def hello() -> str:\n    return "hello"\n'

    _write(path, v1)
    await _append_op_history(history_path, path, "write", None, v1)
    _write(path, v2)
    await _append_op_history(history_path, path, "edit", v1, v2)

    targets = _parse_rm_targets(f"rm {path}")
    print(f"    _parse_rm_targets: {[os.path.basename(t) for t in targets]}")
    await _record_rm_targets_before_deletion(history_path, targets, op)
    os.remove(path)
    await _detect_and_record_deletions(history_path)

    _assert_actions(history_path, path, ["write", "edit", "delete"])
    last = json.load(open(history_path))[path][-1]
    assert last["old_content"] == v2 and last["new_content"] is None
    delete_count = sum(1 for e in json.load(open(history_path))[path] if e["action"] == "delete")
    assert delete_count == 1, "delete must appear exactly once"
    return path


async def scenario_bash_glob(history_path: str) -> str:
    path = os.path.join(WORKSPACE, "utils.py")
    content = "def helper(): pass\n"

    _write(path, content)
    await _append_op_history(history_path, path, "write", None, content)

    targets = _parse_rm_targets("rm *.py")
    print(f"    _parse_rm_targets('rm *.py'): {targets}  ← empty (glob skipped)")
    assert targets == []

    os.remove(path)
    await _detect_and_record_deletions(history_path)

    _assert_actions(history_path, path, ["write", "delete"])
    return path


async def scenario_bash_recursive(history_path: str) -> str:
    subdir = os.path.join(WORKSPACE, "pkg")
    path = os.path.join(subdir, "core.py")
    content = "class Core: pass\n"

    _write(path, content)
    await _append_op_history(history_path, path, "write", None, content)

    targets = _parse_rm_targets(f"rm -rf {subdir}")
    print(f"    _parse_rm_targets('rm -rf dir'): {targets}  ← empty (-r skipped)")
    assert targets == []

    shutil.rmtree(subdir)
    await _detect_and_record_deletions(history_path)

    _assert_actions(history_path, path, ["write", "delete"])
    return path


async def scenario_ps_explicit(history_path: str, op) -> str:
    path = os.path.join(WORKSPACE, "config.ps1")
    content = '$setting = "value"\n'

    _write(path, content)
    await _append_op_history(history_path, path, "write", None, content)

    targets = _parse_ps_remove_targets(f"Remove-Item {path}")
    print(f"    _parse_ps_remove_targets: {[os.path.basename(t) for t in targets]}")
    await _record_rm_targets_before_deletion(history_path, targets, op)
    os.remove(path)
    await _detect_and_record_deletions(history_path)

    _assert_actions(history_path, path, ["write", "delete"])
    delete_count = sum(1 for e in json.load(open(history_path))[path] if e["action"] == "delete")
    assert delete_count == 1
    return path


async def scenario_ps_wildcard(history_path: str) -> str:
    path = os.path.join(WORKSPACE, "setup.ps1")
    content = "Write-Host 'setup'\n"

    _write(path, content)
    await _append_op_history(history_path, path, "write", None, content)

    targets = _parse_ps_remove_targets("Remove-Item *.ps1")
    print(f"    _parse_ps_remove_targets('Remove-Item *.ps1'): {targets}  ← empty (wildcard skipped)")
    assert targets == []

    os.remove(path)
    await _detect_and_record_deletions(history_path)

    _assert_actions(history_path, path, ["write", "delete"])
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    if os.path.exists(WORKSPACE):
        shutil.rmtree(WORKSPACE)
    os.makedirs(WORKSPACE)

    # ── real session and tool (no Runner needed) ──────────────────
    set_workspace(WORKSPACE)
    session = create_agent_session(session_id=SESSION_ID, card=AgentCard(id=AGENT_ID, name=AGENT_ID))
    op = _make_operation()
    bash_tool = BashTool(op)

    # Phase 1: verify IDs are retrievable
    history_path = phase1_check_ids(bash_tool, session)

    # Phase 2: delete recording with all scenarios
    print("=" * 65)
    print("Phase 2 — delete recording scenarios")
    print("=" * 65)
    print(f"[JSON路径] {history_path}\n")

    scenarios = [
        ("bash rm explicit file    ", lambda h: scenario_bash_explicit(h, op)),
        ("bash rm *.py (glob)      ", scenario_bash_glob),
        ("bash rm -rf dir/         ", scenario_bash_recursive),
        ("PowerShell Remove-Item   ", lambda h: scenario_ps_explicit(h, op)),
        ("PowerShell Remove-Item * ", scenario_ps_wildcard),
    ]

    for label, fn in scenarios:
        print(f"── {label} ──")
        try:
            await fn(history_path)
            print(f"    [OK]\n")
        except Exception as e:
            print(f"    [FAIL] {e}\n")
            raise

    print("=" * 65)
    print("[Result] Full history JSON:")
    _print_history(history_path)

    print(f"\n[完成] JSON已保存至:\n  {history_path}")


if __name__ == "__main__":
    asyncio.run(main())
