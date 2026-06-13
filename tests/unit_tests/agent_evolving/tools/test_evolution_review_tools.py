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
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.agent_evolving.tools import (
    EvolutionReviewListSkillExperiencesTool,
    EvolutionReviewListTrajectoryStepsTool,
    EvolutionReviewReadSkillExperiencesTool,
    EvolutionReviewReadTrajectoryStepsTool,
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
        "list_trajectory_steps",
        "read_trajectory_steps",
        "submit_evolution_review",
    }
    assert {tool.card.id for tool in tools} == {
        "EvolutionReviewListSkillExperiencesTool_parent_agent_1",
        "EvolutionReviewReadSkillExperiencesTool_parent_agent_1",
        "EvolutionReviewListTrajectoryStepsTool_parent_agent_1",
        "EvolutionReviewReadTrajectoryStepsTool_parent_agent_1",
        "SubmitEvolutionReviewResultTool_parent_agent_1",
    }


def test_review_tools_declare_required_input_schemas():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    tools = [
        EvolutionReviewListSkillExperiencesTool(runtime=runtime, query_service=query_service),
        EvolutionReviewReadSkillExperiencesTool(runtime=runtime, query_service=query_service),
        EvolutionReviewListTrajectoryStepsTool(runtime=runtime),
        EvolutionReviewReadTrajectoryStepsTool(runtime=runtime),
        SubmitEvolutionReviewResultTool(runtime=runtime),
    ]

    schemas = {tool.card.name: tool.card.input_params for tool in tools}

    assert schemas["list_skill_experiences"]["required"] == ["evolution_review_ref"]
    assert schemas["read_skill_experiences"]["required"] == ["evolution_review_ref", "record_ids"]
    assert schemas["list_trajectory_steps"]["required"] == ["evolution_review_ref"]
    list_props = schemas["list_trajectory_steps"]["properties"]
    assert list_props["cursor"]["type"] == "string"
    assert list_props["limit"]["type"] == "integer"
    assert list_props["kind"]["enum"] == ["llm", "tool"]
    assert list_props["tool_name"]["type"] == "string"
    assert list_props["has_error"]["type"] == "boolean"
    assert schemas["read_trajectory_steps"]["required"] == ["evolution_review_ref", "refs"]
    assert "trajectory ref" in schemas["read_trajectory_steps"]["properties"]["refs"]["description"]
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
    assert submit_schema["properties"]["proposals"]["maxItems"] == 3
    proposal_required = submit_schema["properties"]["proposals"]["items"]["required"]
    assert proposal_required == ["proposal_id", "experience"]
    assert "proposal_id" in submit_schema["properties"]["proposals"]["items"]["properties"]
    experience_required = submit_schema["properties"]["proposals"]["items"]["properties"]["experience"]["required"]
    assert experience_required == ["summary", "content"]
    experience_properties = submit_schema["properties"]["proposals"]["items"]["properties"]["experience"]["properties"]
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

    refs_description = schemas["read_trajectory_steps"]["properties"]["refs"]["description"]
    assert refs_description == "Trajectory refs from the scoped review materials."
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
async def test_read_trajectory_steps_returns_details_and_records_trace():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        scoped_materials={
            "trajectory_steps": [
                {
                    "ref": "step-1",
                    "index": 0,
                    "kind": "tool",
                    "tool_name": "bash",
                    "summary": "tool=bash result_preview=failed parse",
                    "has_error": True,
                }
            ],
            "trajectory_step_details": {
                "step-1": {
                    "ref": "step-1",
                    "index": 0,
                    "kind": "tool",
                    "detail": {
                        "tool_name": "bash",
                        "call_args": {"cmd": "pytest"},
                        "call_result": "failed parse",
                        "tool_call_id": "call-1",
                    },
                }
            },
        },
    )
    tool = EvolutionReviewReadTrajectoryStepsTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["step-1"]},
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data["items"] == [
        {
            "ref": "step-1",
            "index": 0,
            "kind": "tool",
            "detail": {
                "tool_name": "bash",
                "call_args": {"cmd": "pytest"},
                "call_result": "failed parse",
                "tool_call_id": "call-1",
            },
        }
    ]
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == {"step-1"}


@pytest.mark.asyncio
async def test_read_trajectory_steps_rejects_unknown_detail_refs():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "tool", "summary": "tool=bash", "has_error": False}
            ],
            "trajectory_step_details": {},
        },
    )
    tool = EvolutionReviewReadTrajectoryStepsTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["step-1"]},
        conversation_id="session-1",
    )

    assert result.success is False
    assert "unknown trajectory refs" in result.error


