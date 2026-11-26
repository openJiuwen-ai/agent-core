# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concise built-in prompts preserve live catalogs and extension contracts."""

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections.progressive_tool_rail import (
    build_progressive_tool_rules_prompt,
    render_deferred_tool_catalog_delta,
    render_deferred_tool_catalog_snapshot,
)
from openjiuwen.harness.prompts.sections.reload import build_reload_section
from openjiuwen.harness.prompts.sections.task_tool import build_task_section
from openjiuwen.harness.prompts.tools import build_tool_card, get_tool_description
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    DEFAULT_BROWSER_CAPABILITIES,
)


pytestmark = pytest.mark.level0


def test_deferred_catalog_keeps_constraints_across_updates() -> None:
    catalog = {
        "cron": "Manage standalone scheduled tasks and reminders.",
        "heartbeat_create_job": "Resume this task later; use cron for independent schedules.",
        "submit_goal_report": "Only call when the user message contains a <goal_task> tag.",
        "custom_write": "Requires explicit approval before writing.",
    }
    texts = (
        build_progressive_tool_rules_prompt("en", catalog),
        render_deferred_tool_catalog_snapshot(catalog, language="en"),
        render_deferred_tool_catalog_delta(catalog, {}, ["removed_tool"], language="en"),
    )
    for text in texts:
        for name, purpose in catalog.items():
            assert f"**{name}**" in text
            assert purpose in text
    changed = {"cron": "Custom cron: confirm before scheduling."}
    update = render_deferred_tool_catalog_delta({}, changed, [], language="en")
    assert changed["cron"] in update
    assert "Latest state overrides" in texts[0]


def test_context_recovery_uses_explicit_path_not_handle() -> None:
    prompt = build_reload_section("en").render("en")
    assert "handle is an ID, never a path" in prompt
    assert "Without path, do not guess" in prompt
    assert "in_memory" in prompt


def test_host_task_extension_survives_without_a_profile() -> None:
    section = build_task_section("en", extension_content="CUSTOM HOST RULE")
    assert section.render("en").endswith("CUSTOM HOST RULE\n")
    assert "parent synthesizes, verifies and delivers" in section.render("en")


def test_model_and_execution_metadata_share_the_same_provider() -> None:
    card = build_tool_card("task_tool", "test-task-tool", language="en")
    assert card.description == get_tool_description("task_tool", "en")
    assert "{available_agents}" in card.description
    assert "resume_task_id" in card.input_params["properties"]
    assert card.input_params["required"] == ["subagent_type", "task_description"]


@pytest.mark.parametrize(
    ("language", "rules"),
    [
        ("en", (
            "Browser interaction uses browser_agent",
            "summary, evidence and partial results",
            "retryable=true",
        )),
        ("cn", ("实际浏览器操作使用 browser_agent", "摘要、证据和部分结果", "retryable=true")),
    ],
)
def test_task_tool_exposes_browser_routing_and_resume_checks_in_each_language(
    language: str, rules: tuple[str, ...],
) -> None:
    card = build_tool_card("task_tool", "test-task-tool", language=language)
    for rule in rules:
        assert rule in card.description


def test_live_catalog_keeps_custom_policy_and_browser_capabilities() -> None:
    rail = SubagentRail()
    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    browser = SubAgentConfig(
        agent_card=AgentCard(name="browser_agent", description="Browser policy"),
        system_prompt="Browser task",
    )
    custom_tool = SimpleNamespace(card=ToolCard(name="custom_write", description="Custom write"))
    custom = SubAgentConfig(
        agent_card=AgentCard(name="custom_worker", description="Confirm before writing."),
        system_prompt="Custom task",
        tools=[custom_tool],
    )
    catalog = rail._build_available_agents_description([browser, custom])
    assert "- custom_worker: Confirm before writing. (Tools: custom_write)" in catalog
    for capability in DEFAULT_BROWSER_CAPABILITIES:
        assert f"{capability.name}: {capability.description}" in catalog
        assert f"(Tools: {', '.join(capability.tool_names)})" not in catalog
    assert rail._build_available_agents_description([custom]).count("browser_agent") == 0
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    assert "(Tools:" in rail._build_available_agents_description([browser])


@pytest.mark.parametrize("persistent_runtime", [False, True])
def test_chinese_task_tool_keeps_browser_categories_without_tool_name_lists(persistent_runtime: bool) -> None:
    browser = SubAgentConfig(
        agent_card=AgentCard(name="browser_agent", description="Browser policy"),
        system_prompt="Browser task",
    )
    agent = SimpleNamespace(
        card=AgentCard(name="parent"),
        system_prompt_builder=SystemPromptBuilder(language="cn"),
        deep_config=SimpleNamespace(subagents=[browser]),
        ability_manager=SimpleNamespace(add_ability=lambda card, tool: None),
    )
    rail = SubagentRail(
        enable_subagent_runtime=persistent_runtime,
        synchronous_subagent_types={"browser_agent"},
    )
    rail.init(agent)
    card = next(tool.card for tool in rail.tools if tool.card.name == "task_tool")
    assert card.description.startswith("同步委派")
    assert "browser_agent: Browser policy" in card.description
    for capability in DEFAULT_BROWSER_CAPABILITIES:
        assert f"{capability.name}: {capability.description}" in card.description
        assert f"(Tools: {', '.join(capability.tool_names)})" not in card.description
    registered = card.description
    rail.refresh_available_agents(agent)
    assert card.description == registered
    assert card.input_params["required"] == ["subagent_type", "task_description"]
