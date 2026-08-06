# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for secure Skill folder discovery and manifest normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import openjiuwen.symphony.shared.fingerprint.parser as parser_module
import openjiuwen.symphony.shared.fingerprint.scanner as scanner_module
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.shared.fingerprint import (
    DataTypeVocabulary,
    SkillFolderScanner,
    SkillManifestParser,
    build_source_snapshot,
    normalize_capability_type,
    normalize_io_specs,
)


def _write_skill(folder: Path, frontmatter: str, body: str = "Skill instructions.") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    entrypoint = folder / "SKILL.md"
    entrypoint.write_text(f"---\n{frontmatter.rstrip()}\n---\n{body}\n", encoding="utf-8")
    return entrypoint


def test_parser_populates_descriptor_fields_and_keeps_skill_md_as_semantic_input(tmp_path: Path) -> None:
    entrypoint = _write_skill(
        tmp_path / "media-tool",
        """
capability_id: media-transcode
capability_type: workflow-node-v2
name: Media Transcode
description: Convert structured media requests.
category: Media
tags: [video, conversion, video]
inputs:
  request:
    type: application/json
    description: Conversion request
  api_key:
    type: string
    default: should-not-be-retained
outputs:
  - name: rendered_video
    type: video/mp4
    description: Rendered result
""",
        body="# Procedure\nUse the request exactly as documented.",
    )

    parsed = SkillManifestParser().parse(entrypoint, root=tmp_path, display_path="media-tool/SKILL.md")

    assert parsed.ok
    assert parsed.descriptor is not None
    assert parsed.descriptor.capability_id == "media-transcode"
    assert parsed.descriptor.capability_type == "workflow-node-v2"
    assert parsed.descriptor.name == "Media Transcode"
    assert parsed.descriptor.description == "Convert structured media requests."
    assert parsed.descriptor.classification == "Media"
    assert parsed.descriptor.tags == ("video", "conversion")
    assert parsed.descriptor.inputs[0].name == "request"
    assert parsed.descriptor.inputs[0].type == "json"
    assert parsed.descriptor.inputs[1].default is None
    assert parsed.descriptor.inputs[1].metadata["default_redacted"] is True
    assert parsed.descriptor.outputs[0].type == "mp4"
    assert parsed.semantic_content == entrypoint.read_text(encoding="utf-8")
    assert parsed.descriptor.semantic_content == parsed.semantic_content
    assert any(item.code == "sensitive_default_redacted" for item in parsed.diagnostics)


