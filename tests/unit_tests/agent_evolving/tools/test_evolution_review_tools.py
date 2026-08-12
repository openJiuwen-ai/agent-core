# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for restricted Skill evolution review tools."""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES, SIMPLIFY_ACTION_VALUES, VALID_SECTIONS
from openjiuwen.agent_evolving.prompts.tools import (
    ListSkillExperiencesMetadataProvider,
    SimplifySkillExperiencesMetadataProvider,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.agent_evolving.tools import (
    EvolutionReviewListSkillExperiencesTool,
    EvolutionReviewListTrajectorySpansTool,
    EvolutionReviewReadSkillExperiencesTool,
    EvolutionReviewReadTrajectorySpansTool,
    SubmitEvolutionReviewResultTool,
    create_evolution_review_tools,
)


class DummyStore:
    def __init__(self, records):
        self.records = records
        self.exists_skill_names = []
        self.exists_skill_kinds = []
        self.scored_skill_names = []
        self.loaded_record_requests = []
        self.loaded_full_skill_names = []

    def skill_exists(self, skill_name, *, subject_kind=None):
        self.exists_skill_names.append(skill_name)
        self.exists_skill_kinds.append(subject_kind)
        return True

    async def get_records_by_score(self, skill_name, *, min_score=None):
        self.scored_skill_names.append((skill_name, min_score))
        return list(self.records)

    async def load_records_by_ids(self, skill_name, record_ids, *, subject_kind=None):
        self.loaded_record_requests.append((skill_name, list(record_ids)))
        wanted = set(record_ids)
        return [record for record in self.records if record.id in wanted]

    async def load_full_evolution_log(self, skill_name, *, subject_kind=None):
        self.loaded_full_skill_names.append(skill_name)
        return SimpleNamespace(entries=list(self.records))


class DummyQueryService:
    def __init__(self, *, list_result=None, read_result=None):
        self.list_result = list_result or {"success": True, "operation": "list", "items": []}
        self.read_result = read_result or {"success": True, "operation": "read", "items": []}
        self.list_calls = []
        self.read_calls = []

    async def list_experiences(self, subject, **kwargs):
        self.list_calls.append((dict(subject), dict(kwargs)))
        return dict(self.list_result)

    async def read_experiences(self, subject, **kwargs):
        self.read_calls.append((dict(subject), dict(kwargs)))
        return dict(self.read_result)


def _record(record_id="ev_1", content="Prefer structured parser fields."):
    return SimpleNamespace(
        id=record_id,
        summary="Use parser fields",
        change=SimpleNamespace(
            target="body",
            section="Troubleshooting",
            content=content,
        ),
        score=0.8,
        timestamp="2026-01-01T00:00:00Z",
    )


def _span(name, span_id, *, start=1, parent=None, attributes=None, status=None):
    span = {
        "traceId": "trace-1",
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 1),
        "attributes": dict(attributes or {}),
    }
    if parent is not None:
        span["parentSpanId"] = parent
    if status is not None:
        span["status"] = status
    return span


def _trajectory(*spans):
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: "review-tool-test"}),
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "test"},
                            "spans": list(spans),
                        }
                    ],
                }
            ]
        }
    )


def _tool_trajectory(*, output="failed parse", status=None):
    return _trajectory(
        _span(
            "tool.bash",
            "tool-1",
            attributes={
                semconv.GEN_AI_TOOL_NAME: "bash",
                semconv.GEN_AI_TOOL_ID: "call-1",
                semconv.GEN_AI_TOOL_INPUT: {"cmd": "pytest"},
                semconv.GEN_AI_TOOL_OUTPUT: output,
            },
            status=status,
        )
    )


