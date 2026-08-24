# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin / AgentTemplate manifest loader helpers not covered by the E2E suite.

Field-by-field manifest mapping, legacy YAML compatibility, and Plugin /
AgentTemplate hot-load behavior are covered end-to-end in
``tests/system_tests/harness/test_deep_agent_spec_load_e2e.py`` (see
``TestExtensionLoadE2E``) per the hot-load test plan; this file keeps small
standalone helpers that are awkward to cover only via DeepAgent E2E.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

pytestmark = pytest.mark.level0


def test_agent_template_manifest_accepts_flat_identity(tmp_path: Path) -> None:
    """Top-level name/description derive the runtime card from the directory."""
    from openjiuwen.harness.resources import load_agent_template_package

    package_dir = tmp_path / "member1"
    persona_dir = package_dir / "persona"
    persona_dir.mkdir(parents=True)
    (persona_dir / "member1.md").write_text("# Member 1\n", encoding="utf-8")
    manifest = package_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "package_type": "agent_template",
                "name": "member1",
                "description": "负责主要专业分析",
                "persona": {"dir": "./persona"},
            }
        ),
        encoding="utf-8",
    )

    spec = load_agent_template_package(manifest)

    assert spec.agent_card.id == "member1"
    assert spec.agent_card.name == "member1"
    assert spec.agent_card.description == "负责主要专业分析"


def test_agent_template_manifest_uses_name_and_description(tmp_path: Path) -> None:
    """Root templates expose package identity without serializing an AgentCard."""
    from openjiuwen.harness.resources.extension_loader import load_agent_template_package

    package_dir = tmp_path / "workplace-slim-coach"
    persona_dir = package_dir / "persona"
    persona_dir.mkdir(parents=True)
    (persona_dir / "workplace-slim-coach.md").write_text("# Workplace Slim Coach", encoding="utf-8")
    manifest = package_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "package_type": "agent_template",
                "name": "workplace-slim-coach",
                "description": "A personal weight-loss coach for busy office workers.",
                "persona": {"dir": "./persona"},
            }
        ),
        encoding="utf-8",
    )

    spec = load_agent_template_package(manifest)

    assert spec.agent_card.id == "workplace-slim-coach"
    assert spec.agent_card.name == "workplace-slim-coach"
    assert spec.agent_card.description == "A personal weight-loss coach for busy office workers."


def test_legacy_yaml_tool_aliases_remap_to_rails(tmp_path: Path) -> None:
    """Legacy tools short names become rails before PluginSpec resolve."""
    from openjiuwen.harness.resources.extension_loader import load_plugin_package
    from openjiuwen.harness.resources.extension_resolver import resolve_plugin_parts
    from openjiuwen.harness.schema.build_context import BuildContext

    manifest = tmp_path / "harness_config.yaml"
    manifest.write_text(
        "\n".join(
            [
                "id: legacy_short_tools",
                "name: legacy_short_tools",
                "tools:",
                "  - todo",
                "  - filesystem",
                "  - ask_user",
                "  - web_search",
                "rails:",
                "  - security",
            ]
        ),
        encoding="utf-8",
    )

    spec = load_plugin_package(manifest)
    assert [tool.type for tool in spec.tools] == ["core.web_search"]
    assert {rail.type for rail in spec.rails} == {
        "core.security",
        "core.task_planning",
        "core.sys_operation",
        "core.ask_user",
    }

    parts = resolve_plugin_parts(spec, BuildContext(language="cn"))
    assert len(parts.tools) == 1
    rail_names = {type(rail).__name__ for rail in parts.rails}
    assert "TaskPlanningRail" in rail_names
    assert "SysOperationRail" in rail_names
    assert "AskUserRail" in rail_names


def test_normalize_package_mcps_for_agent_template_manifest(tmp_path: Path) -> None:
    """Agent-template packages can expand ``manifest.mcps`` file refs independently."""
    from openjiuwen.harness.resources.extension_loader import normalize_package_mcps

    package_dir = tmp_path / "workplace-slim-coach"
    mcp_dir = package_dir / "mcps" / "slim-local-demo"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "slim-local-demo": {
                        "command": "python",
                        "args": ["local_demo_server.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    expanded = normalize_package_mcps(
        [{"file": "./mcps/slim-local-demo/mcp.json"}], package_dir
    )

    assert len(expanded) == 1
    assert expanded[0]["server_name"] == "slim-local-demo"
    assert expanded[0]["type"] == "stdio"
    assert expanded[0]["command"] == "python"
    assert Path(expanded[0]["cwd"]) == mcp_dir.resolve()


def _write_plugin_with_mcps(package_dir: Path, mcps: list[dict]) -> Path:
    """Minimal plugin package whose ``mcps`` list is under test."""
    mcp_dir = package_dir / "mcps" / "demo"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": "python",
                        "args": ["server.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = package_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "package_type": "plugin",
                "id": "connector-mcp-test",
                "mcps": mcps,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_build_mcp_specs_skips_connector_entries(tmp_path: Path) -> None:
    """``{"connector": "<name>"}`` is a host-managed ref — skip, keep file/dir specs."""
    from openjiuwen.harness.resources.extension_loader import load_plugin_package

    manifest = _write_plugin_with_mcps(
        tmp_path / "pkg",
        [
            {"file": "./mcps/demo/mcp.json"},
            {"connector": "amap"},
        ],
    )

    spec = load_plugin_package(manifest)

    assert len(spec.mcps) == 1
    assert spec.mcps[0].server_name == "demo"
    assert all(getattr(mcp, "server_name", None) != "amap" for mcp in spec.mcps)


def test_build_mcp_specs_rejects_connector_mixed_keys(tmp_path: Path) -> None:
    """Connector entry must be exactly ``{"connector": <non-empty str>}``."""
    from openjiuwen.harness.resources.extension_loader import load_plugin_package

    manifest = _write_plugin_with_mcps(
        tmp_path / "pkg",
        [{"connector": "amap", "command": "x"}],
    )

    with pytest.raises(ValueError, match="connector"):
        load_plugin_package(manifest)


@pytest.mark.parametrize(
    "connector_value",
    ["", 123, None],
)
def test_build_mcp_specs_rejects_invalid_connector_value(
    tmp_path: Path, connector_value: object
) -> None:
    """Empty or non-str ``connector`` values are rejected."""
    from openjiuwen.harness.resources.extension_loader import load_plugin_package

    manifest = _write_plugin_with_mcps(
        tmp_path / "pkg",
        [{"connector": connector_value}],
    )

    with pytest.raises(ValueError, match="connector"):
        load_plugin_package(manifest)
