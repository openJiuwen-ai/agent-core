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
    """Agent-template packages can expand ``manifest.mcps`` dir refs independently."""
    from openjiuwen.harness.resources.extension_loader import normalize_package_mcps

    package_dir = tmp_path / "workplace-slim-coach"
    mcp_dir = package_dir / "mcps"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcps.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "slim-local-demo",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "python",
                        "args": ["mcps/local_demo_server.py"],
                        "cwd": ".",
                        "server_id": "slim_local_demo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    expanded = normalize_package_mcps([{"dir": "./mcps"}], package_dir)

    assert len(expanded) == 1
    assert expanded[0]["server_name"] == "slim-local-demo"
    assert expanded[0]["type"] == "stdio"
    assert expanded[0]["server_id"] == "slim_local_demo"


def _write_agent_template_package(package_dir: Path, manifest: dict) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return package_dir / "manifest.json"


def test_agent_template_flat_identity_derives_card_id_from_dir(tmp_path: Path) -> None:
    """Flat ``name``/``description`` manifests get their card id from the dir name."""
    from openjiuwen.harness.resources.extension_loader import load_agent_template_package

    manifest = _write_agent_template_package(
        tmp_path / "member1",
        {
            "packageType": "agent_template",
            "name": "成员一",
            "description": "专家团成员",
        },
    )

    spec = load_agent_template_package(manifest)

    assert spec.agent_card.id == "member1"
    assert spec.agent_card.name == "成员一"
    assert spec.agent_card.description == "专家团成员"


def test_agent_template_flat_identity_requires_name(tmp_path: Path) -> None:
    """Without agentCard, a missing/blank ``name`` is a hard error."""
    from openjiuwen.harness.resources.extension_loader import load_agent_template_package

    manifest = _write_agent_template_package(
        tmp_path / "member1",
        {"packageType": "agent_template", "description": "缺名字"},
    )

    with pytest.raises(ValueError, match="name"):
        load_agent_template_package(manifest)


def test_agent_template_flat_identity_requires_description(tmp_path: Path) -> None:
    """Without agentCard, a missing ``description`` is a hard error."""
    from openjiuwen.harness.resources.extension_loader import load_agent_template_package

    manifest = _write_agent_template_package(
        tmp_path / "member1",
        {"packageType": "agent_template", "name": "成员一"},
    )

    with pytest.raises(ValueError, match="description"):
        load_agent_template_package(manifest)


def test_agent_template_agent_card_wins_over_flat_fields(tmp_path: Path) -> None:
    """Nested agentCard keeps full priority when both shapes are present."""
    from openjiuwen.harness.resources.extension_loader import load_agent_template_package

    manifest = _write_agent_template_package(
        tmp_path / "member1",
        {
            "packageType": "agent_template",
            "name": "平铺名字（应被忽略）",
            "description": "平铺描述（应被忽略）",
            "agentCard": {"id": "member1", "name": "嵌套名字", "description": "嵌套描述"},
        },
    )

    spec = load_agent_template_package(manifest)

    assert spec.agent_card.id == "member1"
    assert spec.agent_card.name == "嵌套名字"
    assert spec.agent_card.description == "嵌套描述"
