# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""skill_permissions.json schema validation, normalization and hashing."""

from __future__ import annotations

import hashlib

import pytest

from openjiuwen.harness.security.skill_authorization.schema import (
    SKILL_AUTHORIZATION_ENABLED_ENV,
    SKILL_PERMISSION_FILENAME,
    SkillPermissionValidationError,
    canonical_skill_permission_json,
    compute_permissions_hash,
    compute_skill_md_hash,
    is_skill_authorization_enabled,
    normalize_skill_permission,
    normalize_skill_risk,
    validate_skill_permission,
)


def _valid_declaration() -> dict:
    return {
        "permissions": {
            "tools": {"write_file": "ASK"},
            "rules": [
                {"id": "allow_date", "pattern": "date *", "action": "ALLOW", "scope": "WILDCARD"},
                {"pattern": "rm *", "action": "deny"},
            ],
            "file_guard": {"global": {"/data": {"read": "allow", "write": "ASK"}}},
        },
        "risk": {"level": "LOW"},
    }


def test_constants_match_public_contract() -> None:
    assert SKILL_PERMISSION_FILENAME == "skill_permissions.json"
    assert SKILL_AUTHORIZATION_ENABLED_ENV == "SKILL_AUTHORIZATION_ENABLED"


def test_valid_declaration_passes() -> None:
    validate_skill_permission(_valid_declaration())


def test_non_object_top_level_is_rejected() -> None:
    with pytest.raises(SkillPermissionValidationError) as exc_info:
        validate_skill_permission(["not", "an", "object"])
    assert "顶层必须是 JSON 对象" in exc_info.value.errors[0]


def test_flat_top_level_permissions_are_rejected() -> None:
    with pytest.raises(SkillPermissionValidationError) as exc_info:
        validate_skill_permission({"tools": {"write_file": "allow"}})
    assert any("tools 禁止在 skill_permissions.json 中声明" in error for error in exc_info.value.errors)


def test_shell_tool_whole_allow_is_rejected() -> None:
    with pytest.raises(SkillPermissionValidationError) as exc_info:
        validate_skill_permission({"permissions": {"tools": {"bash": "allow"}}})
    assert any("禁止 shell 类工具整工具 allow" in error for error in exc_info.value.errors)


def test_unknown_keys_and_bad_values_are_collected() -> None:
    declaration = {
        "permissions": {
            "unknown_section": True,
            "tools": {"write_file": "maybe"},
            "rules": [
                {"pattern": "", "action": "maybe", "scope": "suffix", "extra": 1},
            ],
            "file_guard": {
                "workspace": {},
                "unexpected_entry": {},
                "global": {"/data": {"read": "sometimes", "other": "allow"}},
            },
        },
        "unexpected": {},
    }
    with pytest.raises(SkillPermissionValidationError) as exc_info:
        validate_skill_permission(declaration)
    errors = "\n".join(exc_info.value.errors)
    assert "permissions 存在不允许的键" in errors
    assert "tools.write_file 值域" in errors
    assert "rules[0].action" in errors
    assert "rules[0].scope" in errors
    assert "rules[0] 存在不允许的键" in errors
    assert "file_guard.workspace 禁止" in errors
    assert "file_guard 存在不允许的键" in errors
    assert "file_guard.global['/data'].read 值域" in errors
    assert "非法轴" in errors
    assert "存在不允许的顶层键" in errors


def test_legacy_enabled_field_is_accepted_but_ignored() -> None:
    validate_skill_permission({"permissions": {"enabled": True, "tools": {"write_file": "ask"}}})
    assert "enabled" not in normalize_skill_permission(
        {"permissions": {"enabled": True, "tools": {"write_file": "ask"}}},
    )


def test_normalize_lowercases_and_strips() -> None:
    normalized = normalize_skill_permission(_valid_declaration())
    assert normalized == {
        "tools": {"write_file": "ask"},
        "rules": [
            {"id": "allow_date", "pattern": "date *", "action": "allow", "scope": "wildcard"},
            {"pattern": "rm *", "action": "deny"},
        ],
        "file_guard": {"global": {"/data": {"read": "allow", "write": "ask"}}},
        "risk": {"level": "low"},
    }


def test_normalize_drops_empty_sections() -> None:
    assert normalize_skill_permission({"permissions": {}}) == {}
    assert normalize_skill_permission({"permissions": {"tools": {}, "rules": []}}) == {}


def test_normalize_skill_risk_statuses() -> None:
    assert normalize_skill_risk({}) == (None, "missing")
    assert normalize_skill_risk({"risk": {"level": "HIGH"}}) == ("high", "valid")
    assert normalize_skill_risk({"risk": {"level": "critical"}}) == (None, "invalid")
    assert normalize_skill_risk({"risk": {"level": "low", "reason": "x"}}) == (None, "invalid")
    assert normalize_skill_risk({"risk": "low"}) == (None, "invalid")


def test_canonical_json_is_deterministic_and_order_independent() -> None:
    first = {
        "permissions": {
            "tools": {"write_file": "ask", "read_file": "allow"},
            "rules": [{"pattern": "date *", "action": "allow"}],
        },
    }
    second = {
        "permissions": {
            "rules": [{"action": "ALLOW", "pattern": " date * "}],
            "tools": {"read_file": "ALLOW", "write_file": "ASK"},
        },
    }
    assert canonical_skill_permission_json(first) == canonical_skill_permission_json(second)
    assert '"enabled"' not in canonical_skill_permission_json(first)


def test_compute_permissions_hash_is_deterministic() -> None:
    first = compute_permissions_hash(_valid_declaration())
    second = compute_permissions_hash(_valid_declaration())
    assert first == second
    assert len(first) == 64
    expected = hashlib.sha256(
        canonical_skill_permission_json(_valid_declaration()).encode("utf-8"),
    ).hexdigest()
    assert first == expected

    changed = _valid_declaration()
    changed["permissions"]["tools"]["write_file"] = "deny"
    assert compute_permissions_hash(changed) != first


def test_compute_permissions_hash_validates_first() -> None:
    with pytest.raises(SkillPermissionValidationError):
        compute_permissions_hash({"tools": {"bash": "allow"}})


def test_compute_skill_md_hash_accepts_str_and_bytes() -> None:
    assert compute_skill_md_hash("body") == compute_skill_md_hash(b"body")
    assert compute_skill_md_hash("body") == hashlib.sha256(b"body").hexdigest()


def test_is_skill_authorization_enabled_explicit_config_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SKILL_AUTHORIZATION_ENABLED_ENV, "false")
    assert is_skill_authorization_enabled({"skill_authorization": {"enabled": True}})
    monkeypatch.setenv(SKILL_AUTHORIZATION_ENABLED_ENV, "true")
    assert not is_skill_authorization_enabled({"skill_authorization": {"enabled": False}})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("maybe", False),
    ],
)
def test_is_skill_authorization_enabled_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    monkeypatch.setenv(SKILL_AUTHORIZATION_ENABLED_ENV, raw)
    assert is_skill_authorization_enabled({}) is expected


def test_is_skill_authorization_enabled_defaults_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKILL_AUTHORIZATION_ENABLED_ENV, raising=False)
    assert not is_skill_authorization_enabled(None)
    assert not is_skill_authorization_enabled({"skill_authorization": {}})
