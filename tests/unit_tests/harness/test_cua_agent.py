# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the cua desktop subagent factory and dispatch."""

from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.core.context_engine import ToolResultWindowProcessorConfig
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.rails.multimodal_context_summarizer_rail import MultimodalContextSummarizerRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine import PermissionEngine
from openjiuwen.harness.subagents.cua_agent import (
    CUA_AGENT_FACTORY_NAME,
    CUA_KILL_APP_PROMPT_SUFFIX,
    DEFAULT_CUA_AGENT_SYSTEM_PROMPT,
    build_cua_agent_config,
    create_cua_agent,
)
from openjiuwen.harness.tools.cua.cua_capabilities import CORE_CUA_TOOL_NAMES
from openjiuwen.harness.tools.cua.desktop_tools import CuaDesktopTool
from openjiuwen.harness.tools.cua.rails import (
    CuaProgressRail,
    CuaRepeatFailureRail,
    CuaRuntimeRail,
    CuaScreenshotDownscaleRail,
    CuaUserTakeoverRail,
)
from openjiuwen.harness.workspace.workspace import Workspace


def _create_dummy_model() -> Model:
    model_client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="test-key",
        api_base="http://test-base",
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model="test-model")
    return Model(model_client_config=model_client_config, model_config=model_config)


def _cua_tools(agent) -> dict:
    """The agent's cua tool instances, resolved the way dispatch resolves them."""
    tools = (Runner.resource_mgr.get_tool(card.id) for card in agent.deep_config.tools or [])
    return {tool.card.name: tool for tool in tools if isinstance(tool, CuaDesktopTool)}


def test_build_cua_agent_config_uses_cua_factory() -> None:
    spec = build_cua_agent_config(_create_dummy_model(), language="en")

    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "cua_agent"
    assert spec.system_prompt == DEFAULT_CUA_AGENT_SYSTEM_PROMPT["en"]
    assert spec.factory_name == CUA_AGENT_FACTORY_NAME


def test_create_cua_agent_registers_the_granted_native_tools() -> None:
    """The allowlist is enforced by construction: an ungranted tool is never
    built, so the model cannot see or call it."""
    agent = create_cua_agent(_create_dummy_model(), language="en")

    tools = _cua_tools(agent)
    # Default grant: read-only core + input + app_lifecycle.
    assert {"get_window_state", "click", "type_text", "launch_app"} <= set(tools)
    assert "kill_app" not in tools
    assert not agent.deep_config.mcps

    core_only = _cua_tools(create_cua_agent(_create_dummy_model(), language="en", cua_capabilities=[]))
    assert set(core_only) == set(CORE_CUA_TOOL_NAMES)


def test_the_preflight_probes_the_desktop_the_tools_use() -> None:
    """A preflight against a different backend could pass while the tools fail."""
    agent = create_cua_agent(_create_dummy_model(), language="en")

    [runtime] = [rail for rail in agent._pending_rails if isinstance(rail, CuaRuntimeRail)]
    assert _cua_tools(agent)
    assert all(tool._desktop is runtime._desktop for tool in _cua_tools(agent).values())


def test_every_agent_gets_its_own_desktop_tools() -> None:
    # Two agents in one process must not share zoom state or tool instances;
    # this is also why cua_instance_key has nothing left to isolate.
    first = _cua_tools(create_cua_agent(_create_dummy_model(), language="en"))
    second = _cua_tools(create_cua_agent(_create_dummy_model(), language="en", cua_instance_key="run7"))

    assert first["click"] is not second["click"]
    assert first["click"]._desktop is not second["click"]._desktop


def test_cua_rails_are_installed_on_the_bare_tool_names() -> None:
    """Perception is screenshot-only, so the image rails are always on, and
    every cua rail must key on the bare names the tools are registered under."""
    agent = create_cua_agent(_create_dummy_model(), language="en")

    rails = agent._pending_rails
    cua_rails = [r for r in rails if isinstance(r, (CuaRepeatFailureRail, CuaProgressRail, CuaScreenshotDownscaleRail))]
    assert len(cua_rails) == 3
    assert {r._tool_prefix for r in cua_rails} == {""}
    summarizers = [r for r in rails if isinstance(r, MultimodalContextSummarizerRail)]
    assert len(summarizers) == 1
    # Screenshot retention follows cua_snapshot_keep_last_k (default 3) so a
    # two-window task keeps both windows' screens, mirroring the tool window.
    assert summarizers[0]._screenshots_to_keep == 3


