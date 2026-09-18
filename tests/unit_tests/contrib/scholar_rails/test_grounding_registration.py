# coding: utf-8
"""Native scholar tool registration and review dispatch regressions; no network."""
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.contrib.scholar_rails import literature_grounding_rail as grounding
from openjiuwen.contrib.scholar_rails import iclr_review_rail as review


def test_grounding_builds_native_tool_cards(tmp_path):
    tools = grounding.LiteratureGroundingRail(tmp_path / "registry.json")._build_tools()
    assert len(tools) == 4
    assert all(isinstance(tool, LocalFunction) for tool in tools)
    assert all(isinstance(tool.card, ToolCard) for tool in tools)


@pytest.mark.asyncio
async def test_real_manager_registers_executes_and_unregisters(tmp_path):
    rail = grounding.LiteratureGroundingRail(tmp_path / "registry.json")
    manager = AbilityManager(owner_id="scholar-patch-" + uuid4().hex)
    agent = SimpleNamespace(ability_manager=manager)
    try:
        rail.init(agent)
        assert isinstance(manager.get("arxiv_search"), ToolCard)
        result, _ = await manager._execute_single_tool_call(
            ToolCall(id="list-keys", type="function", name="list_citable_keys", arguments="{}"), session=None,
        )
        assert json.loads(result) == {"citable_keys": []}
        rail.uninit(agent)
        assert manager.get("arxiv_search") is None
        assert manager.get("list_citable_keys") is None
    finally:
        manager.teardown_tools()


def test_uninit_preserves_another_rail_replacement(tmp_path):
    rail = grounding.LiteratureGroundingRail(tmp_path / "registry.json")
    manager = AbilityManager(owner_id="scholar-patch-" + uuid4().hex)
    agent = SimpleNamespace(ability_manager=manager)
    try:
        rail.init(agent)
        assert manager.get("arxiv_search") is rail._tools[0].card
        replacement = LocalFunction(ToolCard(name="arxiv_search", input_params={}), lambda: "replacement")
        manager.add_ability(replacement.card, replacement)
        rail.uninit(agent)
        assert manager.get("arxiv_search") is replacement.card
    finally:
        manager.teardown_tools()


def test_registration_failure_propagates(tmp_path, monkeypatch):
    rail = grounding.LiteratureGroundingRail(tmp_path / "registry.json")
    manager = AbilityManager(owner_id="scholar-patch-" + uuid4().hex)
    def reject(card, tool):
        raise RuntimeError("registration unavailable")
    monkeypatch.setattr(manager, "add_ability", reject)
    with pytest.raises(RuntimeError, match="registration unavailable"):
        rail.init(SimpleNamespace(ability_manager=manager))


@pytest.mark.asyncio
async def test_arxiv_io_runs_off_the_agent_event_loop(tmp_path, monkeypatch):
    main_thread = threading.get_ident()
    calls = []
    def offline_search(query, max_results):
        calls.append((query, max_results, threading.get_ident()))
        return []
    monkeypatch.setattr(grounding, "search_arxiv", offline_search)
    tool = grounding.LiteratureGroundingRail(tmp_path / "registry.json")._build_tools()[0]
    assert json.loads(await tool.invoke({"query": "test"})) == []
    assert calls[0][:2] == ("test", 8)
    assert calls[0][2] != main_thread


def test_review_literal_decision_braces_format():
    prompt = review._REVIEW_PROMPT.format(draft="source draft")
    assert "{accept, borderline, reject}" in prompt
    assert "source draft" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ainvoke", "invoke", "generate"])
async def test_review_supports_native_and_legacy_model_methods(tmp_path, monkeypatch, method):
    # Keep dispatch independent from the separate literal-brace regression.
    monkeypatch.setattr(review, "_REVIEW_PROMPT", "Review draft: {draft}")
    calls = []
    async def endpoint(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({
            "soundness": 7, "contribution": 7, "clarity": 7, "reproducibility": 7,
            "overall": 7, "decision": "accept", "comments": "Explicit test result",
        }))
    judge = SimpleNamespace(**{method: endpoint})
    rail = review.ICLRReviewRail(judge, report_path=tmp_path / "review.jsonl")
    score = await rail._run_review("real draft content")
    assert score.overall == 7
    assert score.decision == "accept"
    assert len(calls) == 1
    assert "real draft content" in calls[0][0]["content"]