@pytest.mark.asyncio
async def test_review_tools_are_bound_to_their_runtime_and_query_service():
    runtime_a = EvolutionReviewRuntime()
    runtime_b = EvolutionReviewRuntime()
    launch_a = runtime_a.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-a",
    )
    launch_b = runtime_b.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-b"},
        session_id="session-b",
    )
    query_service_a = DummyQueryService(
        read_result={"success": True, "operation": "read", "items": [{"record_id": "ev_a"}]}
    )
    query_service_b = DummyQueryService(
        read_result={"success": True, "operation": "read", "items": [{"record_id": "ev_b"}]}
    )
    tools_a = create_evolution_review_tools(runtime=runtime_a, query_service=query_service_a)
    tools_for_a = {tool.card.name: tool for tool in tools_a}
    tools_b = create_evolution_review_tools(runtime=runtime_b, query_service=query_service_b)
    tools_for_b = {tool.card.name: tool for tool in tools_b}

    result_a = await tools_for_a["read_skill_experiences"].invoke(
        {"evolution_review_ref": launch_a.evolution_review_ref, "record_ids": ["ev_a"]},
        conversation_id="session-a",
    )
    result_b = await tools_for_b["read_skill_experiences"].invoke(
        {"evolution_review_ref": launch_b.evolution_review_ref, "record_ids": ["ev_b"]},
        conversation_id="session-b",
    )

    assert set(tools_for_a) == set(tools_for_b)
    assert tools_for_a["read_skill_experiences"] is not tools_for_b["read_skill_experiences"]
    assert result_a.success is True
    assert result_a.data["items"][0]["record_id"] == "ev_a"
    assert result_b.success is True
    assert result_b.data["items"][0]["record_id"] == "ev_b"
    assert query_service_a.read_calls[0][0] == {"kind": "skill", "name": "skill-a"}
    assert query_service_b.read_calls[0][0] == {"kind": "skill", "name": "skill-b"}


@pytest.mark.asyncio
async def test_review_tools_construct_query_service_from_store_for_compatibility():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-a",
    )
    store = DummyStore([_record("ev_a", content="from store")])
    tools = {tool.card.name: tool for tool in create_evolution_review_tools(runtime=runtime, store=store)}

    result = await tools["read_skill_experiences"].invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "record_ids": ["ev_a"]},
        conversation_id="session-a",
    )

    assert result.success is True
    assert result.data["items"][0]["record_id"] == "ev_a"
    assert store.exists_skill_names == ["skill-a"]
    assert store.loaded_record_requests == [("skill-a", ["ev_a"])]
    assert store.loaded_full_skill_names == []


def test_review_tool_agent_id_scopes_tool_ids_without_changing_names():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()

    tools = create_evolution_review_tools(runtime=runtime, query_service=query_service, agent_id="parent_agent_1")

    assert {tool.card.name for tool in tools} == {
        "list_skill_experiences",
        "read_skill_experiences",
        "list_trajectory_spans",
        "read_trajectory_spans",
        "submit_evolution_review",
    }
    assert {tool.card.id for tool in tools} == {
        "EvolutionReviewListSkillExperiencesTool_parent_agent_1",
        "EvolutionReviewReadSkillExperiencesTool_parent_agent_1",
        "EvolutionReviewListTrajectorySpansTool_parent_agent_1",
        "EvolutionReviewReadTrajectorySpansTool_parent_agent_1",
        "SubmitEvolutionReviewResultTool_parent_agent_1",
    }


