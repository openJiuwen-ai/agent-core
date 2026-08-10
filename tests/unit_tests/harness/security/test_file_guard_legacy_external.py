# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FileGuard Legacy 投影：复现 ExternalDirectoryChecker 黄金集（§5.5.7）。

验收：
1. 仅有 ``external_directory`` / ``trusted_dirs`` 时，行为与现网 ExternalDirectory 等价。
2. ``file_guard.enabled=false`` 时，即使存在 ``external_directory: {"*": deny}``，也不拦路径；
   工具级 ``tools/rules`` 判定不变。
3. 缺省未写 ``file_guard.enabled``、但有 ``external_directory`` 时自动开启路径层（§5.5.3）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.models import PermissionLevel


def _legacy_cfg(external_action: str = "ask", **extra) -> dict:
    cfg: dict = {
        "enabled": True,
        "external_directory": {"*": external_action},
    }
    cfg.update(extra)
    return cfg


# ---------- normalize / enabled ----------


def test_normalize_auto_enables_when_external_directory_present(tmp_path: Path) -> None:
    """有 external_directory、未写 file_guard.enabled → 路径层自动开启（Legacy）。"""
    ws = tmp_path / "ws"
    effective = normalize_path_guard_config(
        _legacy_cfg("ask"),
        workspace_root=ws,
        trusted_dirs=[],
    )
    assert effective.enabled is True
    assert effective.mode == "legacy"


def test_normalize_disabled_when_no_path_config(tmp_path: Path) -> None:
    """无 external_directory、无 file_guard.paths、无 trusted_dirs → 路径层关闭。"""
    effective = normalize_path_guard_config(
        {"enabled": True},
        workspace_root=tmp_path / "ws",
        trusted_dirs=[],
    )
    assert effective.enabled is False


def test_normalize_explicit_false_disables_even_with_external_directory(tmp_path: Path) -> None:
    """显式 file_guard.enabled=false 关掉整层路径防护（含旧 ExternalDirectory）。"""
    effective = normalize_path_guard_config(
        {
            "enabled": True,
            "external_directory": {"*": "deny"},
            "file_guard": {"enabled": False},
        },
        workspace_root=tmp_path / "ws",
        trusted_dirs=[],
    )
    assert effective.enabled is False


# ---------- Legacy 黄金集（原 ExternalDirectory 语义） ----------


def test_legacy_external_path_triggers_ask_without_trusted_dirs(tmp_path: Path) -> None:
    """无 trusted_dirs 时，workspace 外路径应触发 ASK。"""
    workspace = tmp_path / "ws"
    external_file = tmp_path / "external" / "secrets.txt"
    effective = normalize_path_guard_config(
        _legacy_cfg("ask"),
        workspace_root=workspace,
        trusted_dirs=[],
    )
    checker = FileGuardChecker(effective)
    result = checker.evaluate("read_file", {"file_path": str(external_file)})
    assert result is not None
    assert result.permission == PermissionLevel.ASK
    assert result.external_paths is not None and result.external_paths


def test_legacy_trusted_dir_makes_external_path_internal(tmp_path: Path) -> None:
    """把外部路径父目录加入 trusted_dirs 后，该子树不再触发路径层。"""
    workspace = tmp_path / "ws"
    external_dir = tmp_path / "external"
    external_file = external_dir / "secrets.txt"

    baseline = FileGuardChecker(
        normalize_path_guard_config(
            _legacy_cfg("ask"),
            workspace_root=workspace,
            trusted_dirs=[],
        )
    )
    assert baseline.evaluate("read_file", {"file_path": str(external_file)}) is not None

    checker = FileGuardChecker(
        normalize_path_guard_config(
            _legacy_cfg("ask"),
            workspace_root=workspace,
            trusted_dirs=[external_dir],
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(external_file)}) is None


def test_legacy_workspace_internal_path_is_noop(tmp_path: Path) -> None:
    """Legacy 下 workspace 内路径不抬升（等价 ExternalDirectory 返回 None）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    internal = workspace / "src" / "main.py"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _legacy_cfg("ask"),
            workspace_root=workspace,
            trusted_dirs=[],
        )
    )
    assert checker.evaluate("read_file", {"file_path": str(internal)}) is None


def test_legacy_external_directory_allow_prefix(tmp_path: Path) -> None:
    """external_directory 中 action=allow 的前缀覆盖外部路径。"""
    workspace = tmp_path / "ws"
    trusted = tmp_path / "trusted"
    external_file = trusted / "data.txt"
    cfg = {
        "enabled": True,
        "external_directory": {
            "*": "ask",
            str(trusted).replace("\\", "/"): "allow",
        },
    }
    checker = FileGuardChecker(
        normalize_path_guard_config(cfg, workspace_root=workspace, trusted_dirs=[])
    )
    assert checker.evaluate("read_file", {"file_path": str(external_file)}) is None


def test_legacy_deny_star(tmp_path: Path) -> None:
    """external_directory '*'=deny 时外部路径 DENY。"""
    workspace = tmp_path / "ws"
    external_file = tmp_path / "external" / "x.txt"
    checker = FileGuardChecker(
        normalize_path_guard_config(
            _legacy_cfg("deny"),
            workspace_root=workspace,
            trusted_dirs=[],
        )
    )
    result = checker.evaluate("read_file", {"file_path": str(external_file)})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


# ---------- 引擎挂载 + 开关隔离 ----------


def test_engine_update_trusted_dirs_switches_verdict(tmp_path: Path) -> None:
    """PermissionEngine.update_trusted_dirs 热更新后，路径层判定跟随变化。"""
    workspace = tmp_path / "ws"
    external_dir = tmp_path / "external"
    external_file = external_dir / "secrets.txt"
    args = {"file_path": str(external_file)}

    engine = PermissionEngine(_legacy_cfg("ask"), workspace_root=workspace)

    level, _rule = engine.evaluate_global_policy_directly("read_file", args)
    assert level == PermissionLevel.ASK

    engine.update_trusted_dirs([external_dir])
    assert engine.trusted_dirs == [external_dir]
    level_none, rule = engine.evaluate_global_policy_directly("read_file", args)
    assert rule is None or "external_directory" not in (rule or "")
    assert "file_guard" not in (rule or "")
    assert level_none is None or level_none == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_file_guard_disabled_skips_path_layer(tmp_path: Path) -> None:
    """file_guard.enabled=false 时，即使 '*'=deny，也不再拦截外部路径。"""
    workspace = tmp_path / "ws"
    external_file = tmp_path / "external" / "secrets.txt"
    cfg = {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "external_directory": {"*": "deny"},
        "file_guard": {"enabled": False},
    }
    engine = PermissionEngine(cfg, workspace_root=workspace)
    result = await engine.check_permission(
        "read_file", {"file_path": str(external_file)}
    )
    assert result.permission == PermissionLevel.ALLOW
    assert result.external_paths is None


@pytest.mark.asyncio
async def test_engine_file_guard_disabled_does_not_change_tool_rules(tmp_path: Path) -> None:
    """关闭路径层不得改变 tools/rules 判定。"""
    workspace = tmp_path / "ws"
    cfg = {
        "enabled": True,
        "tools": {"bash": "ask"},
        "external_directory": {"*": "deny"},
        "file_guard": {"enabled": False},
    }
    engine = PermissionEngine(cfg, workspace_root=workspace)
    result = await engine.check_permission("bash", {"command": "echo hi"})
    assert result.permission == PermissionLevel.ASK
