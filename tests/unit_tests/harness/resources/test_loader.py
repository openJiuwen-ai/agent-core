# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExpertHarness manifest loader core contracts."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
import yaml

from openjiuwen.harness.resources.expert_harness_parts import canonicalize_expert_harness_spec
from openjiuwen.harness.schema.expert_harness_spec import ExpertHarnessSpec, SubAgentSpec

pytestmark = pytest.mark.level0


def _load_expert_harness_spec(path: str | Path) -> ExpertHarnessSpec:
    loader = import_module("openjiuwen.harness.resources.loader")
    return loader.load_expert_harness_spec(path)


def _write_manifest(package_dir: Path, payload: dict[str, Any] | None = None) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = package_dir / "expert_harness.yaml"
    manifest.write_text(yaml.safe_dump(_manifest_payload(payload), sort_keys=True), encoding="utf-8")
    return manifest


def _write_harness_config_manifest(
    package_dir: Path,
    payload: dict[str, Any] | None = None,
) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = package_dir / "harness_config.yaml"
    manifest.write_text(yaml.safe_dump(_manifest_payload(payload), sort_keys=True), encoding="utf-8")
    return manifest


def _write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _manifest_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "expert_harness.v1",
        "id": "loader_pack",
        "name": "Loader Pack",
        "tools": [{"type": "core.web_search"}],
    }
    if overrides is not None:
        payload.update(overrides)
    return payload


def test_load_binds_source_root_from_directory_or_manifest(tmp_path: Path) -> None:
    """Directory or manifest path loads Spec with source.root at the package dir."""
    package_dir = tmp_path / "package"
    manifest = _write_manifest(package_dir)

    by_dir = _load_expert_harness_spec(package_dir)
    by_file = _load_expert_harness_spec(manifest)

    assert isinstance(by_dir, ExpertHarnessSpec)
    assert by_dir.id == "loader_pack"
    assert by_dir.source is not None
    assert by_dir.source.root == str(package_dir.resolve())
    assert by_dir.source.uri == str(manifest.resolve())
    assert by_file.source is not None
    assert by_file.source.root == str(package_dir.resolve())


def test_load_normalizes_legacy_prompt_sections(tmp_path: Path) -> None:
    """Legacy prompts.sections normalize into prompt_sections / file_sections."""
    package_dir = tmp_path / "package"
    _write_harness_config_manifest(
        package_dir,
        {
            "schema_version": "harness_config.v0.1",
            "language": "en",
            "prompts": {
                "sections": [
                    {
                        "name": "identity",
                        "content": "You are a legacy expert.",
                    },
                    {
                        "name": "mission",
                        "priority": 40,
                        "content": {"en": "Use {{ workspace_root }}."},
                    },
                    {
                        "name": "role_playbook",
                        "file": "AGENT.md",
                        "content": {"en": "Read {{ workspace_root }}."},
                    },
                ],
            },
            "resources": {"tools": []},
        },
    )

    spec = _load_expert_harness_spec(package_dir)

    assert [(section.name, section.priority) for section in spec.prompt_sections] == [
        ("identity", 10),
        ("mission", 40),
    ]
    assert spec.prompt_sections[0].content == {
        "cn": "You are a legacy expert.",
        "en": "You are a legacy expert.",
    }
    assert len(spec.file_sections) == 1
    assert spec.file_sections[0].filename == "AGENT.md"


def test_load_builtin_subagent_short_name_into_spec(tmp_path: Path) -> None:
    """Builtin subagent short name loads into Spec and canonicalize maps to core.subagent.*."""
    package_dir = tmp_path / "r_member"
    _write_yaml(package_dir / "harness_config.yaml", {"config": {"enable_subagent": True}})
    _write_yaml(
        package_dir / "subagents" / "subagents.yaml",
        {"subagents": [{"builtin": "explore_agent"}]},
    )

    spec = _load_expert_harness_spec(package_dir)
    assert len(spec.subagents) == 1
    assert isinstance(spec.subagents[0], SubAgentSpec)
    assert spec.subagents[0].factory_name == "core.explore_agent"

    canonical = canonicalize_expert_harness_spec(spec)
    assert canonical.subagents[0].factory_name == "core.subagent.explore_agent"
    assert any(rail.type == "core.subagent" for rail in canonical.rails)


def test_load_raises_when_manifest_missing(tmp_path: Path) -> None:
    """Missing recognized manifest names raise FileNotFoundError."""
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "not_a_manifest.yaml").write_text(
        yaml.safe_dump(_manifest_payload()), encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError):
        _load_expert_harness_spec(package_dir)