def test_review_tools_declare_required_input_schemas():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    tools = [
        EvolutionReviewListSkillExperiencesTool(runtime=runtime, query_service=query_service),
        EvolutionReviewReadSkillExperiencesTool(runtime=runtime, query_service=query_service),
        EvolutionReviewListTrajectorySpansTool(runtime=runtime),
        EvolutionReviewReadTrajectorySpansTool(runtime=runtime),
        SubmitEvolutionReviewResultTool(runtime=runtime),
    ]

    schemas = {tool.card.name: tool.card.input_params for tool in tools}

    assert schemas["list_skill_experiences"]["required"] == ["evolution_review_ref"]
    assert schemas["read_skill_experiences"]["required"] == ["evolution_review_ref", "record_ids"]
    assert schemas["list_trajectory_spans"]["required"] == ["evolution_review_ref"]
    list_props = schemas["list_trajectory_spans"]["properties"]
    assert list_props["cursor"]["type"] == "string"
    assert list_props["limit"]["type"] == "integer"
    assert set(list_props["kind"]["enum"]) == {
        "llm",
        "tool",
        "team",
        "agent",
        "task",
        "message",
        "member",
        "plan",
        "event",
    }
    assert list_props["name_contains"]["type"] == "string"
    assert list_props["tool_name"]["type"] == "string"
    assert list_props["has_error"]["type"] == "boolean"
    assert schemas["read_trajectory_spans"]["required"] == ["evolution_review_ref", "refs"]
    assert schemas["read_trajectory_spans"]["properties"]["refs"]["description"]
    submit_required = schemas["submit_evolution_review"]["required"]
    assert submit_required == [
        "evolution_review_ref",
        "subject",
        "outcome",
        "evidence_refs",
        "proposals",
    ]
    submit_schema = schemas["submit_evolution_review"]
    assert "proposals" in submit_schema["properties"]
    assert "source" not in submit_schema["properties"]
    assert "record_source" not in submit_schema["properties"]
    assert submit_schema["properties"]["proposals"]["maxItems"] == 3
    proposal_properties = submit_schema["properties"]["proposals"]["items"]["properties"]
    assert "source" not in proposal_properties
    assert "record_source" not in proposal_properties
    proposal_required = submit_schema["properties"]["proposals"]["items"]["required"]
    assert proposal_required == ["proposal_id", "experience"]
    assert "proposal_id" in proposal_properties
    experience_required = proposal_properties["experience"]["required"]
    assert experience_required == ["summary", "content"]
    experience_properties = proposal_properties["experience"]["properties"]
    assert "source" not in experience_properties
    assert "record_source" not in experience_properties
    assert experience_properties["target"]["enum"] == list(EVOLUTION_TARGET_VALUES)
    assert set(experience_properties["section"]["enum"]) == set(VALID_SECTIONS)
    assert "可选值" in experience_properties["section"]["description"]
    simplify_schema = SimplifySkillExperiencesMetadataProvider().get_input_params()
    action_schema = simplify_schema["properties"]["actions"]["items"]["properties"]["action"]
    assert action_schema["enum"] == list(SIMPLIFY_ACTION_VALUES)
    list_schema = ListSkillExperiencesMetadataProvider().get_input_params()
    assert list_schema["properties"]["target"]["enum"] == list(EVOLUTION_TARGET_VALUES)


def test_review_tools_declare_english_parameter_constraints():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    tools = create_evolution_review_tools(runtime=runtime, query_service=query_service, language="en")
    schemas = {tool.card.name: tool.card.input_params for tool in tools}

    refs_description = schemas["read_trajectory_spans"]["properties"]["refs"]["description"]
    assert refs_description == "Span refs returned by list_trajectory_spans for the current review."
    experience_properties = schemas["submit_evolution_review"]["properties"]["proposals"]["items"]["properties"][
        "experience"
    ]["properties"]
    assert "Allowed values" in experience_properties["target"]["description"]
    assert "Allowed values" in experience_properties["section"]["description"]


@pytest.mark.asyncio
async def test_review_tools_declare_general_subject_schema_and_normalize_team_skill_scope():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "team-skill", "name": "team-a"},
        session_id="session-a",
    )
    store = DummyStore([_record("ev_a", content="from swarm store")])
    tools = {tool.card.name: tool for tool in create_evolution_review_tools(runtime=runtime, store=store)}

    submit_subject_schema = tools["submit_evolution_review"].card.input_params["properties"]["subject"]
    result = await tools["read_skill_experiences"].invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "record_ids": ["ev_a"]},
        conversation_id="session-a",
    )

    assert submit_subject_schema["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]
    assert result.success is True
    assert result.data["subject"] == {"kind": "swarm-skill", "name": "team-a"}
    assert store.loaded_record_requests == [("team-a", ["ev_a"])]


@pytest.mark.asyncio
async def test_read_trajectory_spans_returns_bounded_tool_detail_and_records_trace():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(status={"code": "ERROR", "message": "failed parse"}),
    )
    tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)
    span_ref = "span:trace-1:tool-1"

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": [span_ref]},
        conversation_id="session-1",
    )

    assert result.success is True
    item = result.data["items"][0]
    assert item["ref"] == span_ref
    assert item["kind"] == "tool"
    assert item["tool"] == {
        "name": "bash",
        "id": "call-1",
        "input": {"value": {"cmd": "pytest"}, "truncated": False},
        "output": {"value": "failed parse", "truncated": False},
    }
    assert item["error"]["value"]["message"] == "failed parse"
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == {span_ref}


