# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PCS search registration and graph/file-tool contract tests on temporary data."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
from openjiuwen.harness.personal_context import PersonalContext, context_graph
from openjiuwen.harness.prompts import PromptAttachmentManager
from openjiuwen.harness.rails import personal_context as rail_module
from openjiuwen.harness.rails.personal_context import PersonalContextRail
from openjiuwen.harness.tools.filesystem import ReadFileTool


def _switch(home: Path, enabled: bool) -> None:
    (home / "personal_context.yaml").write_text(
        f"agent_use_enabled: {str(enabled).lower()}\ncollection_enabled: false\n", encoding="utf-8"
    )


@pytest.fixture
def search_agent(tmp_path: Path):
    _switch(tmp_path, True)
    agent = SimpleNamespace(
        ability_manager=AbilityManager(owner_id=f"pcs-search-test-{uuid4().hex}"),
        prompt_attachment_manager=PromptAttachmentManager(),
    )
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)
    try:
        yield agent, rail
    finally:
        rail.uninit(agent)
        for card in list(agent.ability_manager.list()):
            agent.ability_manager.remove_ability(card.name)


def _registered_tool(agent):
    card = agent.ability_manager.get("pcs_search")
    assert card is not None
    tool = Runner.resource_mgr.get_tool(card.id)
    assert isinstance(tool, LocalFunction)
    return tool


def _write_pages(home: Path, count: int = 1) -> Path:
    root = home / "workspace" / "context"
    topic = root / "技术"
    topic.mkdir(parents=True)
    (root / "description.md").write_text("# 知识空间\n", encoding="utf-8")
    (topic / "description.md").write_text("# 容量研究\nnavigation_only_123\n", encoding="utf-8")
    for index in range(count):
        (topic / f"资料{index}.md").write_text(
            f"# 容量研究 {index}\n\n" + "容量研究需要准确记录来源。" * 40, encoding="utf-8"
        )
    return root


@pytest.mark.asyncio
async def test_search_matches_core_graph_results_and_returns_readable_paths(search_agent, tmp_path: Path):
    agent, _rail = search_agent
    root = _write_pages(tmp_path, 12)
    core = PersonalContext(home=tmp_path)
    try:
        expected = await core.search_graph("容量研究")
    finally:
        core.shutdown()
    tool = _registered_tool(agent)
    actual = await tool.invoke({"query": "  容量研究  "})

    assert actual["results"] == expected["results"]
    assert actual["context_root"] == str(root)
    assert len(actual["results"]) == 10
    # The graph keeps a 240-character body and may add ellipses at both ends.
    assert all(len(item["snippet"]) <= 242 for item in actual["results"])
    assert "context_root" in tool.render_for_llm(actual)
    assert "容量研究" in tool.render_for_llm(actual)
    navigation = await tool.invoke({"query": "navigation_only_123"})
    assert navigation["results"][0]["path"] == "技术/description.md"

    operation = SysOperation(
        SysOperationCard(
            id=f"pcs-read-test-{uuid4().hex}",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(),
        )
    )
    reader = ReadFileTool(operation, language="en", enable_image_multimodal=False)
    hit = next(item for item in actual["results"] if not item["path"].endswith("description.md"))
    read = await reader.invoke({"file_path": str(root / hit["path"])})
    assert read.success
    assert "容量研究需要准确记录来源" in read.data["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_pages", [False, True])
async def test_empty_library_and_no_matches_are_successful_empty_results(search_agent, tmp_path, with_pages):
    agent, _rail = search_agent
    if with_pages:
        _write_pages(tmp_path)
    result = await _registered_tool(agent).invoke({"query": "unmatched_keyword_987"})
    assert result["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", " \n\t", None, 42])
async def test_invalid_keywords_do_not_execute_search(search_agent, monkeypatch, query):
    _agent, rail = search_agent
    monkeypatch.setattr(context_graph, "search_context_graph", lambda *_args: pytest.fail("must not search"))
    with pytest.raises(BaseError, match="non-empty string"):
        await rail._search(query, tool_card=rail._search_tool.card)


@pytest.mark.asyncio
async def test_registered_schema_has_only_query_and_rejects_missing_input(search_agent):
    agent, _rail = search_agent
    tool = _registered_tool(agent)
    assert tool.card.input_params["properties"].keys() == {"query"}
    assert tool.card.idempotent is True
    assert tool.card.exposure.value == "direct"
    with pytest.raises(BaseError):
        await tool.invoke({})


@pytest.mark.asyncio
async def test_graph_errors_are_not_reported_as_empty_results(search_agent, tmp_path):
    agent, _rail = search_agent
    root = _write_pages(tmp_path)
    (root / "技术" / "资料0.md").write_bytes(b"invalid utf8\xff")
    with pytest.raises(BaseError, match="UTF-8"):
        await _registered_tool(agent).invoke({"query": "容量"})


@pytest.mark.asyncio
async def test_search_cancellation_propagates(search_agent, monkeypatch):
    agent, _rail = search_agent

    def cancelled_search(_home, _query):
        raise asyncio.CancelledError

    monkeypatch.setattr(context_graph, "search_context_graph", cancelled_search)
    with pytest.raises(asyncio.CancelledError):
        await _registered_tool(agent).invoke({"query": "容量"})


