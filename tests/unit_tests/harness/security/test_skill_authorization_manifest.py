# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillManifest loading from a skill directory (harness-side conventions)."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.harness.security.skill_authorization.manifest import load_skill_manifest
from openjiuwen.harness.security.skill_authorization.models import SkillTrustLevel
from openjiuwen.harness.security.skill_authorization.schema import (
    SKILL_PERMISSION_FILENAME,
    compute_permissions_hash,
    compute_skill_md_hash,
)


def _write_skill(skill_dir: Path, declaration: object, *, skill_md: str = "body") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / SKILL_PERMISSION_FILENAME).write_text(
        json.dumps(declaration),
        encoding="utf-8",
    )


def test_missing_permission_file_yields_empty_authorizable_manifest(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")

    manifest = load_skill_manifest(skill_dir, trust=SkillTrustLevel.OTHER)

    assert manifest is not None
    assert manifest.authorizable is True
    assert manifest.overlay == {}
    assert manifest.permissions_hash == compute_permissions_hash({"permissions": {}})
    assert manifest.skill_md_hash == compute_skill_md_hash("body")
    assert manifest.skill_name == "demo"
    assert manifest.source == str(skill_dir)
    assert manifest.trust is SkillTrustLevel.OTHER


def test_valid_permission_file_is_normalized(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    declaration = {
        "permissions": {
            "tools": {"write_file": "ASK"},
            "rules": [{"pattern": "date *", "action": "ALLOW"}],
        },
        "risk": {"level": "low"},
    }
    _write_skill(skill_dir, declaration)

    manifest = load_skill_manifest(
        skill_dir,
        trust="builtin",
        source="hub",
        version="1.2.3",
        skill_name="named-skill",
    )

    assert manifest is not None
    assert manifest.authorizable is True
    assert manifest.trust is SkillTrustLevel.BUILTIN
    assert manifest.source == "hub"
    assert manifest.version == "1.2.3"
    assert manifest.skill_name == "named-skill"
    assert manifest.permissions_hash == compute_permissions_hash(declaration)
    assert manifest.overlay == {
        "tools": {"write_file": "ask"},
        "rules": [{"pattern": "date *", "action": "allow"}],
        "risk": {"level": "low"},
    }
    assert manifest.risk_level == "low"
    assert manifest.risk_status == "valid"


def test_invalid_permission_file_is_not_authorizable(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir, {"tools": {"bash": "allow"}})

    manifest = load_skill_manifest(skill_dir, trust=SkillTrustLevel.OTHER)

    assert manifest is not None
    assert manifest.authorizable is False
    assert manifest.permissions_hash == ""
    assert manifest.errors
    assert any("tools" in error for error in manifest.errors)


def test_malformed_json_is_not_authorizable(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / SKILL_PERMISSION_FILENAME).write_text("{not json", encoding="utf-8")

    manifest = load_skill_manifest(skill_dir, trust=SkillTrustLevel.OTHER)

    assert manifest is not None
    assert manifest.authorizable is False
    assert manifest.errors and "解析失败" in manifest.errors[0]


def test_unreadable_skill_md_yields_empty_hash(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir, {"permissions": {"tools": {"write_file": "ask"}}})
    (skill_dir / "SKILL.md").unlink()

    manifest = load_skill_manifest(skill_dir, trust=SkillTrustLevel.OTHER)

    assert manifest is not None
    assert manifest.skill_md_hash == ""


def test_defaults_read_skill_name_and_source_from_directory(tmp_path: Path) -> None:
    skill_dir = tmp_path / "from-dir"
    _write_skill(skill_dir, {"permissions": {}})

    manifest = load_skill_manifest(skill_dir, trust=SkillTrustLevel.OTHER)

    assert manifest is not None
    assert manifest.skill_name == "from-dir"
    assert manifest.source == str(skill_dir)
    assert manifest.version is None
