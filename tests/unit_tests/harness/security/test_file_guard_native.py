# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native ``file_guard``：allow/ask/deny + R/W/X + prefix/glob（§5.1–5.3 / §5.5.7）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.models import PermissionLevel


def _native_cfg(paths: list[dict], *, defaults: dict | None = None, enabled: bool = True) -> dict:
    return {
        "enabled": True,
        "file_guard": {
            "enabled": enabled,
            "defaults": defaults or {"read": "ask", "write": "ask", "exec": "ask"},
            "paths": paths,
        },
    }


def test_native_mode_when_paths_present(tmp_path: Path) -> None:
    effective = normalize_path_guard_config(
        _native_cfg([{"path": str(tmp_path / "data"), "read": "allow", "write": "ask", "exec": "deny"}]),
        workspace_root=tmp_path / "ws",
    )
    assert effective.enabled is True
    assert effective.mode == "native"


def test_native_workspace_not_implicitly_allowed(tmp_path: Path) -> None:
    """Native 无隐式 workspace 放行；未配 paths/workspace 时走 defaults。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    internal = workspace / "a.py"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg([], defaults={"read": "ask", "write": "ask", "exec": "ask"}),
            workspace_root=workspace,
        )
    )
    result = checker.evaluate("read_file", {"file_path": str(internal)})
    assert result is not None
    assert result.permission == PermissionLevel.ASK


def test_native_workspace_axis_allows_agent_workspace(tmp_path: Path) -> None:
    """file_guard.workspace 绑定运行时 workspace_root，不写死路径。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    internal = workspace / "a.py"
    outside = tmp_path / "outside" / "b.py"
    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
        },
    }
    effective = normalize_path_guard_config(cfg, workspace_root=workspace)
    assert effective.mode == "native"
    assert any(r.match == "prefix" and "ws" in r.path.replace("\\", "/") for r in effective.paths)

    checker = FileGuardChecker(effective)
    assert checker.evaluate("read_file", {"file_path": str(internal)}) is None
    assert checker.evaluate("write_file", {"file_path": str(internal)}) is None

    outside_result = checker.evaluate("read_file", {"file_path": str(outside)})
    assert outside_result is not None
    assert outside_result.permission == PermissionLevel.ASK


def test_native_workspace_axis_skipped_without_root(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """拿不到 workspace_root 时跳过 workspace 规则并 WARN。"""
    import logging

    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
        },
    }
    with caplog.at_level(logging.WARNING, logger="openjiuwen.harness.security.file_guard"):
        effective = normalize_path_guard_config(cfg, workspace_root=None)
    assert effective.mode == "native"
    assert effective.paths == ()
    assert any("workspace.rule_skipped" in r.message for r in caplog.records)


def test_native_workspace_key_alone_triggers_native(tmp_path: Path) -> None:
    """仅声明 workspace 轴（无 defaults）也进入 Native。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = {
        "enabled": True,
        "external_directory": {"*": "ask"},
        "file_guard": {
            "enabled": True,
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
        },
    }
    effective = normalize_path_guard_config(cfg, workspace_root=workspace)
    assert effective.mode == "native"
    checker = FileGuardChecker(effective)
    assert checker.evaluate("read_file", {"file_path": str(workspace / "x")}) is None


def test_native_prefix_allow_read(tmp_path: Path) -> None:
    data = tmp_path / "data"
    target = data / "f.txt"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg([
                {"path": str(data), "read": "allow", "write": "deny", "exec": "deny"},
            ]),
            workspace_root=tmp_path / "ws",
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(target)}) is None


def test_native_write_denied_on_read_allow_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    target = data / "f.txt"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg([
                {"path": str(data), "read": "allow", "write": "deny", "exec": "deny"},
            ]),
            workspace_root=tmp_path / "ws",
        )
    )
    result = checker.evaluate("write_file", {"file_path": str(target)})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_native_deny_beats_ask_beats_allow_on_overlap(tmp_path: Path) -> None:
    """同一路径多规则冲突：deny > ask > allow。"""
    root = tmp_path / "root"
    nested = root / "nested" / "f.txt"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg([
                {"path": str(root), "read": "allow", "write": "allow", "exec": "ask"},
                {"path": str(root / "nested"), "read": "deny", "write": "deny", "exec": "deny"},
            ]),
            workspace_root=tmp_path / "ws",
        )
    )
    # 最长前缀命中 nested → deny
    result = checker.evaluate("read_file", {"file_path": str(nested)})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_native_write_allow_implies_read_allow(tmp_path: Path) -> None:
    data = tmp_path / "data"
    target = data / "f.txt"
    # 只配 write:allow，未写 read → 蕴含 read allow
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg([
                {"path": str(data), "write": "allow", "exec": "ask"},
            ]),
            workspace_root=tmp_path / "ws",
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(target)}) is None


def test_native_explicit_read_deny_wins_over_write_allow_implication(tmp_path: Path) -> None:
    data = tmp_path / "data"
    target = data / "f.txt"
    effective = normalize_path_guard_config(
        _native_cfg([
            {"path": str(data), "read": "deny", "write": "allow", "exec": "ask"},
        ]),
        workspace_root=tmp_path / "ws",
    )
    rule = next(r for r in effective.paths if "data" in r.path.replace("\\", "/"))
    assert rule.read == PermissionLevel.DENY
    assert rule.write == PermissionLevel.ALLOW
    checker = FileGuardChecker(effective)
    result = checker.evaluate("read_file", {"file_path": str(target)})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_native_glob_ssh_and_env(tmp_path: Path) -> None:
    """Glob：覆盖 path_ask_ssh / path_ask_env 类 pattern。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ssh_key = workspace / ".ssh" / "id_rsa"
    env_file = workspace / ".env.local"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg(
                [
                    {"path": "**/.ssh/**", "match": "glob", "read": "ask", "write": "ask", "exec": "ask"},
                    {"path": "**/.env*", "match": "glob", "read": "ask", "write": "deny", "exec": "deny"},
                ],
                defaults={"read": "allow", "write": "allow", "exec": "ask"},
            ),
            workspace_root=workspace,
        )
    )
    ssh_result = checker.evaluate("read_file", {"file_path": str(ssh_key)})
    assert ssh_result is not None
    assert ssh_result.permission == PermissionLevel.ASK

    env_result = checker.evaluate("read_file", {"file_path": str(env_file)})
    assert env_result is not None
    assert env_result.permission == PermissionLevel.ASK