@pytest.mark.asyncio
async def test_read_trajectory_spans_rejects_unknown_refs():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(),
    )
    tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["span:trace-1:missing"]},
        conversation_id="session-1",
    )

    assert result.success is False
    assert "unknown trajectory refs" in result.error


@pytest.mark.asyncio
async def test_list_trajectory_spans_returns_paginated_factual_index_without_recording_trace():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_trajectory(
            _span("llm.call", "llm-1", start=1, attributes={semconv.GEN_AI_REQUEST_MODEL: "m"}),
            _span("tool.bash", "tool-1", start=2, attributes={semconv.GEN_AI_TOOL_NAME: "bash"}),
            _span(
                "tool.python",
                "tool-2",
                start=3,
                attributes={semconv.GEN_AI_TOOL_NAME: "python"},
                status={"code": "ERROR"},
            ),
        ),
    )
    tool = EvolutionReviewListTrajectorySpansTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "limit": 2},
        conversation_id="session-1",
    )

    assert result.success is True
    assert [item["ref"] for item in result.data["items"]] == [
        "span:trace-1:llm-1",
        "span:trace-1:tool-1",
    ]
    assert result.data["items"][0]["model"] == "m"
    assert result.data["items"][1]["tool_name"] == "bash"
    assert "summary" not in result.data["items"][0]
    assert result.data["next_cursor"] == "2"
    assert result.data["total"] == 3
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == set()


@pytest.mark.asyncio
async def test_list_trajectory_spans_filters_kind_name_tool_and_error():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_trajectory(
            _span("llm.call", "llm-1"),
            _span("tool.bash", "tool-1", start=2, attributes={semconv.GEN_AI_TOOL_NAME: "bash"}),
            _span(
                "tool.python",
                "tool-2",
                start=3,
                attributes={semconv.GEN_AI_TOOL_NAME: "python"},
                status={"code": "ERROR"},
            ),
        ),
    )
    tool = EvolutionReviewListTrajectorySpansTool(runtime=runtime)

    result = await tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "kind": "tool",
            "name_contains": "PYTHON",
            "tool_name": "python",
            "has_error": True,
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data["items"][0]["ref"] == "span:trace-1:tool-2"
    assert result.data["items"][0]["tool_name"] == "python"
    assert result.data["items"][0]["has_error"] is True
    assert result.data["next_cursor"] is None
    assert result.data["total"] == 1