def test_default_wiring_windows_cua_capture_results() -> None:
    """An old capture describes a screen that has since changed, so the
    sliding window must be on by default or the agent burns its context on
    stale history. Retention must default to more than one: a two-window task
    needs both windows' latest captures in context at once."""
    agent = create_cua_agent(_create_dummy_model(), language="en")

    context_rails = [rail for rail in agent._pending_rails if isinstance(rail, ContextProcessorRail)]
    assert len(context_rails) == 1
    assert context_rails[0]._preset is False
    processors = context_rails[0]._user_processors
    assert len(processors) == 1
    key, cfg = processors[0]
    assert key == "ToolResultWindowProcessor"
    assert isinstance(cfg, ToolResultWindowProcessorConfig)
    # Pin the intended contract literally (not against a source constant) so a
    # regression like a renamed tool silently dropping out is actually caught.
    assert cfg.tool_names == ["get_window_state", "zoom"]
    assert cfg.keep_last_k == 3


def test_snapshot_keep_last_k_drives_window_and_screenshot_retention() -> None:
    """One knob rules both evictions: the tool-result window and the retained
    screenshot images fail the same way on multi-window tasks, so they must
    move in lockstep rather than drift apart."""
    agent = create_cua_agent(_create_dummy_model(), language="en", cua_snapshot_keep_last_k=5)

    context_rails = [rail for rail in agent._pending_rails if isinstance(rail, ContextProcessorRail)]
    _, cfg = context_rails[0]._user_processors[0]
    assert cfg.keep_last_k == 5
    summarizer_rails = [r for r in agent._pending_rails if isinstance(r, MultimodalContextSummarizerRail)]
    assert summarizer_rails[0]._screenshots_to_keep == 5


def test_snapshot_keep_last_k_is_validated_at_config_time() -> None:
    # factory_kwargs are not resolved until the subagent is materialized, so an
    # unusable retention count must fail when the config is built, not mid-run.
    with pytest.raises(ValueError, match="cua_snapshot_keep_last_k"):
        build_cua_agent_config(_create_dummy_model(), cua_snapshot_keep_last_k=0)
    with pytest.raises(ValueError, match="cua_snapshot_keep_last_k"):
        create_cua_agent(_create_dummy_model(), cua_snapshot_keep_last_k=True)


def test_build_cua_agent_config_forwards_factory_kwargs() -> None:
    spec = build_cua_agent_config(_create_dummy_model(), cua_snapshot_keep_last_k=2, cua_instance_key="run7")

    assert spec.factory_kwargs["cua_snapshot_keep_last_k"] == 2
    assert spec.factory_kwargs["cua_instance_key"] == "run7"


def test_caller_context_processor_rail_suppresses_cua_injection() -> None:
    caller_rail = ContextProcessorRail(preset=False)
    agent = create_cua_agent(_create_dummy_model(), language="en", rails=[caller_rail])

    context_rails = [rail for rail in agent._pending_rails if isinstance(rail, ContextProcessorRail)]
    assert context_rails == [caller_rail]


def test_create_cua_agent_rejects_unknown_capabilities() -> None:
    with pytest.raises(ValueError, match="Unsupported cua capabilities: browser"):
        create_cua_agent(_create_dummy_model(), cua_capabilities=["browser"])


def test_create_subagent_uses_cua_agent_factory(tmp_path) -> None:
    workspace_root = tmp_path / "parent_workspace"
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[build_cua_agent_config(_create_dummy_model(), language="en")],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.cua_agent.create_cua_agent",
        return_value=factory_result,
    ) as mock_create_cua_agent:
        sub = parent.create_subagent("cua_agent", "sub_session_id")

    assert sub is factory_result
    mock_create_cua_agent.assert_called_once()
    call_kwargs = mock_create_cua_agent.call_args.kwargs
    assert call_kwargs["card"].name == "cua_agent"
    assert Path(call_kwargs["workspace"].root_path).name == "sub_session_id"