def test_native_shell_l1_extracts_write_action(tmp_path: Path) -> None:
    """Native 用 L1 抽取：rm 外部路径看 write 轴。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    victim = tmp_path / "outside" / "x.txt"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _native_cfg(
                [{"path": str(tmp_path / "outside"), "read": "allow", "write": "deny", "exec": "ask"}],
                defaults={"read": "ask", "write": "ask", "exec": "ask"},
            ),
            workspace_root=workspace,
        )
    )
    result = checker.evaluate(
        "bash",
        {"command": f'rm "{victim}"', "workdir": str(workspace)},
    )
    assert result is not None
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_engine_native_strictest_with_tool_allow(tmp_path: Path) -> None:
    """A=allow 且 B=deny → DENY。"""
    workspace = tmp_path / "ws"
    secret = tmp_path / "secret" / "a.txt"
    cfg = {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "deny", "write": "deny", "exec": "deny"},
            "paths": [],
        },
    }
    engine = PermissionEngine(cfg, workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(secret)})
    assert result.permission == PermissionLevel.DENY


def test_trusted_dirs_follow_strict_workspace_axes(tmp_path: Path) -> None:
    """选中目录与 workspace 轴一致：Strict 可读、写需确认。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    selected = tmp_path / "docs"
    selected.mkdir()
    target = selected / "a.txt"
    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "ask", "exec": "ask"},
        },
    }
    checker = FileGuardChecker(
        normalize_path_guard_config(
            cfg,
            workspace_root=workspace,
            trusted_dirs=[selected],
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(target)}) is None
    write_result = checker.evaluate(
        "write_file",
        {"file_path": str(target), "content": "x"},
    )
    assert write_result is not None
    assert write_result.permission == PermissionLevel.ASK


def test_trusted_dirs_follow_auto_workspace_axes(tmp_path: Path) -> None:
    """Auto 选中目录可读写。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    selected = tmp_path / "docs"
    selected.mkdir()
    target = selected / "a.txt"
    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
        },
    }
    checker = FileGuardChecker(
        normalize_path_guard_config(
            cfg,
            workspace_root=workspace,
            trusted_dirs=[selected],
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(target)}) is None
    assert checker.evaluate(
        "write_file",
        {"file_path": str(target), "content": "x"},
    ) is None


def test_deny_glob_wins_over_workspace_prefix_in_matched_rule(tmp_path: Path) -> None:
    """Workspace 前缀 allow 与敏感 glob deny 同时命中时，matched_rule 必须指向 glob。"""
    workspace = tmp_path / "ws"
    key = workspace / ".ssh" / "id_rsa"
    key.parent.mkdir(parents=True)
    key.write_text("x", encoding="utf-8")
    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
            "paths": [
                {
                    "path": "**/.ssh/**",
                    "match": "glob",
                    "read": "deny",
                    "write": "deny",
                    "exec": "deny",
                },
            ],
        },
    }
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=workspace))
    result = checker.evaluate("read_file", {"file_path": str(key)})
    assert result is not None
    assert result.permission == PermissionLevel.DENY
    rule = result.matched_rule or ""
    assert "file_guard:glob:**/.ssh/**" in rule
    assert "file_guard:prefix:" not in rule
