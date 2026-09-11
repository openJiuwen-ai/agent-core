# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manifest-driven harness factory tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, ModelSpec, RailSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, McpServerSpec, PromptSectionSpec
from openjiuwen.harness_protocol import HostCapability, McpTransport
from openjiuwen.harness_providers import PROVIDER_NAMES, build_harness_context, create_harness, resolve_provider
from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness
from openjiuwen.harness_providers.codex import CodexHarness
from openjiuwen.harness_providers.dsh import DshHarness
from openjiuwen.harness_providers.factory import manifest_provider_config
from openjiuwen.harness_providers.native import DeepAgentHarness
from tests.test_logger import logger


def _manifest(*, with_tools: bool = False) -> AgentTemplateSpec:
    return AgentTemplateSpec(
        agent_card=AgentCard(id="expert", name="expert", description="An expert agent"),
        model=ModelSpec(
            model_client_config=ModelClientConfig(client_provider="openai", api_key="secret", api_base="https://llm"),
            model_request_config=ModelRequestConfig(model="gpt-x"),
        ),
        prompt_sections=[
            PromptSectionSpec(name="rules", content={"en": "Follow {{language}} rules.", "cn": "遵守规则"}, priority=20),
            PromptSectionSpec(name="identity", content={"en": "You are an expert."}, priority=10),
        ],
        mcps=[McpServerSpec(type="stdio", server_name="fs", command="mcp-fs", args=["--root", "/tmp"])],
        tools=[BuiltinToolSpec(type="core.web_search")] if with_tools else [],
    )


def test_resolve_provider_covers_every_declared_name() -> None:
    for name in PROVIDER_NAMES:
        provider = resolve_provider(name)
        logger.info("provider %s -> %s", name, provider.card.name)
        assert provider.card.name
    with pytest.raises(ValueError, match="unknown harness provider"):
        resolve_provider("nope")


def test_manifest_provider_config_maps_the_model_per_provider() -> None:
    manifest = _manifest()
    claude = manifest_provider_config(manifest, provider="claudecode")
    assert claude["model"] == {"model": "gpt-x", "api_base": "https://llm", "api_key": "secret"}
    codex = manifest_provider_config(manifest, provider="codex", config={"cwd": "/w"})
    assert codex["cwd"] == "/w"
    assert codex["model"]["provider"].lower() == "openai"
    dsh = manifest_provider_config(manifest, provider="dsh", config={"model": "custom"})
    assert dsh["model"] == "custom" and dsh["base_url"] == "https://llm"
    native = manifest_provider_config(manifest, provider="native", language="en")
    assert native["language"] == "en"
    assert native["agent_template"]["agent_card"]["name"] == "expert"


def test_third_party_providers_reject_deepagent_only_sections() -> None:
    manifest = _manifest(with_tools=True)
    with pytest.raises(ValueError, match="'tools' depends on the DeepAgent framework"):
        create_harness(manifest, provider="codex")
    railed = _manifest().model_copy(update={"rails": [RailSpec(type="core.ask_user")]})
    with pytest.raises(ValueError, match="'rails'"):
        create_harness(railed, provider="claudecode")


def test_create_harness_returns_the_requested_implementation() -> None:
    manifest = _manifest()
    assert isinstance(create_harness(manifest, provider="claudecode", config={"cwd": "/tmp"}), ClaudeCodeHarness)
    assert isinstance(create_harness(manifest, provider="codex"), CodexHarness)
    assert isinstance(create_harness(manifest, provider="dsh"), DshHarness)
    native = create_harness(_manifest(with_tools=True), provider="native", language="en")
    assert isinstance(native, DeepAgentHarness)
    with pytest.raises(ValueError, match="unknown harness provider"):
        create_harness(manifest, provider="nope")  # type: ignore[arg-type]