@pytest.mark.parametrize("language", ["en", "cn"])
def test_prompt_claims_electron_apps_for_the_desktop_agent(language: str) -> None:
    """Electron apps must not be deferred to the browser agent.

    The prompt tells the agent that browser work belongs to browser_agent.
    Read without a carve-out, that also disowns Discord/Slack/VS Code, which
    are Chromium-rendered but are native windows the browser agent cannot
    reach by URL — so the task would be handed to an agent that must fail it.
    """
    prompt = DEFAULT_CUA_AGENT_SYSTEM_PROMPT[language]
    assert "Electron" in prompt
    for app in ("Discord", "Slack", "VS Code"):
        assert app in prompt, f"{language} prompt no longer names {app} as desktop work"


@pytest.mark.parametrize(("language", "marker"), [("en", "top-left corner"), ("cn", "左上角起算")])
def test_prompt_states_the_window_local_pixel_convention(language: str, marker: str) -> None:
    """x,y are pixels in the window's latest screenshot. A model that reads
    them as screen coordinates misses by the window's offset, silently."""
    prompt = DEFAULT_CUA_AGENT_SYSTEM_PROMPT[language]
    assert marker in prompt
    assert "from_zoom=true" in prompt
    assert "-32000" in prompt, "minimized-window sentinel dropped from the prompt"


@pytest.mark.parametrize(
    ("language", "marker", "margin_marker"),
    [("en", "fails silently", "30+ px"), ("cn", "静默失效", "30 像素")],
)
def test_prompt_warns_that_region_edges_are_unreliable(language: str, marker: str, margin_marker: str) -> None:
    """Coordinate actions starting outside a visually-estimated region no-op.

    VLM edge localization is off by tens of pixels, and a drag whose
    mouse-down lands just outside a surface (canvas, slider, drop zone)
    produces no ink, no error, and no signal — live-diagnosed: five runs of
    misdiagnosis because strokes started 20px above a drawing area. The
    prompt must both demand an inside-the-region margin and teach that a
    silent no-effect result means aim, not delivery.
    """
    prompt = DEFAULT_CUA_AGENT_SYSTEM_PROMPT[language]
    assert marker in prompt
    assert margin_marker in prompt


def test_user_takeover_pause_is_off_by_default_and_can_be_enabled() -> None:
    """The pause is opt-in for now: holding every action while the user
    touches the machine made interactive runs stall, so the rail stays
    available but is not installed unless asked for."""
    agent = create_cua_agent(_create_dummy_model(), language="en")
    assert not any(isinstance(r, CuaUserTakeoverRail) for r in agent._pending_rails)

    agent = create_cua_agent(_create_dummy_model(), language="en", cua_pause_on_user_input=True)
    [takeover] = [r for r in agent._pending_rails if isinstance(r, CuaUserTakeoverRail)]
    assert takeover._tool_prefix == ""


def test_build_cua_agent_config_forwards_pause_on_user_input() -> None:
    spec = build_cua_agent_config(_create_dummy_model(), cua_pause_on_user_input=True)
    assert spec.factory_kwargs["cua_pause_on_user_input"] is True
    assert build_cua_agent_config(_create_dummy_model()).factory_kwargs["cua_pause_on_user_input"] is False


def test_superseded_captures_keep_only_a_short_inline_preview() -> None:
    """A superseded capture describes a screen that has since changed, so a
    long inline preview is dead weight. The placeholder still carries the
    offload handle, so the full result stays reachable when wanted."""
    agent = create_cua_agent(_create_dummy_model(), language="en")

    context_rails = [rail for rail in agent._pending_rails if isinstance(rail, ContextProcessorRail)]
    _, cfg = context_rails[0]._user_processors[0]
    assert cfg.trim_size == 200


def test_build_cua_agent_config_forwards_permissions_and_host_to_the_factory() -> None:
    host = object()
    spec = build_cua_agent_config(
        _create_dummy_model(),
        permissions={"enabled": True, "schema": "tiered_policy"},
        permission_host=host,
    )

    assert spec.factory_kwargs["permissions"] == {"enabled": True, "schema": "tiered_policy"}
    assert spec.factory_kwargs["permission_host"] is host


def test_factory_kwargs_omit_permission_keys_unless_provided() -> None:
    # Older configs must keep their exact factory_kwargs shape.
    spec = build_cua_agent_config(_create_dummy_model())

    assert "permissions" not in spec.factory_kwargs
    assert "permission_host" not in spec.factory_kwargs