def test_load_normalizes_legacy_builtin_tool_and_rail_specs(tmp_path: Path) -> None:
    """Legacy resources.tools/rails builtin shapes normalize to core.* Spec types."""
    package_dir = tmp_path / "package"
    _write_harness_config_manifest(
        package_dir,
        {
            "schema_version": "harness_config.v0.1",
            "tools": [],
            "resources": {
                "tools": [
                    {"type": "builtin", "names": ["filesystem", "shell"]},
                    {"type": "builtin", "name": "web_search", "kwargs": {"limit": 3}},
                ],
                "rails": [
                    {"type": "builtin", "name": "security", "kwargs": {"strict": True}},
                ],
            },
        },
    )

    spec = _load_expert_harness_spec(package_dir)

    assert [tool.type for tool in spec.tools] == [
        "core.filesystem",
        "core.shell",
        "core.web_search",
    ]
    assert spec.tools[2].params == {"limit": 3}
    assert len(spec.rails) == 1
    assert spec.rails[0].type == "core.security"
    assert spec.rails[0].params == {"strict": True}


def test_load_normalizes_legacy_package_specs_with_kwargs(tmp_path: Path) -> None:
    """Legacy package tool/rail entries normalize to harness.tool.file / harness.rail.import."""
    package_dir = tmp_path / "package"
    _write_harness_config_manifest(
        package_dir,
        {
            "schema_version": "harness_config.v0.1",
            "tools": [],
            "resources": {
                "tools": [
                    {
                        "type": "package",
                        "module": "openjiuwen.extensions.harness.package.tools.custom",
                        "class": "CustomTool",
                        "kwargs": {"mode": "fast"},
                    }
                ],
                "rails": [
                    {
                        "type": "package",
                        "module": "external.package.CustomRailModule",
                        "class": "CustomRail",
                        "params": {"level": 2},
                    }
                ],
            },
        },
    )

    spec = _load_expert_harness_spec(package_dir)

    assert len(spec.tools) == 1
    assert spec.tools[0].type == "harness.tool.file"
    assert spec.tools[0].params == {
        "file_path": "tools/custom.py",
        "class_name": "CustomTool",
        "mode": "fast",
    }
    assert len(spec.rails) == 1
    assert spec.rails[0].type == "harness.rail.import"
    assert spec.rails[0].params == {
        "import_path": "external.package.CustomRailModule.CustomRail",
        "level": 2,
    }


def test_load_normalizes_member_optimizer_sidecar_manifests(tmp_path: Path) -> None:
    """Member-optimizer sidecar tools/rails/skills.yaml normalize into ExpertHarnessSpec."""
    package_dir = tmp_path / "r_member"
    _write_yaml(package_dir / "harness_config.yaml", {"config": {"enable_subagent": False}})
    (package_dir / "tools" / "risk_checker.py").parent.mkdir(parents=True, exist_ok=True)
    (package_dir / "tools" / "risk_checker.py").write_text("# tool\n", encoding="utf-8")
    _write_yaml(
        package_dir / "tools" / "tools.yaml",
        {"tools": [{"file": "tools/risk_checker.py", "class_name": "RiskCheckerTool"}]},
    )
    (package_dir / "rails" / "budget.py").parent.mkdir(parents=True, exist_ok=True)
    (package_dir / "rails" / "budget.py").write_text("# rail\n", encoding="utf-8")
    _write_yaml(
        package_dir / "rails" / "rails.yaml",
        {"rails": [{"file": "rails/budget.py", "class_name": "BudgetRail"}]},
    )
    _write_yaml(package_dir / "skills" / "skills.yaml", {"skills": ["skills"]})

    spec = _load_expert_harness_spec(package_dir)

    # Legacy harness_config without id falls back to package directory name.
    assert spec.source is not None
    assert spec.source.root == str(package_dir.resolve())
    assert spec.id == package_dir.name
    assert spec.config.enable_subagent is False
    assert [tool.type for tool in spec.tools] == ["harness.tool.file"]
    assert spec.tools[0].params == {
        "file_path": "tools/risk_checker.py",
        "class_name": "RiskCheckerTool",
    }
    assert [rail.type for rail in spec.rails] == ["harness.rail.file"]
    assert spec.rails[0].params == {
        "file_path": "rails/budget.py",
        "class_name": "BudgetRail",
    }
    assert [skill.dir for skill in spec.skills] == ["skills"]
