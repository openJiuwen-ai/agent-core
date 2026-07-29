# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tiny agent tests (no real LLM).

A fake harness replaces ``NativeHarness`` in the tiny_agent module so the
run/chat/preset/factory logic is exercised deterministically: the fake simulates
the model calling ``structured_output`` (schema path) and settling a chat round
back to IDLE. Team-scoped wiring (get-or-create + dispose) is covered against a
configured leader ``TeamAgent`` with the model resolver stubbed.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from openjiuwen.agent_teams import tiny_agent as tiny_mod
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec, TinyAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tiny_agent import (
    TinyAgent,
    create_summary_agent,
    create_tiny_agent,
    create_title_agent,
    generate_summary,
    generate_title,
)
from openjiuwen.agent_teams.tools.structured_output_tool import StructuredOutputTool
from openjiuwen.core.foundation.llm import ModelClientConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

_DICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _model_resolver(model_name: str) -> TeamModelConfig:
    """A resolver that always yields a mock TeamModelConfig (no real endpoint)."""
    return TeamModelConfig(
        model_client_config=ModelClientConfig(
            model=model_name,
            api_key="mock-key",
            api_base="http://mock",
            client_provider="openai",
        )
    )


def _none_resolver(model_name: str) -> None:
    """A resolver that never resolves (simulates a missing model pool entry)."""
    return None


class _FakeAbilityManager:
    """Records tool add/remove for the chat schema-turn path."""

    def __init__(self) -> None:
        self.added: dict[str, Any] = {}

    def add_ability(self, card: Any, resource: Any) -> None:
        self.added[card.name] = resource

    def remove_ability(self, name: str) -> None:
        self.added.pop(name, None)


class _FakeHarness:
    """Stand-in for NativeHarness; simulates run_once / chat round + capture.

    Class attributes configure one test's behavior:
    - ``captured_payload``: what the "model" submits to structured_output.
    - ``output_text``: the free-text round output.
    - ``submit``: whether the model calls structured_output at all.
    """

    instances: list["_FakeHarness"] = []
    captured_payload: dict[str, Any] | None = None
    output_text: str = "fake-output"
    submit: bool = True

    def __init__(self, spec: Any, build_context: Any = None, extra_rails: Any = None) -> None:
        self.spec = spec
        self.rails: list[Any] = []
        self.sent: list[str] = []
        self.started = False
        self.disposed = False
        self.ability_manager = _FakeAbilityManager()
        self._on_state = None
        self._on_round = None
        _FakeHarness.instances.append(self)

    def add_rail(self, rail: Any) -> None:
        self.rails.append(rail)

    def _capture_tool(self) -> StructuredOutputTool | None:
        for tool in self.spec.tools or []:
            if isinstance(tool, StructuredOutputTool):
                return tool
        return self.ability_manager.added.get("structured_output")

    async def _maybe_submit(self) -> None:
        tool = self._capture_tool()
        if tool is not None and _FakeHarness.submit:
            await tool.invoke(_FakeHarness.captured_payload or {})

    async def run_once(self, content: str, *, session: Any = None) -> dict[str, Any]:
        self.sent.append(content)
        await self._maybe_submit()
        return {"output": _FakeHarness.output_text}

    async def dispose(self) -> None:
        self.disposed = True

    async def start(self) -> None:
        self.started = True

    async def subscribe(self, *, on_state: Any = None, on_round: Any = None) -> None:
        self._on_state = on_state
        self._on_round = on_round

    async def send(self, content: str, *, immediate: bool = False) -> str:
        self.sent.append(content)
        await self._maybe_submit()
        if self._on_round is not None:
            await self._on_round(kind="finished", result={"output": _FakeHarness.output_text})
        if self._on_state is not None:
            await self._on_state(new=HarnessState.IDLE)
        return "ok"


@pytest.fixture
def fake_harness(monkeypatch: pytest.MonkeyPatch):
    """Patch tiny_agent.NativeHarness with the fake and reset its config."""
    _FakeHarness.instances = []
    _FakeHarness.captured_payload = {"answer": "42"}
    _FakeHarness.output_text = "fake-output"
    _FakeHarness.submit = True
    monkeypatch.setattr(tiny_mod, "NativeHarness", _FakeHarness)
    return _FakeHarness


# ---------------------------------------------------------------------------
# run() — single-shot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_returns_plain_text_without_schema(fake_harness) -> None:
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    out = await agent.run("hello")
    assert out == "fake-output"
    assert fake_harness.instances[-1].disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_returns_structured_dict_with_dict_schema(fake_harness) -> None:
    fake_harness.captured_payload = {"answer": "42"}
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    out = await agent.run("question", schema=_DICT_SCHEMA)
    assert out == {"answer": "42"}
    assert fake_harness.instances[-1].disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_coerces_pydantic_schema(fake_harness) -> None:
    class Answer(BaseModel):
        answer: str

    fake_harness.captured_payload = {"answer": "42"}
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    out = await agent.run("question", schema=Answer)
    assert isinstance(out, Answer)
    assert out.answer == "42"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_run_raises_when_structured_output_not_submitted(fake_harness) -> None:
    fake_harness.submit = False
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    with pytest.raises(Exception):
        await agent.run("question", schema=_DICT_SCHEMA)
    assert fake_harness.instances[-1].disposed is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_run_uses_default_schema(fake_harness) -> None:
    fake_harness.captured_payload = {"answer": "default"}
    agent = create_tiny_agent(
        system_prompt="p", model_name="m", model_resolver=_model_resolver, default_schema=_DICT_SCHEMA
    )
    out = await agent.run("question")
    assert out == {"answer": "default"}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_run_uses_unique_card_id_per_call(fake_harness) -> None:
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    await agent.run("one")
    await agent.run("two")
    ids = [h.spec.card.id for h in fake_harness.instances]
    assert len(ids) == 2 and ids[0] != ids[1]