def test_serializable_parser_and_scan_views_exclude_semantic_source_content(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    entrypoint = _write_skill(
        root / "credentialed",
        """
name: credentialed
description: Uses a caller-provided credential.
inputs:
  api_key:
    type: string
    default: FRONTMATTER-CREDENTIAL
""",
        body="# Procedure\nUse BODY-ONLY-SECRET while executing the capability.",
    )

    parsed = SkillManifestParser().parse(entrypoint, root=root)
    scan = SkillFolderScanner(root).scan()

    assert parsed.descriptor is not None
    assert "FRONTMATTER-CREDENTIAL" in parsed.descriptor.semantic_content
    assert "BODY-ONLY-SECRET" in parsed.descriptor.semantic_content
    assert scan.capabilities[0].semantic_content == parsed.descriptor.semantic_content

    parser_payload = parsed.to_dict()
    descriptor_payload = parsed.to_descriptor_data()
    scan_payload = scan.to_dict()
    serialized_payloads = json.dumps(
        [parser_payload, descriptor_payload, scan_payload],
        ensure_ascii=False,
        sort_keys=True,
    )

    assert "semantic_content" not in parser_payload
    assert descriptor_payload is not None and "semantic_content" not in descriptor_payload
    assert "semantic_content" not in scan_payload["capabilities"][0]
    assert "FRONTMATTER-CREDENTIAL" not in serialized_payloads
    assert "BODY-ONLY-SECRET" not in serialized_payloads


def test_scanner_is_deterministic_and_hashes_only_safe_recursive_assets(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    alpha = skills_root / "alpha"
    _write_skill(
        alpha,
        "name: alpha\ndescription: Alpha skill\ninputs:\n  prompt:\n    type: text",
    )
    safe_asset = alpha / "assets" / "guide.md"
    safe_asset.parent.mkdir()
    safe_asset.write_text("safe-v1", encoding="utf-8")
    (alpha / ".env.production").write_text("TOKEN=first", encoding="utf-8")
    (alpha / "credentials.json").write_text('{"token": "first"}', encoding="utf-8")
    cache_asset = alpha / "cache" / "cached.txt"
    cache_asset.parent.mkdir()
    cache_asset.write_text("cache-v1", encoding="utf-8")
    git_asset = alpha / ".git" / "config"
    git_asset.parent.mkdir()
    git_asset.write_text("git-v1", encoding="utf-8")
    bytecode_asset = alpha / "__pycache__" / "module.pyc"
    bytecode_asset.parent.mkdir()
    bytecode_asset.write_bytes(b"cache-v1")
    key_asset = alpha / "keys" / "private.pem"
    key_asset.parent.mkdir()
    key_asset.write_text("private-v1", encoding="utf-8")

    _write_skill(
        skills_root / "zulu",
        "name: zulu\ndescription: Zulu skill\noutputs:\n  summary:\n    type: markdown",
    )
    external = tmp_path / "outside.txt"
    external.write_text("outside-v1", encoding="utf-8")
    (alpha / "external-link.txt").symlink_to(external)

    first = SkillFolderScanner(skills_root).scan()
    second = SkillFolderScanner(skills_root).scan()

    assert [item.capability_id for item in first] == ["alpha", "zulu"]
    assert first.to_dict() == second.to_dict()
    assert first.source_snapshot == second.source_snapshot
    assert build_source_snapshot(first) == first.source_snapshot
    assert first.source_snapshot.capability_count == 2
    different_source = SkillFolderScanner(skills_root, source="another-provider").scan()
    assert different_source.source_snapshot.snapshot_id != first.source_snapshot.snapshot_id
    assert different_source.source_snapshot.metadata["protocol"] == "symphony-source-snapshot-v1"
    assert first.io_name_vocabulary.observed_terms == ("prompt", "summary")
    assert first.io_name_vocabulary.resolve("Prompt").normalized_value == "prompt"
    assert first.io_name_vocabulary.resolve("not-observed").normalized_value is None
    assert first.io_name_vocabulary.to_dict() == second.io_name_vocabulary.to_dict()
    alpha_descriptor = first[0]
    assert alpha_descriptor.metadata["asset_paths"] == ["assets/guide.md", "SKILL.md"]
    assert "outside-v1" not in alpha_descriptor.semantic_content
    first_hash = alpha_descriptor.content_hash

    (alpha / ".env.production").write_text("TOKEN=second", encoding="utf-8")
    (alpha / "credentials.json").write_text('{"token": "second"}', encoding="utf-8")
    cache_asset.write_text("cache-v2", encoding="utf-8")
    git_asset.write_text("git-v2", encoding="utf-8")
    bytecode_asset.write_bytes(b"cache-v2")
    key_asset.write_text("private-v2", encoding="utf-8")
    external.write_text("outside-v2", encoding="utf-8")
    excluded_change = SkillFolderScanner(skills_root).scan()

    assert excluded_change[0].content_hash == first_hash
    assert excluded_change.source_snapshot == first.source_snapshot

    safe_asset.write_text("safe-v2", encoding="utf-8")
    safe_change = SkillFolderScanner(skills_root).scan()

    assert safe_change[0].content_hash != first_hash
    assert safe_change.source_snapshot != first.source_snapshot
    assert any(item.code == "symlink_skipped" for item in safe_change.diagnostics)


def test_scanner_never_traverses_symlinked_skill_directories(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    external_skill = tmp_path / "external" / "not-visible"
    _write_skill(external_skill, "name: hidden")
    (root / "linked-skill").symlink_to(external_skill, target_is_directory=True)
    _write_skill(root / "visible", "name: visible")

    result = SkillFolderScanner(root).scan()

    assert [item.capability_id for item in result] == ["visible"]
    assert any(item.code == "symlink_skipped" and item.path == "linked-skill" for item in result.diagnostics)


def test_scanner_does_not_follow_directory_replaced_after_parent_enumeration(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    victim = root / "victim"
    _write_skill(victim, "name: original", body="ORIGINAL")
    outside = tmp_path / "outside"
    _write_skill(outside, "name: outside", body="OUTSIDE SECRET")
    original_open = scanner_module.open_directory_no_follow

    def replace_then_open(path: Path, *, root: Path):
        if path == victim and victim.is_dir() and not victim.is_symlink():
            victim.rename(root / "victim-original")
            victim.symlink_to(outside, target_is_directory=True)
        return original_open(path, root=root)

    monkeypatch.setattr(scanner_module, "open_directory_no_follow", replace_then_open)

    result = SkillFolderScanner(root).scan()

    assert result.capabilities == ()
    assert any(item.code == "directory_read_failed" and item.path == "victim" for item in result.diagnostics)
    assert "OUTSIDE SECRET" not in result.to_dict().__repr__()


def test_scanner_fails_closed_without_secure_anchored_open(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "visible", "name: visible")
    monkeypatch.setattr(scanner_module, "supports_anchored_open", lambda: False)

    with pytest.raises(BaseError) as caught:
        SkillFolderScanner(root).scan()

    assert caught.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


def test_parser_does_not_follow_ancestor_replaced_after_validation(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    entrypoint = _write_skill(root / "skill", "name: original", body="ORIGINAL")
    outside = tmp_path / "outside" / "skill"
    _write_skill(outside, "name: outside", body="OUTSIDE SECRET")
    original_validate = parser_module._validate_entrypoint

    def validate_then_replace(path: Path, scan_root: Path | str | None) -> str | None:
        result = original_validate(path, scan_root)
        if result is None:
            original_folder = entrypoint.parent
            original_folder.rename(root / "original-moved")
            original_folder.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(parser_module, "_validate_entrypoint", validate_then_replace)

    parsed = SkillManifestParser().parse(entrypoint, root=root)

    assert parsed.descriptor is None
    assert "OUTSIDE SECRET" not in parsed.semantic_content


def test_scanner_rejects_manifest_changed_between_parse_and_asset_hash(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    skill = root / "mutable"
    _write_skill(skill, "name: before\ndescription: Original description", body="ORIGINAL")
    original_hash = scanner_module._hash_safe_assets

    def replace_then_hash(skill_root: Path, **kwargs: Any):
        _write_skill(skill_root, "name: after\ndescription: Replacement description", body="REPLACED")
        return original_hash(skill_root, **kwargs)

    monkeypatch.setattr(scanner_module, "_hash_safe_assets", replace_then_hash)

    result = SkillFolderScanner(root).scan()

    assert result.capabilities == ()
    assert any(
        item.code == "manifest_changed_during_scan" and item.path == "mutable/SKILL.md" for item in result.diagnostics
    )
    assert "ORIGINAL" not in result.to_dict().__repr__()
    assert "REPLACED" not in result.to_dict().__repr__()


def test_scanner_rejects_root_that_becomes_unreadable_after_validation(tmp_path: Path, monkeypatch) -> None:
    scan_root = tmp_path / "skills"
    _write_skill(scan_root / "visible", "name: visible")
    original_open = scanner_module.open_directory_no_follow

    def fail_root_open(path: Path, *, root: Path):
        if path == scan_root:
            return None
        return original_open(path, root=root)

    monkeypatch.setattr(scanner_module, "open_directory_no_follow", fail_root_open)

    with pytest.raises(BaseError) as caught:
        SkillFolderScanner(scan_root).scan()

    assert caught.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


def test_scanner_reports_bad_items_and_continues(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "good", "name: good")
    bad = root / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: [unterminated\n---\nbody", encoding="utf-8")

    result = SkillFolderScanner(root).scan()

    assert [item.capability_id for item in result] == ["good"]
    assert any(item.code == "invalid_frontmatter" and item.path == "bad/SKILL.md" for item in result.diagnostics)


def test_recursive_yaml_is_isolated_as_one_manifest_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "good", "name: good")
    bad = root / "recursive"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: recursive\nmetadata: &loop\n  - *loop\n---\nbody\n",
        encoding="utf-8",
    )

    result = SkillFolderScanner(root).scan()

    assert [item.capability_id for item in result] == ["good"]
    assert any(
        item.code == "unsafe_frontmatter_structure" and item.path == "recursive/SKILL.md" for item in result.diagnostics
    )


def test_asset_hash_limits_reject_only_the_oversized_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "good", "name: good")
    large = root / "large"
    _write_skill(large, "name: large")
    (large / "asset.bin").write_bytes(b"x" * 256)

    result = SkillFolderScanner(root, max_asset_file_bytes=128).scan()

    assert [item.capability_id for item in result] == ["good"]
    assert any(
        item.code == "asset_size_limit_exceeded" and item.capability_id == "large" for item in result.diagnostics
    )


def test_secret_named_directories_and_camel_case_files_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "safe"
    _write_skill(skill, "name: safe")
    safe_asset = skill / "assets" / "guide.md"
    safe_asset.parent.mkdir()
    safe_asset.write_text("safe", encoding="utf-8")
    for directory_name in ("tokens", "passwords", "clientSecrets"):
        hidden = skill / directory_name / "current.txt"
        hidden.parent.mkdir()
        hidden.write_text("must-not-hash", encoding="utf-8")
    for file_name in ("apiKey.json", "clientSecret.txt", "accessToken.txt"):
        (skill / file_name).write_text("must-not-hash", encoding="utf-8")

    result = SkillFolderScanner(root).scan()

    assert result[0].metadata["asset_paths"] == ["assets/guide.md", "SKILL.md"]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_directory_entries", 1),
        ("max_entrypoints", 1),
        ("max_scan_directories", 1),
        ("max_scan_asset_files", 1),
        ("max_scan_asset_bytes", 1),
    ],
)
def test_whole_scan_limits_abort_instead_of_publishing_partial_inventory(
    tmp_path: Path,
    setting: str,
    value: int,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "alpha", "name: alpha")
    _write_skill(root / "beta", "name: beta")
    scanner = SkillFolderScanner(root, **cast(Any, {setting: value}))

    with pytest.raises(BaseError) as caught:
        scanner.scan()

    assert caught.value.status is StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_depth", True),
        ("max_asset_files", False),
    ],
)
def test_scanner_rejects_boolean_integer_settings(
    tmp_path: Path,
    setting: str,
    value: bool,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "visible", "name: visible")
    scanner = SkillFolderScanner(root, **cast(Any, {setting: value}))

    with pytest.raises(BaseError) as caught:
        scanner.scan()

    assert caught.value.status is StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR


