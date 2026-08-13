from __future__ import annotations

from openjiuwen.harness.security.builtin_platforms import (
    entry_matches_platforms,
    normalize_builtin_platform,
    resolve_active_platforms,
)
from openjiuwen.harness.security.sensitive_paths import get_builtin_sensitive_path_entries
from openjiuwen.harness.security.tiered_policy import get_builtin_security_rules


def test_normalize_win32_and_posix() -> None:
    assert normalize_builtin_platform("win32") == "windows"
    assert normalize_builtin_platform("linux") == "unix"
    assert normalize_builtin_platform("darwin") == "unix"
    assert normalize_builtin_platform("windows") == "windows"
    assert normalize_builtin_platform("unix") == "unix"


def test_omitted_platforms_matches_all() -> None:
    assert entry_matches_platforms({}, {"windows"})
    assert entry_matches_platforms({"platforms": ["all"]}, {"unix"})
    assert not entry_matches_platforms({"platforms": ["unix"]}, {"windows"})
    assert entry_matches_platforms({"platforms": ["unix", "windows"]}, {"windows"})


def test_resolve_active_platforms_is_exclusive() -> None:
    assert resolve_active_platforms("windows") == frozenset({"windows", "all"})
    assert resolve_active_platforms("unix") == frozenset({"unix", "all"})


def test_builtin_shell_rules_filter_by_platform() -> None:
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