@pytest.mark.asyncio
async def test_read_trajectory_spans_projects_llm_and_context_allowlists():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "swarm-skill", "name": "team-a"},
        session_id="session-1",
        trajectory=_trajectory(
            _span(
                "llm.call",
                "llm-1",
                attributes={
                    semconv.GEN_AI_REQUEST_MODEL: "model-a",
                    f"{semconv.GEN_AI_PROMPT}.0.role": "user",
                    f"{semconv.GEN_AI_PROMPT}.0.content": [
                        {"type": "text", "text": "review this"},
                        {"type": "image_url", "image_url": "data:image/png;base64,secret"},
                    ],
                    f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
                    f"{semconv.GEN_AI_COMPLETION}.0.content": "done",
                    semconv.GEN_AI_USAGE_TOTAL_TOKENS: 42,
                    semconv.GEN_AI_REQUEST_TEMPERATURE: 0.8,
                    semconv.LANGFUSE_OBSERVATION_INPUT: "duplicate prompt",
                },
            ),
            _span(
                "agent.worker.task_iteration.1",
                "agent-1",
                start=2,
                attributes={
                    semconv.AT_AGENT_ID: "worker-a",
                    semconv.AT_AGENT_ROLE: "researcher",
                    semconv.AT_AGENT_INPUT: "x" * 1300,
                    "unrelated.business.field": "private",
                },
            ),
        ),
    )
    tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)

    result = await tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "refs": ["span:trace-1:llm-1", "span:trace-1:agent-1"],
        },
        conversation_id="session-1",
    )

    assert result.success is True
    llm_item, agent_item = result.data["items"]
    assert llm_item["llm"]["model"] == "model-a"
    prompt_value = llm_item["llm"]["input_messages"]["items"][0]["content"]["value"]
    assert prompt_value[1] == {"type": "image_url", "omitted": "image_content"}
    assert "usage" not in llm_item["llm"]
    assert "temperature" not in llm_item["llm"]
    assert "langfuse" not in str(llm_item).lower()
    assert agent_item["context"][semconv.AT_AGENT_ID] == "worker-a"
    assert agent_item["context"][semconv.AT_AGENT_ROLE] == "researcher"
    bounded_input = agent_item["context"][semconv.AT_AGENT_INPUT]
    assert bounded_input == {"value": "x" * 1200, "truncated": True, "original_chars": 1300}
    assert "unrelated.business.field" not in agent_item["context"]


@pytest.mark.asyncio
async def test_trajectory_span_tools_omit_unaddressable_spans_and_limit_reads():
    runtime = EvolutionReviewRuntime()
    trajectory = _tool_trajectory()
    payload = trajectory.to_otlp()
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"].append(
        {
            "traceId": "trace-1",
            "name": "event.unaddressable",
            "startTimeUnixNano": "3",
            "endTimeUnixNano": "4",
        }
    )
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=Trajectory.from_otlp(payload),
    )
    tools = {
        tool.card.name: tool
        for tool in create_evolution_review_tools(runtime=runtime, query_service=DummyQueryService())
    }

    listed = await tools["list_trajectory_spans"].invoke(
        {"evolution_review_ref": launch.evolution_review_ref},
        conversation_id="session-1",
    )
    too_many = await tools["read_trajectory_spans"].invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": [f"span:t:s{i}" for i in range(9)]},
        conversation_id="session-1",
    )

    assert listed.success is True
    assert [item["ref"] for item in listed.data["items"]] == ["span:trace-1:tool-1"]
    assert too_many.success is False
    assert "read at most 8 trajectory refs" in too_many.error


@pytest.mark.asyncio
async def test_list_skill_experiences_uses_scope_subject():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    query_service = DummyQueryService(
        list_result={
            "success": True,
            "operation": "list",
            "items": [
                {
                    "record_id": "ev_1",
                    "target": "body",
                    "section": "Troubleshooting",
                    "summary": "Use parser fields",
                }
            ],
        }
    )
    tool = EvolutionReviewListSkillExperiencesTool(runtime=runtime, query_service=query_service)

    result = await tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "other-skill"},
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert query_service.list_calls == [
        (
            {"kind": "skill", "name": "skill-a"},
            {
                "min_score": None,
                "limit": 50,
                "cursor": None,
                "target": None,
                "section": None,
                "query": None,
                "sort": "score_desc",
            },
        )
    ]
    assert result.data["items"] == [
        {
            "record_id": "ev_1",
            "target": "body",
            "section": "Troubleshooting",
            "summary": "Use parser fields",
        }
    ]


