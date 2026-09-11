# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package sensitive_paths: YAML action on file_guard axes; overlay cannot widen."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openjiuwen.harness.security.permission_engine.fileguard.sensitive_paths import (
    load_package_sensitive_paths,
    merge_package_sensitive_paths,
)


def test_sensitive_paths_use_yaml_action_on_axes(tmp_path: Path) -> None:
    from openjiuwen.harness.security.permission_engine.fileguard import sensitive_paths as paths_mod

    paths_mod._SENSITIVE_PATHS_CACHE = None
    with patch.object(Path, "home", return_value=tmp_path):
        entries = load_package_sensitive_paths()
    paths_mod._SENSITIVE_PATHS_CACHE = None
    assert entries
    ssh = next(e for e in entries if e.get("id") == "home_ssh")
    assert ssh["read"] == ssh["write"] == ssh["exec"] == "deny"
    assert ssh.get("layer") == "builtin"
    assert str(ssh["path"]).replace("\\", "/").startswith(tmp_path.as_posix())
    npmrc = next(e for e in entries if e.get("id") == "home_npmrc")
    assert npmrc["read"] == npmrc["write"] == npmrc["exec"] == "ask"


def test_engine_merges_sensitive_paths_for_legacy_host_file_guard() -> None:
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"read_file": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "ask"},
                "paths": [],
            },
        }
    )
    paths = (engine.config.get("file_guard") or {}).get("paths") or []
    builtin_paths = [p for p in paths if isinstance(p, dict) and p.get("layer") == "builtin"]
    assert builtin_paths
    ids = {p.get("id") for p in builtin_paths}
    assert "any_env" in ids
    assert "home_ssh" in ids


def test_overlay_cannot_widen_package_sensitive_path() -> None:
    effective = merge_package_sensitive_paths(
        {
            "file_guard": {
                "enabled": True,
                "paths": [
                    {
                        "path": "**/.ssh/**",
                        "match": "glob",
                        "read": "allow",
                        "write": "allow",
                        "exec": "allow",
                    }
                ],
            }
        }
    )
    ssh = next(
        p
        for p in effective["file_guard"]["paths"]
        if str(p.get("path", "")).replace("\\", "/").endswith(".ssh/**")
        or p.get("id") == "any_ssh"
        or str(p.get("path")) == "**/.ssh/**"
    )
    assert ssh["read"] == "deny"
    assert ssh["write"] == "deny"
    assert ssh["exec"] == "deny"
