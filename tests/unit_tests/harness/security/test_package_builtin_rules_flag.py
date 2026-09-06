# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host switch: skip package builtin_rules.yaml when package_builtin_rules is false."""

from __future__ import annotations

from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.fileguard.sensitive_paths import (
    merge_package_sensitive_paths,
)
from openjiuwen.harness.security.permission_engine.netguard.net_urls import (
    merge_package_net_urls,
)
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
    package_builtin_rules_enabled,
)


def test_package_builtin_rules_defaults_on() -> None:
    assert package_builtin_rules_enabled(None) is True
    assert package_builtin_rules_enabled({}) is True
    assert package_builtin_rules_enabled({"package_builtin_rules": True}) is True
    assert package_builtin_rules_enabled({"package_builtin_rules": False}) is False
    assert package_builtin_rules_enabled({"package_builtin_rules": "false"}) is False


def test_inline_skips_command_rules_when_disabled() -> None:
    cfg = inline_package_command_rules(
        {
            "package_builtin_rules": False,
            "rules": [{"id": "host_rule", "tools": ["bash"], "action": "ask"}],
        }
    )
    assert not any(
        isinstance(r, dict) and r.get("layer") == "builtin" for r in (cfg.get("rules") or [])
    )
    assert any(
        isinstance(r, dict) and r.get("id") == "host_rule" for r in (cfg.get("rules") or [])
    )


def test_merge_skips_sensitive_paths_when_disabled() -> None:
    cfg = merge_package_sensitive_paths(
        {
            "package_builtin_rules": False,
            "file_guard": {"enabled": True, "paths": []},
        }
    )
    paths = (cfg.get("file_guard") or {}).get("paths") or []
    assert not any(isinstance(p, dict) and p.get("layer") == "builtin" for p in paths)


def test_merge_skips_net_urls_when_disabled() -> None:
    cfg = merge_package_net_urls(
        {
            "package_builtin_rules": False,
            "net_guard": {"enabled": True, "defaults": "allow", "urls": {}},
        }
    )
    urls = (cfg.get("net_guard") or {}).get("urls") or {}
    assert "localhost" not in urls
    assert "169.254.169.254" not in urls


def test_engine_does_not_reinject_when_host_disables_package_builtin_rules() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "package_builtin_rules": False,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "ask"},
                "paths": [],
            },
            "net_guard": {"enabled": True, "defaults": "allow", "urls": {}},
        }
    )
    assert not any(
        isinstance(r, dict) and r.get("layer") == "builtin"
        for r in (engine.config.get("rules") or [])
    )
    paths = (engine.config.get("file_guard") or {}).get("paths") or []
    assert not any(isinstance(p, dict) and p.get("layer") == "builtin" for p in paths)
    urls = (engine.config.get("net_guard") or {}).get("urls") or {}
    assert "localhost" not in urls
    assert engine.config.get("package_builtin_rules") is False
