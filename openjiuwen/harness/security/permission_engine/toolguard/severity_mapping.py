# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Map rule severity to allow/ask/deny using ``permission_mode``.

Host rules often carry ``severity`` without an explicit ``action``. Compose and
engine ingest fill ``action`` from this table before tiered evaluation.

Product contract (docs / config.yaml comments)::

    severity | normal | strict
    ---------+--------+-------
    LOW      | allow  | allow
    MEDIUM   | allow  | ask
    HIGH     | ask    | ask
    CRITICAL | ask    | deny

Unknown severity is treated as HIGH (ask). Empty severity returns None so the
caller leaves ``action`` unset.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.security.permission_engine.models import PermissionLevel

_VALID_MODES = frozenset({"normal", "strict"})

_NORMAL_SEVERITY_TO_ACTION = {
    "LOW": "allow",
    "MEDIUM": "allow",
    "HIGH": "ask",
    "CRITICAL": "ask",
}

_STRICT_SEVERITY_TO_ACTION = {
    "LOW": "allow",
    "MEDIUM": "ask",
    "HIGH": "ask",
    "CRITICAL": "deny",
}


def normalize_permission_mode(raw: Any) -> str:
    """Return ``normal`` or ``strict``; invalid / missing → ``normal``."""
    if not isinstance(raw, str):
        return "normal"
    mode = raw.strip().lower()
    return mode if mode in _VALID_MODES else "normal"


def severity_to_action(
    severity: Any,
    permission_mode: Any = "normal",
) -> str | None:
    """Map severity to ``allow`` / ``ask`` / ``deny``.

    Returns None when severity is empty so callers can skip writing ``action``.
    """
    sev = str(severity or "").strip().upper()
    if not sev:
        return None
    mode = normalize_permission_mode(permission_mode)
    table = (
        _STRICT_SEVERITY_TO_ACTION
        if mode == "strict"
        else _NORMAL_SEVERITY_TO_ACTION
    )
    return table.get(sev, "ask")


def severity_to_decision(
    severity: Any,
    permission_mode: Any = "normal",
) -> PermissionLevel | None:
    """Same mapping as :func:`severity_to_action`, as ``PermissionLevel``."""
    action = severity_to_action(severity, permission_mode)
    if action is None:
        return None
    return PermissionLevel(action)


__all__ = [
    "normalize_permission_mode",
    "severity_to_action",
    "severity_to_decision",
]
