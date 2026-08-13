# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""路径持久化写 ``file_guard.paths``（§5.5.6 / P2）。"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.harness.security.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.patterns import (
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
    persist_cli_trusted_directory,
)


def test_merge_file_guard_path_rule_writes_paths(tmp_path: Path) -> None:
    perms = {"enabled": True, "external_directory": {"*": "ask"}}
    target = (tmp_path / "trusted").as_posix()
    merged, wrote = merge_file_guard_path_rule(
        perms, target, read="allow", write="allow", exec_="ask",
    )
    assert wrote is True
    fg = merged["file_guard"]
    assert fg["enabled"] is True
    paths = fg["paths"]
    assert any(
        isinstance(p, dict)
        and p.get("path", "").rstrip("/") == target.rstrip("/")
        and p.get("read") == "allow"
        and p.get("write") == "allow"
        and p.get("exec") == "ask"
        for p in paths
    )
    # 仍有 external_directory 时保持 Legacy，避免误切 Native
    effective = normalize_path_guard_config(merged, workspace_root=tmp_path / "ws")
    assert effective.mode == "legacy"
    assert effective.enabled is True


def test_merge_external_directory_allow_forwards_to_file_guard(tmp_path: Path) -> None:
    perms = {"enabled": True, "external_directory": {"*": "ask"}}
    file_path = str(tmp_path / "outside" / "a.txt")
    merged, wrote = merge_external_directory_allow_into_permissions(perms, [file_path])
    assert wrote is True
    fg = merged.get("file_guard") or {}
    assert isinstance(fg.get("paths"), list) and fg["paths"]
    path_norm = file_path.replace("\\", "/").rstrip("/")
    assert any(
        isinstance(p, dict)
        and str(p.get("path", "")).replace("\\", "/").rstrip("/") == path_norm
        and p.get("read") == "allow"
        and p.get("write") == "ask"
        and p.get("exec") == "ask"
        for p in fg["paths"]
    )
    # 旧键 external_directory 不再新增具名 allow（只读不写）
    ext = merged.get("external_directory") or {}
    parent = Path(file_path.replace("\\", "/")).parent.as_posix().rstrip("/")
    assert ext.get(parent) != "allow"
    assert ext.get(path_norm) != "allow"


def test_merge_file_guard_access_allows_read_keeps_exact_dir(tmp_path: Path) -> None:
    """ls 目录「总是允许」：写入该目录本身，且仅 read allow（不放开 write）。"""
    perms = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
        },
    }
    target = (tmp_path / "workspace" / "projects").as_posix()
    merged, wrote = merge_file_guard_access_allows(perms, [(target, "read")])
    assert wrote is True
    paths = merged["file_guard"]["paths"]
    assert any(
        isinstance(p, dict)
        and str(p.get("path", "")).rstrip("/") == target.rstrip("/")
        and p.get("read") == "allow"
        and p.get("write") == "ask"
        and p.get("exec") == "ask"
        for p in paths
    )
    # 不得上卷到父目录
    parent = Path(target).parent.as_posix().rstrip("/")
    assert not any(
        isinstance(p, dict) and str(p.get("path", "")).rstrip("/") == parent
        for p in paths
    )
    added = merged.get("_file_guard_paths_added") or []
    assert any(
        isinstance(p, dict)
        and str(p.get("path", "")).rstrip("/") == target.rstrip("/")
        and p.get("read") == "allow"
        for p in added
    )


def test_merge_file_guard_access_allows_write_escalates_axes(tmp_path: Path) -> None:
    perms = {"enabled": True, "file_guard": {"enabled": True, "paths": []}}
    target = (tmp_path / "data").as_posix()
    merged, _ = merge_file_guard_access_allows(perms, [(target, "read")])
    merged, wrote = merge_file_guard_access_allows(merged, [(target, "write")])
    assert wrote is True
    entry = next(p for p in merged["file_guard"]["paths"] if p.get("path") == target)
    assert entry["read"] == "allow"
    assert entry["write"] == "allow"
    assert entry["exec"] == "ask"


def test_collect_ask_accesses_for_ls_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projects = tmp_path / "workspace" / "projects"
    projects.mkdir(parents=True)
    cfg = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
        },
    }
    checker = FileGuardChecker(
        normalize_path_guard_config(cfg, workspace_root=workspace)
    )
    accesses = checker.collect_ask_accesses(
        "bash",
        {"command": f'ls "{projects}"', "workdir": str(workspace)},
    )
    assert accesses
    path_posix, action = accesses[0]
    assert action == "read"
    assert path_posix.rstrip("/") == projects.resolve().as_posix().rstrip("/")


def test_persist_cli_trusted_directory_writes_file_guard(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent.yaml"
    trusted = tmp_path / "trusted_dir"
    trusted.mkdir()
    bootstrap = {"enabled": True, "external_directory": {"*": "ask"}, "tools": {}}
    result = persist_cli_trusted_directory(
        str(trusted),
        config_yaml_path=yaml_path,
        bootstrap_permissions=bootstrap,
    )
    assert result.get("ok") is True

    import yaml

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    perms = data["permissions"]
    fg = perms["file_guard"]
    assert fg["enabled"] is True
    dir_norm = trusted.resolve().as_posix().rstrip("/")
    assert any(
        isinstance(p, dict) and p.get("path", "").rstrip("/") == dir_norm
        and p.get("read") == "allow"
        and p.get("write") == "allow"
        and p.get("exec") == "ask"
        for p in fg["paths"]
    )
    # 不再写 path 类 approval_overrides；shell command 覆盖仍可保留
    overrides = perms.get("approval_overrides") or []
    assert not any(
        isinstance(o, dict) and o.get("match_type") == "path" for o in overrides
    )
    assert any(
        isinstance(o, dict) and o.get("match_type") == "command" for o in overrides
    )

    # 路径层对信任目录生效
    engine_cfg = perms
    effective = normalize_path_guard_config(
        engine_cfg, workspace_root=tmp_path / "ws", trusted_dirs=[],
    )
    from openjiuwen.harness.security.file_guard import FileGuardChecker

    checker = FileGuardChecker(effective)
    assert checker.evaluate(
        "read_file", {"file_path": str(trusted / "x.txt")}
    ) is None
