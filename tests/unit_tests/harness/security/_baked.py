# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-baked permission dicts for engine tests (no product-mode compose)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.fileguard.sensitive_paths import get_builtin_sensitive_path_entries


def baked_unrestricted(**extra: Any) -> dict[str, Any]:
    """file_guard off, tools default allow, ignore host rules, no findings escalate."""
    cfg: dict[str, Any] = {
        "enabled": True,
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "network": {
            "enabled": True,
            "defaults": "allow",
            "ignore_user_host_rules": True,
            "hosts": [],
        },
        "permission_mode": "normal",
        "findings_escalate": False,
        "sandbox_intent": "optional",
    }
    cfg.update(extra)
    return cfg


def baked_workspace_trust(*, home: Path | None = None, **extra: Any) -> dict[str, Any]:
    """Workspace read/write/exec allow; other dirs write/exec ask; builtins injected."""
    paths = extra.pop("paths", None)
    if paths is None:
        paths = get_builtin_sensitive_path_entries(home=home)
    cfg: dict[str, Any] = {
        "enabled": True,
        "defaults": {"*": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
            "paths": deepcopy(paths),
        },
        "network": {
            "enabled": True,
            "defaults": "allow",
            "ignore_user_host_rules": False,
            "hosts": [],
        },
        "permission_mode": "normal",
        "sandbox_intent": "required",
    }
    file_guard_extra = extra.pop("file_guard", None)
    if isinstance(file_guard_extra, dict):
        fg = cfg["file_guard"]
        overlay_paths = file_guard_extra.get("paths")
        for key, value in file_guard_extra.items():
            if key == "paths":
                continue
            fg[key] = deepcopy(value)
        if isinstance(overlay_paths, list):
            fg["paths"] = list(overlay_paths) + list(fg.get("paths") or [])
    cfg.update(extra)
    return cfg


def baked_workspace_ask(*, home: Path | None = None, **extra: Any) -> dict[str, Any]:
    """Unknown tools ask; workspace read allow, write/exec ask; path tools allow."""
    paths = extra.pop("paths", None)
    if paths is None:
        paths = get_builtin_sensitive_path_entries(home=home)
    cfg: dict[str, Any] = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "tools": {
            "read_file": "allow",
            "write_file": "allow",
            "edit_file": "allow",
            "send_file_to_user": "allow",
        },
        "allow_tools": [
            "read_file",
            "write_file",
            "edit_file",
            "send_file_to_user",
        ],
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "ask", "exec": "ask"},
            "paths": deepcopy(paths),
        },
        "network": {
            "enabled": True,
            "defaults": "ask",
            "ignore_user_host_rules": False,
            "hosts": [],
        },
        "permission_mode": "strict",
        "sandbox_intent": "required",
    }
    cfg.update(extra)
    return cfg
