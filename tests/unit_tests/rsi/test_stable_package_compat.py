"""Exercise the stable loader and runtime, rather than Plugin API aliases."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
import yaml

from openjiuwen.agent_teams.paths import (
    get_openjiuwen_home,
    reset_task_openjiuwen_home,
    set_task_openjiuwen_home,
)
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.rsi.harness_rsi.package_compat import load_plugin_package, resolve_plugin_parts


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_json_package_loads_and_unloads_through_stable_runtime(tmp_path, native):
    package = tmp_path / "package"
    package.mkdir()
    (package / "manifest.json").write_text(json.dumps({
        ("packageType" if native else "package_type"): "plugin",
        "id": "stable-test",
        "prompt_sections": [{
            "name": "stable-rsi-policy",
            "content": {"en": "Check the final answer."} if native else "Check the final answer.",
        }],
    }), encoding="utf-8")
    agent = create_deep_agent(model=MagicMock(), workspace=str(tmp_path / "work"),
                              agent_ras=False, auto_create_workspace=False)
    record = await agent.load_plugin_spec(load_plugin_package(package))
    assert "Check the final answer." in agent.system_prompt_builder.build()
    await agent.unload_extension(record)
    assert "Check the final answer." not in agent.system_prompt_builder.build()
    agent.ability_manager.teardown_tools()


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest_name", ["harness.yaml", "legacy.yaml"])
async def test_legacy_relative_tool_is_built_from_its_package(tmp_path, manifest_name):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "probe.py").write_text('''
from openjiuwen.core.foundation.tool import Tool, ToolCard
class Probe(Tool):
    def __init__(self):
        super().__init__(ToolCard(name="stable_probe", description="Probe", input_params={"type": "object"}))
    async def invoke(self, inputs, **kwargs):
        return "package tool executed"
    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)
''', encoding="utf-8")
    manifest = tmp_path / manifest_name
    manifest.write_text(yaml.safe_dump({
        "name": "legacy", "tools": [{"type": "harness.tool.file",
        "params": {"file_path": "tools/probe.py", "class_name": "Probe"}}],
    }), encoding="utf-8")
    package = tmp_path if manifest_name == "harness.yaml" else manifest
    parts = resolve_plugin_parts(load_plugin_package(package), BuildContext(language="en"))
    assert await parts.tools[0].invoke({}) == "package tool executed"


@pytest.mark.parametrize("declaration", [
    {"memory": {"enabled": True}},
    {"tools": [{"file": "../outside.py"}]},
    {"mcps": [{"command": "external-process"}]},
])
def test_unsupported_or_escaping_declarations_are_rejected(tmp_path, declaration):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "id": "invalid", **declaration,
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        load_plugin_package(tmp_path)


@pytest.mark.asyncio
async def test_concurrent_case_homes_do_not_replace_the_host_default(tmp_path):
    original = get_openjiuwen_home()

    async def case(name):
        path = tmp_path / name
        token = set_task_openjiuwen_home(path)
        try:
            await asyncio.sleep(0)
            assert get_openjiuwen_home() == path
        finally:
            reset_task_openjiuwen_home(token)

    await asyncio.gather(case("a"), case("b"))
    assert get_openjiuwen_home() == original


def test_factory_cannot_bypass_the_host_iteration_ceiling(tmp_path):
    with pytest.raises(ValueError, match="ceiling"):
        create_deep_agent(model=MagicMock(), workspace=str(tmp_path), max_iterations=3,
                          max_iterations_hard_limit=2, auto_create_workspace=False, agent_ras=False)


@pytest.mark.asyncio
async def test_iteration_ceiling_retains_real_tool_evidence_without_a_final_model_call(tmp_path, monkeypatch):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.runner import Runner
    from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import RSISysOperationRail
    from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_tool_call_response

    client = MockLLMModel()
    client.set_responses([create_tool_call_response(
        "write_file", '{"file_path":"answer.txt","content":"partial evidence"}',
    )])
    monkeypatch.setattr("openjiuwen.core.foundation.llm.model.create_model_client", lambda **kwargs: client)
    model = Model(ModelClientConfig(client_provider="OpenAI", api_key="test-only", api_base="http://unused"),
                  ModelRequestConfig(model_name="controlled"))
    agent = create_deep_agent(model=model, workspace=str(tmp_path), max_iterations=1,
                              max_iterations_hard_limit=1, on_max_iterations="return_observations",
                              enable_read_image_multimodal=False, agent_ras=False,
                              rails=[RSISysOperationRail()])
    # A later soft-budget update cannot bypass the evaluator-owned ceiling.
    agent.react_config.max_iterations = 10
    try:
        result = await Runner.run_agent(agent, {"query": "Write answer.txt"}, session="bounded-rsi")
        assert result["result_type"] == "error"
        assert result["termination_reason"] == "max_iterations"
        assert result["iterations_used"] == 1
        assert result["truncated_by_max_iterations"] is True
        assert result["observations"][0]["name"] == "write_file"
        assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "partial evidence"
        assert client.call_count == 1
    finally:
        agent.ability_manager.teardown_tools()
        await Runner.stop()
