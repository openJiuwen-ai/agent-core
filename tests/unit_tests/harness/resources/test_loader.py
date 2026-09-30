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