@pytest.mark.asyncio
async def test_read_skill_experiences_returns_details_that_can_be_cited_as_evidence():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    query_service = DummyQueryService(
        read_result={
            "success": True,
            "operation": "read",
            "items": [
                {
                    "record_id": "ev_1",
                    "target": "body",
                    "section": "Troubleshooting",
                    "summary": "Use parser fields",
                    "content": "0123",
                }
            ],
        }
    )
    tool = EvolutionReviewReadSkillExperiencesTool(runtime=runtime, query_service=query_service)

    result = await tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "record_ids": ["ev_1", "ev_missing"],
            "max_content_chars": 4,
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert query_service.read_calls == [
        (
            {"kind": "skill", "name": "skill-a"},
            {"record_ids": ["ev_1", "ev_missing"], "max_content_chars": 4},
        )
    ]
    assert result.data["items"] == [
        {
            "record_id": "ev_1",
            "target": "body",
            "section": "Troubleshooting",
            "summary": "Use parser fields",
            "content": "0123",
        }
    ]
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == {"ev_1"}
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    submit_result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["ev_1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Prefer structured parser fields",
                        "content": "Prefer structured parser fields over raw text parsing.",
                    },
                    "evidence_refs": ["ev_1"],
                }
            ],
        },
        conversation_id="session-1",
    )

    assert submit_result.success is True
    assert submit_result.data["proposal_ids"] == ["prop_1"]


@pytest.mark.asyncio
async def test_submit_evolution_review_records_runtime_completion():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(),
    )
    span_ref = "span:trace-1:tool-1"
    read_tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)
    await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": [span_ref]},
        conversation_id="session-1",
    )
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": [span_ref],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Use parser fields",
                        "content": "Prefer parser fields when extracting structured output.",
                    },
                }
            ],
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data["status"] == "review_completed"
    assert result.data["evolution_review_ref"] == launch.evolution_review_ref
    assert result.data["proposal_ids"] == ["prop_1"]
    assert result.data["review_result"]["evolution_review_ref"] == launch.evolution_review_ref
    assert result.data["review_result"]["status"] == "review_completed"
    assert result.data["review_result"]["proposals"][0]["proposal_id"] == "prop_1"
    assert result.data["review_result"]["proposals"][0]["experience"] == {
        "summary": "Use parser fields",
        "content": "Prefer parser fields when extracting structured output.",
        "target": "body",
        "section": "Troubleshooting",
        "reason": "",
    }
    assert result.data["proposal_selection_for_submission"] == {
        "evolution_review_ref": launch.evolution_review_ref,
        "subject": {"kind": "skill", "name": "skill-a"},
        "selected_proposal_ids": ["prop_1"],
    }
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.status == "review_completed"
    assert scope.proposal_ids == {"prop_1"}


@pytest.mark.asyncio
async def test_review_tools_accept_evolution_reviewer_subsession_for_parent_scope():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(),
    )
    span_ref = "span:trace-1:tool-1"
    reviewer_session_id = "session-1_sub_evolution_reviewer_1234abcd"
    reviewer_session = create_agent_session(session_id=reviewer_session_id)
    read_tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    read_result = await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": [span_ref]},
        session=reviewer_session,
    )
    submit_result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": [span_ref],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Use parser fields",
                        "content": "Prefer parser fields when extracting structured output.",
                    },
                }
            ],
        },
        session=reviewer_session,
    )
    resolved = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )

    assert read_result.success is True
    assert submit_result.success is True
    assert resolved.selected_proposal_ids == ("prop_1",)
    assert runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1").read_trace == {span_ref}


@pytest.mark.asyncio
async def test_review_tools_reject_unrelated_session():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(),
    )
    tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["span:trace-1:tool-1"]},
        session=create_agent_session(session_id="session-2"),
    )

    assert result.success is False
    assert runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1").read_trace == set()


@pytest.mark.asyncio
async def test_submit_evolution_review_rejects_more_than_max_proposals():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        trajectory=_tool_trajectory(),
    )
    span_ref = "span:trace-1:tool-1"
    read_tool = EvolutionReviewReadTrajectorySpansTool(runtime=runtime)
    await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": [span_ref]},
        conversation_id="session-1",
    )
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": [span_ref],
            "proposals": [
                {
                    "proposal_id": f"prop_{index}",
                    "experience": {
                        "summary": f"Use parser fields {index}",
                        "content": f"Prefer parser fields for case {index}.",
                    },
                    "evidence_refs": [span_ref],
                }
                for index in range(1, 5)
            ],
        },
        conversation_id="session-1",
    )

    assert result.success is False
    assert "proposals must contain at most 3 items" in result.error
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.status == "review_required"
    assert scope.proposal_ids == set()
