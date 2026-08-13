# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A 线不管路径；B 线 file_guard；tools.write_file=allow 时路径 ask 只来自 B。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy


def test_a_ignores_path_approval_override() -> None:
    cfg = {
        "enabled": True,
        "tools": {"write_file": "ask"},
        "approval_overrides": [
            {
                "id": "user_allow_path",
                "tools": ["write_file"],
                "match_type": "path",
                "pattern": "C:/tmp/x.txt",
                "action": "allow",
            }
        ],
    }
    level, matched = evaluate_tiered_policy(
        cfg, "write_file", {"file_path": "C:/tmp/x.txt"},
    )
    assert level == PermissionLevel.ASK
    assert "approval_overrides" not in matched


def test_a_ignores_path_rules() -> None:
    cfg = {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "rules": [
            {
                "id": "path_ask_env",
                "tools": ["read_file"],
                "pattern": "**/.env*",
                "severity": "HIGH",
            }
        ],
    }
    level, matched = evaluate_tiered_policy(
        cfg, "read_file", {"file_path": "/ws/.env.local"},
    )
    assert level == PermissionLevel.ALLOW
    assert "path_ask_env" not in matched


@pytest.mark.asyncio
async def test_write_file_allow_path_ask_from_b_only(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = tmp_path / "outside" / "a.txt"
    cfg = {
        "enabled": True,
        "tools": {"write_file": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
        },
    }
    engine = PermissionEngine(cfg, workspace_root=workspace)
    result = await engine.check_permission(
        "write_file", {"file_path": str(target)},
    )
    assert result.permission == PermissionLevel.ASK
    assert result.matched_rule == "file_guard:defaults" or (
        result.matched_rule or ""
    ).startswith("file_guard:")
    # A 线已 allow 时，matched_rule 不应再带上 tiered/tools 前缀
    assert "tools.write_file" not in (result.matched_rule or "")
    assert "tiered_policy" not in (result.matched_rule or "")


@pytest.mark.asyncio
async def test_write_file_allow_and_file_guard_write_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = tmp_path / "outside" / "a.txt"
    outside = tmp_path / "outside"
    cfg = {
        "enabled": True,
        "tools": {"write_file": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
            "paths": [
                {
                    "path": outside.as_posix(),
                    "read": "allow",
                    "write": "allow",
                    "exec": "ask",
                    "match": "prefix",
                }
            ],
        },
    }
    engine = PermissionEngine(cfg, workspace_root=workspace)
    result = await engine.check_permission(
        "write_file", {"file_path": str(target)},
    )
    assert result.permission == PermissionLevel.ALLOW


def test_merge_path_tool_writes_allow_tools_not_path_override() -> None:
    """路径工具无 command suggestion：写 allow_tools，不写 path 类 approval_overrides。"""
    cfg = {
        "enabled": True,
        "defaults": {"*": "allow"},
        "tools": {"write_file": "ask"},
        "ask_tools": ["write_file"],
        "approval_overrides": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg, "write_file", {"file_path": "C:/tmp/x.txt"},
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert not any(
        isinstance(o, dict) and o.get("match_type") == "path" for o in overrides
    )
    assert "write_file" in (merged.get("allow_tools") or [])
    assert merged.get("tools", {}).get("write_file") == "allow"