@pytest.mark.level0
def test_create_tiny_agent_raises_when_model_unresolved() -> None:
    with pytest.raises(Exception):
        create_tiny_agent(system_prompt="p", model_name="missing", model_resolver=_none_resolver)


# ---------------------------------------------------------------------------
# chat() — multi-turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_chat_multi_turn_reuses_harness(fake_harness) -> None:
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    r1 = await agent.chat("turn one")
    r2 = await agent.chat("turn two")
    assert r1 == "fake-output" and r2 == "fake-output"
    # One persistent harness, started once, both turns sent to it.
    assert len(fake_harness.instances) == 1
    harness = fake_harness.instances[0]
    assert harness.started is True
    assert harness.sent == ["turn one", "turn two"]
    await agent.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_chat_with_schema_captures_and_detaches_tool(fake_harness) -> None:
    fake_harness.captured_payload = {"answer": "chat"}
    agent = create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver)
    out = await agent.chat("question", schema=_DICT_SCHEMA)
    assert out == {"answer": "chat"}
    # Tool was detached at turn end.
    assert "structured_output" not in fake_harness.instances[0].ability_manager.added
    await agent.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_aclose_disposes_chat_harness(fake_harness) -> None:
    async with create_tiny_agent(system_prompt="p", model_name="m", model_resolver=_model_resolver) as agent:
        await agent.chat("hi")
    assert fake_harness.instances[0].disposed is True


# ---------------------------------------------------------------------------
# presets + one-call helpers
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_presets_carry_prompt_and_schema() -> None:
    title = create_title_agent(model_name="m", model_resolver=_model_resolver)
    summary = create_summary_agent(model_name="m", model_resolver=_model_resolver)
    assert title._default_schema == tiny_mod._TITLE_SCHEMA
    assert summary._default_schema == tiny_mod._SUMMARY_SCHEMA
    assert title._spec.system_prompt == tiny_mod._TITLE_PROMPT["cn"]
    assert summary._spec.system_prompt == tiny_mod._SUMMARY_PROMPT["cn"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_generate_title_returns_string(fake_harness) -> None:
    fake_harness.captured_payload = {"title": "My Title"}
    out = await generate_title("some content", model_name="m", model_resolver=_model_resolver)
    assert out == "My Title"
    assert fake_harness.instances[-1].disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_generate_summary_returns_string(fake_harness) -> None:
    fake_harness.captured_payload = {"summary": "A summary."}
    out = await generate_summary("some content", model_name="m", model_resolver=_model_resolver)
    assert out == "A summary."


# ---------------------------------------------------------------------------
# team-scoped: get_tiny_agent + dispose
# ---------------------------------------------------------------------------


def _make_leader(tiny_agents: dict[str, TinyAgentSpec]) -> TeamAgent:
    """Configure a minimal leader TeamAgent carrying declared tiny agents."""
    team_spec = TeamSpec(team_name="t", display_name="t", leader_member_name="leader")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="t", tiny_agents=tiny_agents)
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", desc="lead", team_spec=team_spec)
    agent = TeamAgent(AgentCard(id="leader", name="leader", description="t")).configure(spec, ctx)
    # Stub the resolver so a model_name resolves without a configured pool.
    agent.infra.tiny_agent_model_resolver = _model_resolver
    return agent


@pytest.mark.level0
def test_get_tiny_agent_builds_caches_and_reuses() -> None:
    agent = _make_leader({"summ": TinyAgentSpec(system_prompt="s", model_name="m")})
    ta = agent.get_tiny_agent("summ")
    assert ta is not None
    assert agent.get_tiny_agent("summ") is ta  # cached, same instance
    assert agent.infra.tiny_agents == {"summ": ta}


@pytest.mark.level0
def test_get_tiny_agent_returns_none_when_undeclared() -> None:
    agent = _make_leader({"summ": TinyAgentSpec(system_prompt="s", model_name="m")})
    assert agent.get_tiny_agent("nope") is None


@pytest.mark.level1
def test_get_tiny_agent_supports_multiple_independent_agents() -> None:
    agent = _make_leader(
        {
            "summ": TinyAgentSpec(system_prompt="summarize", model_name="m"),
            "title": TinyAgentSpec(system_prompt="title", model_name="m"),
        }
    )
    summ = agent.get_tiny_agent("summ")
    title = agent.get_tiny_agent("title")
    assert summ is not None and title is not None and summ is not title
    assert summ._spec.system_prompt == "summarize"
    assert title._spec.system_prompt == "title"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_dispose_tiny_agents_clears_cache() -> None:
    agent = _make_leader({"summ": TinyAgentSpec(system_prompt="s", model_name="m")})
    agent.get_tiny_agent("summ")
    assert agent.infra.tiny_agents
    await agent._dispose_tiny_agents()
    assert agent.infra.tiny_agents == {}