@pytest.mark.asyncio
async def test_detach_during_final_switch_read_cannot_return_results(search_agent, monkeypatch):
    agent, rail = search_agent
    tool = _registered_tool(agent)
    loop = asyncio.get_running_loop()
    checks = 0

    def enabled(_path):
        nonlocal checks
        checks += 1
        if checks == 2:
            loop.call_soon_threadsafe(rail.uninit, agent)
        return True

    monkeypatch.setattr(rail_module, "_agent_use_enabled", enabled)
    monkeypatch.setattr(context_graph, "search_context_graph", lambda *_args: {"results": []})
    with pytest.raises(BaseError, match="detached"):
        await tool.invoke({"query": "容量"})


@pytest.mark.asyncio
async def test_repeated_init_and_uninit_preserve_tool_ownership(search_agent):
    agent, rail = search_agent
    tool = _registered_tool(agent)
    revision = agent.ability_manager.registry_revision
    rail.init(agent)
    assert _registered_tool(agent) is tool
    assert agent.ability_manager.registry_revision == revision
    rail.uninit(agent)
    rail.uninit(agent)
    assert agent.ability_manager.get("pcs_search") is None
    assert Runner.resource_mgr.get_tool(tool.card.id) is None
    with pytest.raises(BaseError, match="detached"):
        await tool.invoke({"query": "容量"})


def test_another_owners_same_named_tool_is_never_replaced_or_removed(search_agent, tmp_path):
    agent, rail = search_agent
    rail.uninit(agent)
    card = ToolCard(id="other", name="pcs_search")
    foreign = LocalFunction(card, lambda: "other")
    agent.ability_manager.add_ability(card, foreign)
    rail.init(agent)
    rail.uninit(agent)
    assert agent.ability_manager.get("pcs_search") is card
    assert Runner.resource_mgr.get_tool(card.id) is foreign

    agent.ability_manager.remove_ability("pcs_search")
    rail.init(agent)
    agent.ability_manager.add_ability(card, foreign)
    _switch(tmp_path, False)
    rail.uninit(agent)
    assert agent.ability_manager.get("pcs_search") is card


@pytest.mark.asyncio
async def test_switch_changes_registration_and_current_tool_list_without_touching_messages(search_agent, tmp_path):
    agent, rail = search_agent
    _write_pages(tmp_path)
    tool = _registered_tool(agent)
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="pcs-call",
                    type="function",
                    name="pcs_search",
                    arguments='{"query":"容量"}',
                )
            ],
        ),
        ToolMessage(content="result", tool_call_id="pcs-call"),
    ]
    unrelated = ToolCard(id="read", name="read_file").tool_info()
    inputs = ModelCallInputs(messages=messages, tools=[tool.card.tool_info(), unrelated])
    ctx = AgentCallbackContext(agent=agent, inputs=inputs, session=SimpleNamespace(session_id="pcs-test"))
    _switch(tmp_path, False)
    await rail.before_model_call(ctx)
    assert agent.ability_manager.get("pcs_search") is None
    assert ctx.inputs.tools == [unrelated]
    assert ctx.inputs.messages == messages
    with pytest.raises(BaseError, match="disabled"):
        await tool.invoke({"query": "容量"})

    _switch(tmp_path, True)
    await rail.before_invoke(ctx)
    assert _registered_tool(agent) is not tool
    assert "pcs_search" in [info.name for info in await agent.ability_manager.list_tool_info()]
    with pytest.raises(BaseError, match="detached"):
        await tool.invoke({"query": "容量"})


@pytest.mark.parametrize("config", [None, "agent_use_enabled: false", "agent_use_enabled: 'true'", "["])
def test_disabled_missing_or_invalid_configuration_does_not_register(search_agent, tmp_path, config):
    agent, rail = search_agent
    rail.uninit(agent)
    path = tmp_path / "personal_context.yaml"
    if config is None:
        path.unlink()
    else:
        path.write_text(config, encoding="utf-8")
    rail.init(agent)
    assert agent.ability_manager.get("pcs_search") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("detach", [False, True])
async def test_inflight_search_cannot_return_after_disable_or_detach(search_agent, tmp_path, monkeypatch, detach):
    agent, rail = search_agent
    tool = _registered_tool(agent)
    main_thread = threading.get_ident()

    def search(_home, _query):
        assert threading.get_ident() != main_thread
        if not detach:
            _switch(tmp_path, False)
        return {"results": [{"title": "must not be delivered"}]}

    monkeypatch.setattr(context_graph, "search_context_graph", search)
    if detach:
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def blocked_search(_home, _query):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return {"results": [{"title": "must not be delivered"}]}

        monkeypatch.setattr(context_graph, "search_context_graph", blocked_search)
        pending = asyncio.create_task(tool.invoke({"query": "容量"}))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            rail.uninit(agent)
        finally:
            release.set()
        with pytest.raises(BaseError, match="detached"):
            await pending
    else:
        with pytest.raises(BaseError, match="disabled"):
            await tool.invoke({"query": "容量"})