def test_build_harness_context_renders_prompt_and_mcp_servers() -> None:
    manifest = _manifest()
    context = build_harness_context(
        manifest,
        provider="codex",
        host_session_id="host-1",
        language="en",
        cwd="/work",
        extra_system_prompt="Team rules.",
    )
    assert context.agent_name == "expert" and context.agent_id == "expert"
    assert context.system_prompt == "You are an expert.\n\nFollow en rules.\n\nTeam rules."
    assert context.cwd == "/work"
    assert [server.name for server in context.mcp_servers] == ["fs"]
    assert context.mcp_servers[0].transport is McpTransport.STDIO
    assert context.mcp_servers[0].command == ("mcp-fs", "--root", "/tmp")
    assert HostCapability.MCP_SERVERS in context.host_capabilities

    native_context = build_harness_context(manifest, provider="native", host_session_id="host-1", extra_system_prompt="x")
    assert native_context.system_prompt == "x"
    assert native_context.mcp_servers == ()


def test_create_harness_loads_a_manifest_package(tmp_path: Path) -> None:
    package = tmp_path / "expert"
    (package / "persona").mkdir(parents=True)
    (package / "persona" / "identity.md").write_text("# Identity\n\nI am packaged.\n", encoding="utf-8")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "package_type": "agent_template",
                "name": "packaged_expert",
                "description": "Packaged expert.",
                "persona": {"dir": "persona"},
            }
        ),
        encoding="utf-8",
    )
    harness = create_harness(package, provider="dsh")
    assert isinstance(harness, DshHarness)
    context = build_harness_context(package, provider="dsh", host_session_id="host-1", language="en")
    assert "I am packaged." in context.system_prompt
    assert context.agent_name == "packaged_expert"


def test_native_v2_factory_uses_native_harness_template_construction():
    from openjiuwen.agent_teams.harness import NativeHarnessProtocolAdapter
    from openjiuwen.harness_protocol import HarnessProvider, HarnessCapability

    manifest = _manifest(with_tools=True)
    provider = resolve_provider("native_v2")
    assert isinstance(provider, HarnessProvider)
    assert provider.card.name == "native_v2"
    assert provider.card.supports(HarnessCapability.CHECKPOINT)
    harness = create_harness(manifest, provider="native_v2", language="en",
                             config={"deep_agent": {"max_iterations": 9}, "event_buffer_capacity": 32})
    assert isinstance(harness, NativeHarnessProtocolAdapter)
    assert harness.native_harness is None
    assert harness._spec.card == manifest.agent_card
    assert harness._spec.model == manifest.model
    assert harness._spec.agent_template_spec == manifest.model_dump(mode="json")
    assert harness._spec.language == "en"
    assert harness._spec.max_iterations == 9
    assert harness.event_buffer_config.capacity == 32
    context = build_harness_context(manifest, provider="native_v2", host_session_id="session", extra_system_prompt="extra")
    assert context.system_prompt == "extra"
    assert not context.mcp_servers
    with pytest.raises(ValueError, match="unknown native_v2"):
        provider.create({"unknown": True})
    with pytest.raises(ValueError, match="positive integer"):
        provider.create({"event_buffer_capacity": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,relative,loader", [
    ("claudecode", ".claude/skills", "load_claude_sdk"),
    ("codex", ".agents/skills", "load_codex_sdk"),
    ("dsh", ".dsh/skills", "_load_dsh_sdk"),
])
async def test_manifest_skills_are_copied_at_start_before_sdk_launch(tmp_path, monkeypatch, provider, relative, loader):
    import importlib
    from openjiuwen.harness.schema.extension_spec import SkillSpec
    from openjiuwen.harness_protocol import HarnessContext
    from tests.unit_tests.harness_providers.test_skills import bundle

    source = bundle(tmp_path / "source")
    project = tmp_path / "project"
    project.mkdir()
    manifest = _manifest().model_copy(update={"mcps": [], "skills": [SkillSpec(dir=str(source))]})
    harness = create_harness(manifest, provider=provider, config={"skill_conflict": "replace"})
    assert not (project / relative).exists()
    assert harness._config.skills[0].dir == str(source)
    module = importlib.import_module(f"openjiuwen.harness_providers.{provider}.harness")
    def fail_loading():
        assert (project / relative / "example/scripts/run.sh").is_file()
        raise RuntimeError("SDK launch reached")
    monkeypatch.setattr(module, loader, fail_loading)
    with pytest.raises(RuntimeError, match="SDK launch reached"):
        await harness.start(HarnessContext(agent_name="test", agent_id="test", host_session_id="test", system_prompt="", cwd=str(project)))
