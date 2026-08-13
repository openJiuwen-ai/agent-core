from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.file_guard import _match_glob
from openjiuwen.harness.security.mode_controller import PermissionModeController
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.sensitive_paths import (
    get_builtin_sensitive_path_entries,
)


def _compose_auto_with_home(home: Path, monkeypatch: pytest.MonkeyPatch):
    """Compose auto mode with builtins expanded against ``home`` (no _CACHE poke)."""
    monkeypatch.setattr(
        "openjiuwen.harness.security.mode_controller.get_builtin_sensitive_path_entries",
        lambda: get_builtin_sensitive_path_entries(home=home),
    )
    return PermissionModeController().compose({"enabled": True, "mode": "auto"})


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


def test_compose_auto_injects_builtin_sensitive_paths() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    paths = (eff.permissions.get("file_guard") or {}).get("paths") or []
    assert any(".aws" in str(p.get("path", "")) for p in paths if isinstance(p, dict))
    assert any(
        isinstance(p, dict) and "ssh" in str(p.get("path", "")) and p.get("read") == "deny"
        for p in paths
    )


def test_compose_full_access_skips_file_guard_builtins() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "full_access"})
    assert (eff.permissions.get("file_guard") or {}).get("enabled") is False


def test_yaml_allow_cannot_widen_builtin_deny() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {
            "enabled": True,
            "mode": "auto",
            "file_guard": {
                "paths": [
                    {
                        "path": "**/.ssh/**",
                        "match": "glob",
                        "read": "allow",
                        "write": "allow",
                        "exec": "allow",
                    }
                ]
            },
        }
    )
    paths = (eff.permissions.get("file_guard") or {}).get("paths") or []
    ssh_entries = [
        p
        for p in paths
        if isinstance(p, dict) and str(p.get("path", "")).replace("\\", "/") == "**/.ssh/**"
    ]
    assert ssh_entries
    assert all(p.get("read") == p.get("write") == p.get("exec") == "deny" for p in ssh_entries)
    assert any(p.get("layer") == "builtin" for p in ssh_entries)


def test_yaml_allow_cannot_widen_builtin_ask() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {
            "enabled": True,
            "mode": "auto",
            "file_guard": {
                "paths": [
                    {
                        "path": "~/.aws/**",
                        "match": "glob",
                        "read": "allow",
                        "write": "allow",
                        "exec": "allow",
                    }
                ]
            },
        }
    )
    paths = (eff.permissions.get("file_guard") or {}).get("paths") or []
    aws_entries = [p for p in paths if isinstance(p, dict) and ".aws" in str(p.get("path", ""))]
    assert aws_entries
    assert not any(str(p.get("path", "")).replace("\\", "/").startswith("~/") for p in aws_entries)
    assert all(p.get("read") == "ask" for p in aws_entries)


@pytest.mark.asyncio
async def test_eval_ssh_key_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    key = home / ".ssh" / "id_rsa"
    key.write_text("x", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    eff = _compose_auto_with_home(home, monkeypatch)
    eng = PermissionEngine(eff.permissions, workspace_root=ws)
    result = await eng.check_permission("read_file", {"file_path": str(key)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_eval_npmrc_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    npmrc = home / ".npmrc"
    npmrc.write_text("//registry=x", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    eff = _compose_auto_with_home(home, monkeypatch)
    eng = PermissionEngine(eff.permissions, workspace_root=ws)
    result = await eng.check_permission(
        "write_file",
        {"file_path": str(npmrc), "content": "y"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitive path matching")
def test_match_glob_case_insensitive_on_win32() -> None:
    assert _match_glob("**/.ssh/**", "C:/Users/me/.SSH/id_rsa")
    assert _match_glob("**/id_rsa", "C:/Users/me/.ssh/ID_RSA")
