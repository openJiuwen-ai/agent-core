# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real composition and local DOM checks for the optional browser policy."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.subagents.browser_agent import create_browser_agent
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.guard import NODE_STATE_JS
from openjiuwen.harness.tools.browser_move.decision.policy_model import CONTEXT_KEY, BrowserPolicyModel
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_interactive_probe_js
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import setup_policy
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page
from tests.unit_tests.harness.tools.browser_move.test_create_browser_agent import (
    _capture_create_deep_agent,
    _fake_settings,
    _patch_all,
)

dom_page = shared_dom_page


def model(name):
    return Model(ModelClientConfig(client_provider="OpenAI", api_key="synthetic-not-used",
                                   api_base="https://example.invalid/v1"), ModelRequestConfig(model=name))


@pytest.mark.parametrize("mode", ["llm", "shadow", "hybrid"])
def test_factory_wires_one_policy_to_model_context_runtime_and_cleanup(mode):
    calls, fake = _capture_create_deep_agent()
    settings = replace(_fake_settings(), decision=BrowserDecisionConfig(mode=mode))
    ctx, runtime_cls, _, _ = _patch_all(fake)
    with ctx:
        agent = create_browser_agent(model("original"), settings=settings)
    chosen = calls[0]["model"]
    assert isinstance(chosen, BrowserPolicyModel)
    assert chosen.decision_config.mode == mode
    assert calls[0]["parallel_tool_calls"] is False
    assert runtime_cls.return_value.decision_policy is chosen
    assert agent.register_task_resource_cleanup.call_count == 2
    configs = [config for rail in calls[0]["rails"] for _, config in getattr(rail, "_user_processors", [])]
    assert any(getattr(config, "decision_policy", None) is chosen for config in configs)


def test_hot_reload_keeps_policy_and_replaces_only_its_llm_delegate():
    original, replacement = model("original"), model("replacement")
    policy = BrowserPolicyModel(original, BrowserDecisionConfig(mode="hybrid"), SimpleNamespace())
    agent = DeepAgent(AgentCard(name="policy-reload"))
    agent.configure(DeepAgentConfig(model=policy, enable_task_loop=False))
    agent.react_agent.set_llm(policy)
    config = replace(agent.deep_config, model=replacement)
    agent.configure(config)
    assert agent.react_agent._llm is policy
    assert policy.fallback is replacement
    assert policy.model_config.model_name == "replacement"
    assert agent.react_agent.config.model_name == "replacement"


@pytest.mark.asyncio
@pytest.mark.parametrize("publication_fails", [False, True, "eligibility"])
async def test_context_engine_preserves_ephemeral_policy_token_and_fails_open(publication_fails):
    policy, llm, client, runtime, old_context, captured = setup_policy()
    provider = AsyncMock()
    provider.capture_browser_state.return_value = captured
    if publication_fails == "eligibility":
        policy.should_observe = lambda _: (_ for _ in ()).throw(ValueError("bad optional state"))
        provider.capture_browser_state.return_value = {**captured, "decision_observation": {}}
    elif publication_fails:
        policy.publish_context = AsyncMock(side_effect=ValueError("bad optional observation"))
    session = Session(session_id="context-policy")
    session.update_state({"__browser_phase_budget_state__": old_context.get_session_ref().get_state(None)})
    engine = ContextEngine()
    context = await engine.create_context(session=session, processors=[(
        "BrowserStateContextProcessor", BrowserStateContextProcessorConfig(provider=provider, decision_policy=policy),
    )])
    await context.add_messages(UserMessage(content="click Sales"))
    window = await context.get_context_window()
    state = next(m for m in window.context_messages if m.name == "current_browser_state")
    assert (CONTEXT_KEY in state.metadata) is (publication_fails is not True)
    result = await policy.invoke(window.context_messages, tools=[{"name": "browser_batch_interact"}])
    assert bool(result.tool_calls) is (publication_fails is False)
    assert not any(CONTEXT_KEY in m.metadata for m in context.get_messages())


def probe(page):
    return page.evaluate("async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})",
                         build_interactive_probe_js(max_items=30, decision_mode=True))


def test_real_dom_exposes_labels_values_options_and_compiles_native_actions(dom_page):
    dom_page.set_content('''<form><label for="q">搜索词</label><input id="q" type="search">
        <label><input id="yes" type="checkbox">包早餐</label>
        <label for="city">城市</label><select id="city"><option value="bj">北京</option>
        <option value="sg">新加坡</option></select><button type="button">查询</button>
        <label>密码<input type="password" value="synthetic-secret"></label></form>''')
    result = probe(dom_page)
    page = BrowserPageState()
    page.register_interactives(result)
    controls = page.export_decision_targets()
    assert any(c.get("name") == "搜索词" for c in controls)
    assert any(c.get("name") == "包早餐" for c in controls)
    menu = build_menu(controls, '搜索“键盘”，城市选新加坡，勾选包早餐', limit=30)
    operations = {step["op"] for step in menu.steps.values()}
    assert operations == {"fill", "click", "select_option", "set_checked"}
    assert any(step.get("value") == "sg" for step in menu.steps.values())
    assert "synthetic-secret" not in json.dumps(menu.criteria)


@pytest.mark.parametrize("change", ["node", "label", "field", "option", "navigation"])
def test_real_dom_guard_detects_changes_between_choice_and_execution(dom_page, change):
    dom_page.set_content('''<form><input id="q" aria-label="查询词" value="old">
      <button id="go" type="button">查询</button><select id="city" aria-label="城市">
      <option value="one">One</option><option value="two">Two</option></select></form>''')
    result = probe(dom_page)
    state = BrowserPageState()
    state.register_interactives(result)
    name = "城市" if change == "option" else "查询"
    target = next(t for t in state.export_decision_targets() if t.get("name") == name or t.get("text") == name)
    selector = "#city" if change == "option" else "#go"
    expected = target["decision_state"]["node_guard"]
    assert dom_page.locator(selector).evaluate(NODE_STATE_JS) == expected
    if change == "node":
        dom_page.locator(selector).evaluate("el => el.replaceWith(el.cloneNode(true))")
    elif change == "label":
        dom_page.locator(selector).evaluate("el => el.textContent = '删除'")
    elif change == "field":
        dom_page.locator("#q").fill("new")
    elif change == "option":
        dom_page.locator(selector).evaluate("el => el.options[1].value = 'different'")
    else:
        dom_page.goto("about:blank?new-document")
        dom_page.set_content('<button id="go">查询</button>')
    assert dom_page.locator(selector).evaluate(NODE_STATE_JS) != expected