def test_scanner_rejects_traversal_and_every_member_of_duplicate_id_group(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "first", "capability_id: Shared\nname: First")
    _write_skill(root / "second", "capability_id: shared\nname: Second")
    _write_skill(root / "unsafe", "capability_id: ../escape\nname: Unsafe")
    _write_skill(root / "unsafe-name", "name: ../../escape")
    _write_skill(root / "unique", "name: unique")

    result = SkillFolderScanner(root).scan()

    assert [item.capability_id for item in result] == ["unique"]
    duplicate_diagnostics = [item for item in result.diagnostics if item.code == "duplicate_capability_id"]
    assert len(duplicate_diagnostics) == 2
    assert {item.capability_id for item in duplicate_diagnostics} == {"Shared", "shared"}
    assert sum(item.code == "unsafe_capability_id" for item in result.diagnostics) == 2


def test_scanner_respects_max_depth(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "name: root-skill")
    _write_skill(root / "one", "name: one")
    _write_skill(root / "one" / "two", "name: two")

    result = SkillFolderScanner(root, max_depth=1).scan()

    assert [item.capability_id for item in result] == ["one", "root-skill"]


def test_invalid_root_uses_symphony_status_code(tmp_path: Path) -> None:
    with pytest.raises(BaseError) as caught:
        SkillFolderScanner(tmp_path / "missing").scan()

    assert caught.value.status == StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID


def test_data_type_and_io_normalization_are_deterministic_and_extensible() -> None:
    vocabulary = DataTypeVocabulary.default()

    assert vocabulary.version == "data-type-v1"
    assert vocabulary.resolve("application/json").normalized_value == "json"
    assert vocabulary.resolve("new-vendor-format").normalized_value == "unknown"
    assert vocabulary.is_subtype("png", "file")
    assert normalize_capability_type("subagent") == "agent"
    assert normalize_capability_type("vendor-specific-kind") == "vendor-specific-kind"

    items = normalize_io_specs(
        {
            "User Prompt": "string",
            "document": {"type": "application/pdf", "required": False},
        },
        direction="input",
    )

    assert [(item.name, item.type, item.required) for item in items] == [
        ("user_prompt", "text", True),
        ("document", "pdf", False),
    ]
