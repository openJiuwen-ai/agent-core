# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""外部目录检查的 trusted_dirs 行为（commit 400de7dd）。

验证 :class:`ExternalDirectoryChecker` 与 :class:`PermissionEngine` 在引入
``trusted_dirs`` 后的语义：

- 默认（不传 trusted_dirs）与改动前等价：workspace 外路径触发 external_directory。
- 把外部路径的父目录加入 trusted_dirs 后，该子树被视为内部，不再触发。
- ``PermissionEngine.update_trusted_dirs`` 热更新后判定随之变化，且属性可读。

路径用 ``tmp_path`` 派生的真实绝对路径，保证 Windows / POSIX 下 ``Path.is_absolute``
与 ``resolve`` 行为一致——``contains_path`` 基于 ``os.path.relpath`` 判定，不要求路径真实存在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.checker import ExternalDirectoryChecker
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel


def _cfg(external_action: str = "ask") -> dict:
    return {"enabled": True, "external_directory": {"*": external_action}}


def test_external_path_triggers_ask_without_trusted_dirs(tmp_path: Path) -> None:
    """无 trusted_dirs 时，workspace 外路径应触发 ASK（与改动前一致）。"""
    workspace = tmp_path / "ws"
    external_file = tmp_path / "external" / "secrets.txt"
    checker = ExternalDirectoryChecker(_cfg(), workspace_root=workspace)
    result = checker.check_external_paths(
        "read_file", {"file_path": str(external_file)}
    )
    assert result is not None
    assert result.permission == PermissionLevel.ASK
    assert result.external_paths is not None and result.external_paths


def test_trusted_dir_makes_external_path_internal(tmp_path: Path) -> None:
    """把外部路径父目录加入 trusted_dirs 后，该子树不再触发 external_directory。"""
    workspace = tmp_path / "ws"
    external_dir = tmp_path / "external"
    external_file = external_dir / "secrets.txt"

    # 未信任时仍触发（前置断言，证明不是路径本身就在 workspace 内）
    baseline = ExternalDirectoryChecker(_cfg(), workspace_root=workspace)
    assert baseline.check_external_paths(
        "read_file", {"file_path": str(external_file)}
    ) is not None

    checker = ExternalDirectoryChecker(
        _cfg(), workspace_root=workspace, trusted_dirs=[external_dir]
    )
    assert checker.check_external_paths(
        "read_file", {"file_path": str(external_file)}
    ) is None


def test_engine_update_trusted_dirs_switches_verdict(tmp_path: Path) -> None:
    """``PermissionEngine.update_trusted_dirs`` 热更新后，external_directory 判定跟随变化。"""
    workspace = tmp_path / "ws"
    external_dir = tmp_path / "external"
    external_file = external_dir / "secrets.txt"
    args = {"file_path": str(external_file)}

    engine = PermissionEngine(_cfg(), workspace_root=workspace)

    # 初始无 trusted_dirs → 命中 external_directory → ASK
    level, _rule = engine.evaluate_global_policy_directly("read_file", args)
    assert level == PermissionLevel.ASK

    # 热更新信任外部目录 → 不再命中 external_directory
    engine.update_trusted_dirs([external_dir])
    assert engine.trusted_dirs == [external_dir]
    level_none, rule = engine.evaluate_global_policy_directly("read_file", args)
    # external_directory 不再触发；read_file 无任何 tiered 规则时回落为 None
    assert rule is None or "external_directory" not in rule
    assert level_none is None or level_none == PermissionLevel.ALLOW
