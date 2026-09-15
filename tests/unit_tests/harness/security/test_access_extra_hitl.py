# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-declared extra.paths: schema, extraction, HITL ASK, persist skip."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.prompts.tools.bash import get_bash_input_params
from openjiuwen.harness.prompts.tools.code import get_code_input_params
from openjiuwen.harness.prompts.tools.filesystem import get_read_file_input_params
from openjiuwen.harness.prompts.tools.powershell import get_powershell_input_params
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import merge_file_guard_access_allows
from openjiuwen.harness.security.permission_engine.access_extra import (
    bind_tool_access_extra,
    current_tool_access_extra,
    extract_extra_paths,
    reset_tool_access_extra,
)
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_accesses_native,
)


def _swarm_like_cfg(workspace: Path) -> dict:
    return {
        "enabled": True,
        "tools": {
            "read_file": "allow",
            "write_file": "allow",
            "bash": "allow",
            "code": "allow",
        },
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
            "paths": [
                {
                    "path": str(workspace / "_sentinel"),
                    "read": "ask",
                    "write": "ask",
                    "exec": "ask",
                    "layer": "builtin",
                }
            ],
        },
    }


def test_extract_extra_paths_dedup_and_skip_blank() -> None:
    assert extract_extra_paths(
        {"extra": {"paths": ["D:\\docs", "D:/docs/", "", "  ", "E:\\data", 1]}}
    ) == ["D:\\docs", "E:\\data"]
    assert extract_extra_paths({"path": "D:\\docs"}) == []
    assert extract_extra_paths(None) == []


def test_fs_shell_code_schemas_include_extra_paths() -> None:
    for schema in (
        get_read_file_input_params("cn"),
        get_bash_input_params("cn"),
        get_powershell_input_params("cn"),
        get_code_input_params("cn"),
    ):
        extra = schema["properties"]["extra"]
        assert extra["required"] == ["paths"]
        assert extra["properties"]["paths"]["type"] == "array"


def test_native_extract_includes_declared_extra_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    accesses = extract_accesses_native(
        "code",
        {"code": "print(1)", "extra": {"paths": [str(outside)]}},
        workspace,
    )
    sources = {src for _p, _act, src in accesses}
    assert "extra.paths" in sources
    assert any(p == outside.resolve() for p, _act, src in accesses if src == "extra.paths")


def test_evaluate_extra_paths_asks_when_defaults_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    checker = FileGuardChecker(
        normalize_path_guard_config(_swarm_like_cfg(workspace), workspace_root=workspace)
    )
    args = {"file_path": str(outside / "a.txt"), "extra": {"paths": [str(outside)]}}
    assert checker.evaluate("read_file", args) is None
    extra = checker.evaluate_extra_paths("read_file", args)
    assert extra is not None
    assert extra.permission == PermissionLevel.ASK
    assert extra.matched_rule == "extra.paths"
    assert any("outside" in p.replace("\\", "/") for p in (extra.external_paths or []))


def test_evaluate_extra_paths_skips_workspace_and_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checker = FileGuardChecker(
        normalize_path_guard_config(_swarm_like_cfg(workspace), workspace_root=workspace)
    )
    inside = workspace / "a.txt"
    assert checker.evaluate_extra_paths(
        "read_file",
        {"file_path": str(inside), "extra": {"paths": [str(inside)]}},
    ) is None
    assert checker.evaluate_extra_paths("read_file", {"file_path": str(inside)}) is None


def test_evaluate_extra_paths_skips_after_persist(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    cfg = _swarm_like_cfg(workspace)
    args = {"command": "python run.py", "extra": {"paths": [str(outside)]}}
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=workspace))
    accesses = checker.collect_extra_persist_accesses("bash", args)
    assert accesses
    merged, wrote = merge_file_guard_access_allows(cfg, accesses)
    assert wrote is True
    after = FileGuardChecker(normalize_path_guard_config(merged, workspace_root=workspace))
    assert after.evaluate_extra_paths("bash", args) is None


@pytest.mark.asyncio
async def test_engine_asks_declared_extra_paths_with_swarm_defaults(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    engine = PermissionEngine(_swarm_like_cfg(workspace), workspace_root=workspace)
    no_extra = await engine.check_permission(
        "read_file", {"file_path": str(outside / "a.txt")}
    )
    assert no_extra.permission == PermissionLevel.ALLOW

    asked = await engine.check_permission(
        "bash",
        {"command": "python run.py", "extra": {"paths": [str(outside)]}},
    )
    assert asked.permission == PermissionLevel.ASK
    assert "extra.paths" in (asked.matched_rule or "")


@pytest.mark.asyncio
async def test_engine_asks_extra_paths_when_file_guard_disabled(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"code": "allow"},
            "file_guard": {"enabled": False},
        },
        workspace_root=workspace,
    )
    result = await engine.check_permission(
        "code",
        {"code": "print(1)", "extra": {"paths": [str(outside)]}},
    )
    assert result.permission == PermissionLevel.ASK
    assert "extra.paths" in (result.matched_rule or "")


def test_bind_tool_access_extra_contextvar() -> None:
    token = bind_tool_access_extra({"extra": {"paths": ["D:\\approved"]}})
    try:
        assert current_tool_access_extra() == {"extra": {"paths": ["D:\\approved"]}}
    finally:
        reset_tool_access_extra(token)
    assert current_tool_access_extra() is None
