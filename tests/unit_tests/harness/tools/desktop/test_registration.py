"""Public registration and serialized configuration, without native execution."""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openjiuwen.core.session.agent import Session
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.resources import load_plugin_package
from openjiuwen.harness.tools.desktop import DesktopSettings, DirectComputerUseTool


@pytest.mark.asyncio
async def test_import_plugin_builds_disabled_tool_without_worker(tmp_path):
    manifest = tmp_path / "plugin.yaml"
    manifest.write_text(
        "id: scoped-desktop\n"
        "tools:\n"
        "  - type: harness.tool.import\n"
        "    params:\n"
        "      import_path: openjiuwen.harness.tools.desktop.DirectComputerUseTool\n"
        "      settings:\n"
        "        enabled: false\n"
    )
    spec = load_plugin_package(manifest)
    assert not spec.rails and not spec.mcps and not spec.prompt_sections
    tool = spec.tools[0].build(language="en")
    assert isinstance(tool, DirectComputerUseTool)
    assert tool.card.name == "computer_use"
    assert tool.settings == DesktopSettings()
    result = await tool.invoke({"operation": "observe"}, session=Session("registration"))
    assert not result.success and result.error == "desktop disabled"
    assert tool._state is None
    await tool.close()


@pytest.mark.asyncio
async def test_public_agent_loads_and_unloads_tool_plugin(tmp_path):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="offline-test", api_base="http://localhost:1"
        ),
        model_config=ModelRequestConfig(model="offline-test"),
    )
    manifest = tmp_path / "harness.yaml"
    manifest.write_text(
        "id: scoped-desktop\n"
        "tools:\n"
        "  - type: harness.tool.import\n"
        "    params:\n"
        "      import_path: openjiuwen.harness.tools.desktop.DirectComputerUseTool\n"
        "      settings: {enabled: false}\n"
    )
    agent = create_deep_agent(
        model=model,
        enable_task_loop=False,
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_read_image_multimodal=False,
        auto_create_workspace=False,
    )
    record = await agent.load_plugin(str(manifest))
    try:
        assert any(card.name == "computer_use" for card in agent.ability_manager.list())
    finally:
        await agent.unload_extension(record)
    assert all(card.name != "computer_use" for card in agent.ability_manager.list())


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": True},
        {"enabled": "true"},
        {"execution_mode": "delegated"},
        {"endpoint": {"api_key": "not-supported"}},
        {"max_batch_actions": 9},
        {"budgets": {"max_calls": 1}},
    ],
)
def test_serialized_settings_are_validated_before_registration(settings):
    with pytest.raises(ValidationError):
        DirectComputerUseTool(settings)


def test_tool_import_does_not_load_native_frameworks():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "from openjiuwen.harness.tools.desktop import DesktopSettings, DirectComputerUseTool; "
                "DirectComputerUseTool(DesktopSettings()); "
                "print(json.dumps(sorted(set(sys.modules) & "
                "{'Quartz', 'AppKit', 'ApplicationServices', 'ScreenCaptureKit'})))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
