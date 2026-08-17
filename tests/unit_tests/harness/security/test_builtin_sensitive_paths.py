from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openjiuwen.harness.security.engine import PermissionEngine
from openjiuwen.harness.security.fileguard.file_guard import _match_glob
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.fileguard.sensitive_paths import (
    get_builtin_sensitive_path_entries,
)

from tests.unit_tests.harness.security._baked import baked_workspace_trust


def _baked_trust_with_home(home: Path) -> dict:
    return baked_workspace_trust(home=home)


def test_loader_maps_action_to_uniform_axes() -> None:
    entries = get_builtin_sensitive_path_entries()
    assert entries, "expected package sensitive_paths"
    by_id = {e.get("id"): e for e in entries}
    ssh = by_id.get("home_ssh") or next(e for e in entries if "ssh" in str(e.get("path")))
    assert ssh["read"] == ssh["write"] == ssh["exec"] == "deny"
    assert ssh.get("layer") == "builtin"
    aws = next(e for e in entries if ".aws" in str(e.get("path")))
    assert aws["read"] == aws["write"] == aws["exec"] == "ask"


def test_loader_expands_home_prefix(tmp_path: Path) -> None:
    entries = get_builtin_sensitive_path_entries(home=tmp_path)
    aws = next(e for e in entries if e.get("id") == "home_aws" or ".aws" in str(e.get("path")))
    assert str(aws["path"]).replace("\\", "/").startswith(tmp_path.as_posix())
    assert aws["path"].replace("\\", "/").endswith(".aws/**") or "/.aws/**" in aws["path"].replace(
        "\\", "/"
    )


def test_loader_injects_builtin_sensitive_paths() -> None:
    paths = get_builtin_sensitive_path_entries()
    assert any(".aws" in str(p.get("path", "")) for p in paths if isinstance(p, dict))
    assert any(
        isinstance(p, dict) and "ssh" in str(p.get("path", "")) and p.get("read") == "deny"
        for p in paths
    )


@pytest.mark.asyncio
async def test_eval_ssh_key_denied(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    key = home / ".ssh" / "id_rsa"
    key.write_text("x", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    eng = PermissionEngine(_baked_trust_with_home(home), workspace_root=ws)
    result = await eng.check_permission("read_file", {"file_path": str(key)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_eval_npmrc_ask(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    npmrc = home / ".npmrc"
    npmrc.write_text("//registry=x", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    eng = PermissionEngine(_baked_trust_with_home(home), workspace_root=ws)
    result = await eng.check_permission(
        "write_file",
        {"file_path": str(npmrc), "content": "y"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitive path matching")
def test_match_glob_case_insensitive_on_win32() -> None:
    assert _match_glob("**/.ssh/**", "C:/Users/me/.SSH/id_rsa")
    assert _match_glob("**/id_rsa", "C:/Users/me/.ssh/ID_RSA")


def test_normalize_win32_and_posix() -> None:
    from openjiuwen.harness.security.common.builtin_platforms import normalize_builtin_platform

    assert normalize_builtin_platform("win32") == "windows"
    assert normalize_builtin_platform("linux") == "unix"
    assert normalize_builtin_platform("darwin") == "unix"
    assert normalize_builtin_platform("windows") == "windows"
    assert normalize_builtin_platform("unix") == "unix"


def test_omitted_platforms_matches_all() -> None:
    from openjiuwen.harness.security.common.builtin_platforms import entry_matches_platforms

    assert entry_matches_platforms({}, {"windows"})
    assert entry_matches_platforms({"platforms": ["all"]}, {"unix"})
    assert not entry_matches_platforms({"platforms": ["unix"]}, {"windows"})
    assert entry_matches_platforms({"platforms": ["unix", "windows"]}, {"windows"})


def test_resolve_active_platforms_is_exclusive() -> None:
    from openjiuwen.harness.security.common.builtin_platforms import resolve_active_platforms

    assert resolve_active_platforms("windows") == frozenset({"windows", "all"})
    assert resolve_active_platforms("unix") == frozenset({"unix", "all"})


def test_builtin_shell_rules_filter_by_platform() -> None:
    from openjiuwen.harness.security.toolguard.tiered_policy import get_builtin_security_rules

    win = {str(r.get("id")) for r in get_builtin_security_rules(platform="windows")}
    unix = {str(r.get("id")) for r in get_builtin_security_rules(platform="unix")}
    assert "shell_ld_preload" in unix
    assert "shell_ld_preload" not in win
    assert "shell_disk_partition_or_raw_device_write_win" in win
    assert "shell_disk_partition_or_raw_device_write_win" not in unix
    assert "shell_disk_partition_or_raw_device_write_unix" in unix
    assert "shell_disk_partition_or_raw_device_write_unix" not in win
    assert "shell_docker_privileged" in win
    assert "shell_docker_privileged" in unix
    assert "shell_rm_root_hard_deny" in win
    assert "shell_rm_root_hard_deny" in unix


def test_sensitive_paths_filter_keeps_cross_platform_defaults() -> None:
    win = {str(e.get("id")) for e in get_builtin_sensitive_path_entries(platform="windows")}
    unix = {str(e.get("id")) for e in get_builtin_sensitive_path_entries(platform="unix")}
    assert "home_ssh" in win
    assert "home_ssh" in unix
    assert "home_aws" in win
    assert "home_aws" in unix