@pytest.mark.parametrize("language", ["cn", "en"])
def test_kill_app_guidance_follows_the_capability_grant(language: str) -> None:
    # The prompt must not teach a tool the agent was not given; when an
    # operator grants app_termination, the cooperative-close-first rule rides
    # along with it on both construction paths.
    default_agent = create_cua_agent(_create_dummy_model(), language=language)
    assert "kill_app" not in default_agent.deep_config.system_prompt
    assert "kill_app" not in build_cua_agent_config(_create_dummy_model(), language=language).system_prompt

    granted = ["input", "app_lifecycle", "app_termination"]
    granted_agent = create_cua_agent(_create_dummy_model(), language=language, cua_capabilities=granted)
    granted_spec = build_cua_agent_config(_create_dummy_model(), language=language, cua_capabilities=granted)
    expected = DEFAULT_CUA_AGENT_SYSTEM_PROMPT[language] + CUA_KILL_APP_PROMPT_SUFFIX[language]
    assert granted_agent.deep_config.system_prompt == expected
    assert granted_spec.system_prompt == expected
    assert "kill_app" in _cua_tools(granted_agent)


def _caller_permissions() -> dict:
    # A permissive caller: allow-all defaults, kill_app allowed by name and by
    # an approval override, plus a rule of its own that must survive the merge.
    return {
        "enabled": True,
        "defaults": {"*": "allow"},
        "tools": {"kill_app": "allow"},
        "rules": [
            {
                "id": "caller_no_secret_typing",
                "tools": ["type_text"],
                "match_type": "os_control",
                "pattern": "re:(?i)password",
                "action": "deny",
            }
        ],
        "approval_overrides": [{"id": "caller_ok_kill", "tools": ["kill_app"], "pattern": "*"}],
    }


def _engine_for(agent) -> PermissionEngine:
    return PermissionEngine(agent.deep_config.permissions)


def test_cua_permission_rules_merge_into_caller_permissions_without_replacing_them() -> None:
    caller = _caller_permissions()
    agent = create_cua_agent(_create_dummy_model(), language="en", permissions=caller)

    rule_ids = [r["id"] for r in agent.deep_config.permissions["rules"]]
    assert "caller_no_secret_typing" in rule_ids
    assert {"cua_desktop_force_terminate_app", "cua_desktop_launch_app", "cua_desktop_type_text"} <= set(rule_ids)
    assert agent.deep_config.permissions["defaults"] == {"*": "allow"}
    # The caller's dict is theirs: merging must not edit it in place.
    assert [r["id"] for r in caller["rules"]] == ["caller_no_secret_typing"]


def test_kill_app_deny_survives_a_permissive_caller_config() -> None:
    """Force-killing loses unsaved work; no caller allow, default or approval
    override may lift that floor."""
    agent = create_cua_agent(_create_dummy_model(), language="en", permissions=_caller_permissions())

    level, matched = _engine_for(agent).check_tool_permission_directly("kill_app", {"pid": 844})

    assert level == PermissionLevel.DENY
    assert "cua_desktop_force_terminate_app" in str(matched)


def test_desktop_typing_and_launching_ask_while_caller_rules_still_apply() -> None:
    engine = _engine_for(create_cua_agent(_create_dummy_model(), language="en", permissions=_caller_permissions()))

    typed, typed_rule = engine.check_tool_permission_directly("type_text", {"text": "hello", "pid": 5})
    launched, _ = engine.check_tool_permission_directly("launch_app", {"name": "notepad"})
    secret, secret_rule = engine.check_tool_permission_directly("type_text", {"text": "my password"})
    clicked, _ = engine.check_tool_permission_directly("click", {"pid": 5, "x": 1, "y": 2})

    assert typed == PermissionLevel.ASK and "cua_desktop_type_text" in str(typed_rule)
    assert launched == PermissionLevel.ASK
    assert secret == PermissionLevel.DENY and "caller_no_secret_typing" in str(secret_rule)
    assert clicked == PermissionLevel.ALLOW  # no desktop rule: the caller's defaults govern


def test_no_permissions_means_no_engine() -> None:
    # Without a caller policy the agent keeps running engine-less: turning the
    # engine on here would put ASK prompts inside task_tool delegations, where
    # they cannot reach the user.
    agent = create_cua_agent(_create_dummy_model(), language="en")

    assert agent.deep_config.permissions is None