@pytest.mark.asyncio
async def test_list_trajectory_steps_returns_paginated_index_without_recording_trace():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "llm", "summary": "llm model=m messages=1", "has_error": False},
                {
                    "ref": "step-2",
                    "index": 1,
                    "kind": "tool",
                    "tool_name": "bash",
                    "summary": "tool=bash result_preview=ok",
                    "has_error": False,
                },
                {
                    "ref": "step-3",
                    "index": 2,
                    "kind": "tool",
                    "tool_name": "python",
                    "summary": "tool=python result_preview=Error",
                    "has_error": True,
                },
            ]
        },
    )
    tool = EvolutionReviewListTrajectoryStepsTool(runtime=runtime)

    result = await tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "limit": 2},
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data == {
        "items": [
            {"ref": "step-1", "index": 0, "kind": "llm", "summary": "llm model=m messages=1", "has_error": False},
            {
                "ref": "step-2",
                "index": 1,
                "kind": "tool",
                "tool_name": "bash",
                "summary": "tool=bash result_preview=ok",
                "has_error": False,
            },
        ],
        "next_cursor": "2",
        "total": 3,
    }
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == set()


@pytest.mark.asyncio
async def test_list_trajectory_steps_filters_index_items():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "llm", "summary": "llm model=m messages=1", "has_error": False},
                {
                    "ref": "step-2",
                    "index": 1,
                    "kind": "tool",
                    "tool_name": "bash",
                    "summary": "tool=bash result_preview=ok",
                    "has_error": False,
                },
                {
                    "ref": "step-3",
                    "index": 2,
                    "kind": "tool",
                    "tool_name": "python",
                    "summary": "tool=python result_preview=Error",
                    "has_error": True,
                },
            ]
        },
    )
    tool = EvolutionReviewListTrajectoryStepsTool(runtime=runtime)

    result = await tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "kind": "tool",
            "has_error": True,
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data["items"] == [
        {
            "ref": "step-3",
            "index": 2,
            "kind": "tool",
            "tool_name": "python",
            "summary": "tool=python result_preview=Error",
            "has_error": True,
        }
    ]
    assert result.data["next_cursor"] is None
    assert result.data["total"] == 1


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
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "tool", "summary": "tool=bash result_preview=failed parse"}
            ],
            "trajectory_step_details": {
                "step-1": {
                    "ref": "step-1",
                    "index": 0,
                    "kind": "tool",
                    "detail": {"tool_name": "bash", "call_result": "failed parse"},
                }
            },
        },
    )
    read_tool = EvolutionReviewReadTrajectoryStepsTool(runtime=runtime)
    await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["step-1"]},
        conversation_id="session-1",
    )
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
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
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "tool", "summary": "tool=bash result_preview=failed parse"}
            ],
            "trajectory_step_details": {
                "step-1": {
                    "ref": "step-1",
                    "index": 0,
                    "kind": "tool",
                    "detail": {"tool_name": "bash", "call_result": "failed parse"},
                }
            },
        },
    )
    reviewer_session_id = "session-1_sub_evolution_reviewer_1234abcd"
    read_tool = EvolutionReviewReadTrajectoryStepsTool(runtime=runtime)
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    read_result = await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["step-1"]},
        conversation_id=reviewer_session_id,
    )
    submit_result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
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
        conversation_id=reviewer_session_id,
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
    assert runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1").read_trace == {"step-1"}


@pytest.mark.asyncio
async def test_submit_evolution_review_rejects_more_than_max_proposals():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        scoped_materials={
            "trajectory_steps": [
                {"ref": "step-1", "index": 0, "kind": "tool", "summary": "tool=bash result_preview=failed parse"}
            ],
            "trajectory_step_details": {
                "step-1": {
                    "ref": "step-1",
                    "index": 0,
                    "kind": "tool",
                    "detail": {"tool_name": "bash", "call_result": "failed parse"},
                }
            },
        },
    )
    read_tool = EvolutionReviewReadTrajectoryStepsTool(runtime=runtime)
    await read_tool.invoke(
        {"evolution_review_ref": launch.evolution_review_ref, "refs": ["step-1"]},
        conversation_id="session-1",
    )
    submit_tool = SubmitEvolutionReviewResultTool(runtime=runtime)

    result = await submit_tool.invoke(
        {
            "evolution_review_ref": launch.evolution_review_ref,
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": f"prop_{index}",
                    "experience": {
                        "summary": f"Use parser fields {index}",
                        "content": f"Prefer parser fields for case {index}.",
                    },
                    "evidence_refs": ["step-1"],
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
