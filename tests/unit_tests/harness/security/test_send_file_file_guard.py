# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""send_file_to_user 必须走 file_guard（abs_file_path_list 字符串 / 数组）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.harness.security.engine import PermissionEngine
from openjiuwen.harness.security.fileguard.extract import extract_accesses_native
from openjiuwen.harness.security.models import PermissionLevel

from tests.unit_tests.harness.security._baked import baked_workspace_ask, baked_workspace_trust


def test_extract_send_file_string_path(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "a.txt"
    accesses = extract_accesses_native(
        "send_file_to_user",
        {"abs_file_path_list": str(target)},
        workspace,
    )
    assert [(p, act) for p, act, _src in accesses] == [(target.resolve(), "read")]


def test_extract_send_file_json_array_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    a = workspace / "a.txt"
    b = workspace / "b.txt"
    from_json = extract_accesses_native(
        "send_file_to_user",
        {"abs_file_path_list": json.dumps([str(a), str(b)])},
        workspace,
    )
    from_list = extract_accesses_native(
        "send_file_to_user",
        {"abs_file_path_list": [str(a), str(b)]},
        workspace,
    )
    expected = {(a.resolve(), "read"), (b.resolve(), "read")}
    assert {(p, act) for p, act, _src in from_json} == expected
    assert {(p, act) for p, act, _src in from_list} == expected


@pytest.mark.asyncio
async def test_engine_send_file_outside_workspace_allows_read(tmp_path: Path) -> None:
    """发送按读轴：区外读允许。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "send_file_to_user",
        {"abs_file_path_list": str(outside)},
    )
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_send_file_workspace_allows(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = workspace / "report.txt"
    inside.write_text("ok", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "send_file_to_user",
        {"abs_file_path_list": [str(inside)]},
    )
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_send_file_env_denies(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env_file = workspace / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "send_file_to_user",
        {"abs_file_path_list": str(env_file)},
    )
    assert result.permission == PermissionLevel.DENY
    rule = result.matched_rule or ""
    assert "file_guard:glob:" in rule
    assert ".env" in rule
    assert "file_guard:prefix:" not in rule


@pytest.mark.asyncio
async def test_engine_send_workspace_ssh_denies_with_glob_rule(tmp_path: Path) -> None:
    """工作区内 .ssh 被内置 glob deny；reason 不得误报成 workspace 前缀。"""
    workspace = tmp_path / "ws"
    key = workspace / ".ssh" / "id_rsa"
    key.parent.mkdir(parents=True)
    key.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "send_file_to_user",
        {"abs_file_path_list": str(key)},
    )
    assert result.permission == PermissionLevel.DENY
    rule = result.matched_rule or ""
    assert "file_guard:glob:" in rule
    assert ".ssh" in rule
    assert "file_guard:prefix:" not in rule
    assert "Denied by rule:" in (result.reason or "")


@pytest.mark.asyncio
async def test_engine_send_file_outside_workspace_asks_when_read_default_ask(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_ask(), workspace_root=workspace)
    result = await engine.check_permission(
        "send_file_to_user",
        {"abs_file_path_list": str(outside)},
    )
    assert result.permission == PermissionLevel.ASK
