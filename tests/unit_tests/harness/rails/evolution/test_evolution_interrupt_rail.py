# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for active evolution mutation interrupt rail."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import ToolCallInterruptRequest
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import (
    EVOLUTION_APPROVAL_INTERRUPT_KIND,
    EVOLUTION_RESUME_USER_INPUT_KEY,
    EvolutionInterruptRail,
)
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail


class _StateSession:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, key):
        return self.state.get(key)

    def update_state(self, state):
        self.state.update(state)


def _runtime_with_completed_proposal(
    *,
    skill_name: str = "skill-a",
    session_id: str = "session-1",
    summary: str = "Use parser fields",
    content: str = "Prefer parser fields.",
):
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="agent_detected_signal",
        subject={"kind": "skill", "name": skill_name},
        session_id=session_id,
    )
    runtime.record_trajectory_read(launch.evolution_review_ref, session_id=session_id, refs=["step-1"])
    runtime.record_review_result(
        launch.evolution_review_ref,
        session_id=session_id,
        result={
            "subject": {"kind": "skill", "name": skill_name},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": summary,
                        "content": content,
                        "target": "body",
                        "section": "Troubleshooting",
                    },
                    "reason": "reviewed evidence supports this proposal",
                    "evidence_refs": ["step-1"],
                }
            ],
        },
    )
    return runtime, launch.evolution_review_ref


def _runtime_with_completed_proposals(
    *,
    skill_name: str = "skill-a",
    session_id: str = "session-1",
):
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="agent_detected_signal",
        subject={"kind": "skill", "name": skill_name},
        session_id=session_id,
    )
    runtime.record_trajectory_read(launch.evolution_review_ref, session_id=session_id, refs=["step-1"])
    runtime.record_review_result(
        launch.evolution_review_ref,
        session_id=session_id,
        result={
            "subject": {"kind": "skill", "name": skill_name},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Use parser fields",
                        "content": "Prefer parser fields.",
                        "target": "body",
                        "section": "Troubleshooting",
                    },
                    "reason": "reviewed evidence supports parser fields",
                    "evidence_refs": ["step-1"],
                },
                {
                    "proposal_id": "prop_2",
                    "experience": {
                        "summary": "Record retry context",
                        "content": "Capture retry context before retrying.",
                        "target": "body",
                        "section": "Troubleshooting",
                    },
                    "reason": "reviewed evidence supports retry context",
                    "evidence_refs": ["step-1"],
                },
            ],
        },
    )
    return runtime, launch.evolution_review_ref


def _submission_service(
    *,
    validate_simplify_actions=None,
    runtime: EvolutionReviewRuntime | None = None,
) -> SimpleNamespace:
    sub = SimpleNamespace(
        validate_experience_drafts=Mock(),
        validate_simplify_actions=(validate_simplify_actions if validate_simplify_actions is not None else AsyncMock()),
        prepare_evolve_submission=None,
        prepare_simplify_submission=(
            validate_simplify_actions if validate_simplify_actions is not None else AsyncMock()
        ),
    )
    if runtime is not None:

        def _fake_prepare(**kw):
            resolved = runtime.resolve_selected_proposals(
                kw["evolution_review_ref"],
                subject=kw["subject"],
                selected_proposal_ids=kw["selected_proposal_ids"],
                session_id=kw["session_id"],
            )
            sub.validate_experience_drafts(kw["subject"], list(resolved.experience_drafts))
            return resolved

        sub.prepare_evolve_submission = _fake_prepare
    return sub


def _make_rail(
    runtime: EvolutionReviewRuntime,
    *,
    submission: SimpleNamespace | None = None,
    auto_save: bool = False,
    language: str = "cn",
) -> EvolutionInterruptRail:
    if submission is None:
        submission = _submission_service(runtime=runtime)
    return EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=submission,
        auto_save=auto_save,
        language=language,
    )


def _evolve_ctx(
    review_ref: str | None,
    *,
    session: _StateSession | None = None,
    session_id: str = "session-1",
    selected_proposal_ids: list[str] | None = None,
    subject: dict | None = None,
) -> AgentCallbackContext:
    tool_args = {
        "subject": subject or {"kind": "skill", "name": "skill-a"},
        "selected_proposal_ids": selected_proposal_ids or ["prop_1"],
    }
    if review_ref is not None:
        tool_args["evolution_review_ref"] = review_ref
    inputs = ToolCallInputs(
        tool_name="evolve_skill_experiences",
        tool_args=tool_args,
        tool_call=ToolCall(
            id="call_001",
            type="function",
            name="evolve_skill_experiences",
            arguments="{}",
        ),
    )
    inputs.conversation_id = session_id
    return AgentCallbackContext(agent=object(), session=session or _StateSession(), inputs=inputs)


@pytest.mark.asyncio
async def test_evolve_interrupt_uses_generic_request_message():
    runtime, review_ref = _runtime_with_completed_proposal()
    submission = _submission_service(runtime=runtime)
    rail = _make_rail(runtime, submission=submission)
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    assert isinstance(exc_info.value.cause, ToolInterruptException)
    request = exc_info.value.cause.request
    assert "skill-a" in request.message
    assert "演进" in request.message
    assert "Use parser fields" in request.message
    assert "Prefer parser fields." in request.message
    assert "action" in request.payload_schema["properties"]
    assert "approved" not in request.payload_schema["properties"]
    assert "kind" not in request.payload_schema["properties"]
    assert "approval_detail" not in request.metadata
    assert request.metadata["interrupt_kind"] == EVOLUTION_APPROVAL_INTERRUPT_KIND
    assert request.metadata["evolution_approval"] is True
    assert request.metadata["resume_user_input_key"] == EVOLUTION_RESUME_USER_INPUT_KEY
    assert request.ui_options == [
        {"label": "本次允许", "value": "allow_once", "description": "允许本次技能演进变更执行"},
        {"label": "总是允许", "value": "allow_always", "description": "自动允许后续匹配的技能演进变更"},
        {"label": "拒绝", "value": "reject", "description": "跳过本次技能演进变更"},
    ]
    wrapped = ToolCallInterruptRequest.from_tool_call(request=request, tool_call=exc_info.value.cause.tool_call)
    assert "approval_detail" not in wrapped.metadata
    assert not hasattr(request, "approval_detail")
    interrupted_args = json.loads(exc_info.value.cause.tool_call.arguments)
    assert interrupted_args["selected_proposal_ids"] == ["prop_1"]
    assert "experiences" not in interrupted_args
    submission.validate_experience_drafts.assert_called_once()


@pytest.mark.asyncio
async def test_evolve_interrupt_message_lists_multiple_selected_proposals():
    runtime, review_ref = _runtime_with_completed_proposals()
    submission = _submission_service(runtime=runtime)
    rail = _make_rail(runtime, submission=submission)
    ctx = _evolve_ctx(review_ref, selected_proposal_ids=["prop_1", "prop_2"])

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    request = exc_info.value.cause.request
    assert "2 条 Skill 演进经验" in request.message
    assert "Use parser fields" in request.message
    assert "Prefer parser fields." in request.message
    assert "Record retry context" in request.message
    assert "Capture retry context before retrying." in request.message
    interrupted_args = json.loads(exc_info.value.cause.tool_call.arguments)
    assert interrupted_args["selected_proposal_ids"] == ["prop_1", "prop_2"]
    submission.validate_experience_drafts.assert_called_once()


@pytest.mark.asyncio
async def test_evolve_interrupt_accepts_swarm_skill_subject_kind():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="agent_detected_signal",
        subject={"kind": "swarm-skill", "name": "team-a"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(launch.evolution_review_ref, session_id="session-1", refs=["step-1"])
    runtime.record_review_result(
        launch.evolution_review_ref,
        session_id="session-1",
        result={
            "subject": {"kind": "swarm-skill", "name": "team-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Clarify handoff",
                        "content": "Clarify leader/member handoff before delegation.",
                    },
                    "evidence_refs": ["step-1"],
                }
            ],
        },
    )
    submission = _submission_service(runtime=runtime)
    rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=submission,
    )
    ctx = _evolve_ctx(
        launch.evolution_review_ref,
        subject={"kind": "team-skill", "name": "team-a"},
    )

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    request = exc_info.value.cause.request
    assert "team-a" in request.message
    assert "swarm-skill" in request.message
    assert "Clarify handoff" in request.message
    assert "approval_detail" not in request.metadata
    submission.validate_experience_drafts.assert_called_once_with(
        {"kind": "swarm-skill", "name": "team-a"},
        [
            {
                "summary": "Clarify handoff",
                "content": "Clarify leader/member handoff before delegation.",
                "target": "body",
                "section": "Troubleshooting",
                "reason": "",
            }
        ],
    )


@pytest.mark.asyncio
async def test_evolve_interrupt_uses_shared_runtime_service_and_global_auto_save():
    runtime, skill_ref = _runtime_with_completed_proposal(skill_name="shared-name")
    swarm_launch = runtime.create_scope(
        source="agent_detected_signal",
        subject={"kind": "swarm-skill", "name": "shared-name"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(swarm_launch.evolution_review_ref, session_id="session-1", refs=["step-1"])
    runtime.record_review_result(
        swarm_launch.evolution_review_ref,
        session_id="session-1",
        result={
            "subject": {"kind": "swarm-skill", "name": "shared-name"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {
                        "summary": "Clarify handoff",
                        "content": "Use swarm proposal.",
                    },
                    "evidence_refs": ["step-1"],
                }
            ],
        },
    )
    submission = _submission_service(runtime=runtime)
    rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=submission,
        auto_save=True,
        language="cn",
    )

    await rail.before_tool_call(
        _evolve_ctx(swarm_launch.evolution_review_ref, subject={"kind": "swarm-skill", "name": "shared-name"})
    )

    rail.auto_save = False
    with pytest.raises(AbortError):
        await rail.before_tool_call(_evolve_ctx(skill_ref, subject={"kind": "skill", "name": "shared-name"}))

    assert rail.auto_save is False
    assert submission.validate_experience_drafts.call_count == 2


@pytest.mark.asyncio
async def test_configure_rebinds_shared_dependencies_without_kind_routing():
    first_runtime, first_ref = _runtime_with_completed_proposal(skill_name="first")
    second_runtime, second_ref = _runtime_with_completed_proposal(skill_name="second")
    first_submission = _submission_service(runtime=first_runtime)
    second_submission = _submission_service(runtime=second_runtime)
    rail = EvolutionInterruptRail(
        review_runtime=first_runtime,
        submission_service=first_submission,
        auto_save=True,
        language="cn",
    )
    rail.configure(
        review_runtime=second_runtime,
        submission_service=second_submission,
        auto_save=False,
        language="cn",
    )

    with pytest.raises(AbortError):
        await rail.before_tool_call(_evolve_ctx(second_ref, subject={"kind": "skill", "name": "second"}))

    await rail.before_tool_call(_evolve_ctx(first_ref, subject={"kind": "skill", "name": "first"}))

    assert rail.auto_save is False
    first_submission.validate_experience_drafts.assert_not_called()
    second_submission.validate_experience_drafts.assert_called_once()


@pytest.mark.asyncio
async def test_evolve_interrupt_supports_english_approval_copy():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=_submission_service(runtime=runtime),
        auto_save=False,
        language="en",
    )
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    request = exc_info.value.cause.request
    assert request.message.startswith("Approve 1 skill evolution experience(s) for `skill-a` (skill)?")
    assert "Use parser fields" in request.message
    assert "Prefer parser fields." in request.message
    assert "approval_detail" not in request.metadata
    assert request.ui_options == [
        {"label": "Allow Once", "value": "allow_once", "description": "Allow this skill evolution change"},
        {
            "label": "Always Allow",
            "value": "allow_always",
            "description": "Automatically allow future matching skill evolution changes",
        },
        {"label": "Reject", "value": "reject", "description": "Skip this skill evolution change"},
    ]


@pytest.mark.asyncio
async def test_evolve_resume_allow_once_continues_with_frozen_args():
    runtime, review_ref = _runtime_with_completed_proposal(content="Frozen content.")
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    assert isinstance(ctx.inputs.tool_args, str)
    assert '"selected_proposal_ids": ["prop_1"]' in exc_info.value.cause.tool_call.arguments
    assert "Mutated content." not in exc_info.value.cause.tool_call.arguments
    assert ctx.inputs.tool_call.arguments == exc_info.value.cause.tool_call.arguments

    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "allow_once"}
    await rail.before_tool_call(ctx)

    assert "_skip_tool" not in ctx.extra


@pytest.mark.asyncio
async def test_evolve_resume_ignores_generic_resume_key():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)

    ctx.extra[RESUME_USER_INPUT_KEY] = {"action": "allow_once"}
    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    assert exc_info.value.cause.request.metadata["resume_user_input_key"] == EVOLUTION_RESUME_USER_INPUT_KEY
    assert "_skip_tool" not in ctx.extra


@pytest.mark.asyncio
async def test_evolve_resume_accepts_payload_wrapped_by_tool_call_id():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)

    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"call_001": {"action": "allow_once"}}
    await rail.before_tool_call(ctx)

    assert "_skip_tool" not in ctx.extra


@pytest.mark.asyncio
async def test_evolve_resume_allow_always_stores_auto_confirm():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    session = _StateSession()
    ctx = _evolve_ctx(review_ref, session=session)

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)

    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "allow_always"}
    await rail.before_tool_call(ctx)

    assert session.state["__interrupt_auto_confirm__"] == {"evolution:evolve_skill_experiences:skill:skill-a": True}


@pytest.mark.asyncio
async def test_permission_rail_passes_evolution_allow_always_resume_to_evolution_rail():
    runtime, review_ref = _runtime_with_completed_proposal()
    permission_rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "schema": "tiered_policy",
            "permission_mode": "normal",
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        }
    )
    evolution_rail = _make_rail(runtime)
    session = _StateSession()
    ctx = _evolve_ctx(review_ref, session=session)

    await permission_rail.before_tool_call(ctx)
    with pytest.raises(AbortError):
        await evolution_rail.before_tool_call(ctx)

    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "allow_always"}
    await permission_rail.before_tool_call(ctx)
    await evolution_rail.before_tool_call(ctx)

    assert session.state["__interrupt_auto_confirm__"] == {"evolution:evolve_skill_experiences:skill:skill-a": True}


@pytest.mark.asyncio
async def test_permission_rail_does_not_let_evolution_resume_payload_bypass_tool_ask():
    runtime, review_ref = _runtime_with_completed_proposal()
    permission_rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "schema": "tiered_policy",
            "permission_mode": "normal",
            "defaults": {"*": "allow"},
            "tools": {"evolve_skill_experiences": "ask"},
            "rules": [],
            "approval_overrides": [],
        }
    )
    ctx = _evolve_ctx(review_ref, session=_StateSession())
    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "allow_always"}

    with pytest.raises(AbortError) as exc_info:
        await permission_rail.before_tool_call(ctx)

    request = exc_info.value.cause.request
    assert "approved" in request.payload_schema["properties"]
    assert "action" not in request.payload_schema["properties"]
    assert "__interrupt_auto_confirm__" not in ctx.session.state


@pytest.mark.asyncio
async def test_evolve_resume_reject_skips_tool_with_named_tool_message():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(review_ref)

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)

    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "reject"}
    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "rejected" in ctx.inputs.tool_result.error
    assert isinstance(ctx.inputs.tool_msg, ToolMessage)
    assert ctx.inputs.tool_msg.tool_call_id == "call_001"
    assert ctx.inputs.tool_msg.name == "evolve_skill_experiences"
    assert "rejected" in ctx.inputs.tool_msg.content
    ContextUtils.validate_messages(ctx.inputs.tool_msg)


@pytest.mark.asyncio
async def test_evolve_auto_save_does_not_interrupt():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime, auto_save=True)
    ctx = _evolve_ctx(review_ref)

    await rail.before_tool_call(ctx)

    assert "_skip_tool" not in ctx.extra
    assert json.loads(ctx.inputs.tool_call.arguments)["selected_proposal_ids"] == ["prop_1"]


@pytest.mark.asyncio
async def test_evolve_auto_confirm_does_not_interrupt():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(
        review_ref,
        session=_StateSession(
            {"__interrupt_auto_confirm__": {"evolution:evolve_skill_experiences:skill:skill-a": True}}
        ),
    )

    await rail.before_tool_call(ctx)

    assert "_skip_tool" not in ctx.extra


@pytest.mark.asyncio
async def test_evolve_false_auto_confirm_still_interrupts():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(
        review_ref,
        session=_StateSession(
            {"__interrupt_auto_confirm__": {"evolution:evolve_skill_experiences:skill:skill-a": False}}
        ),
    )

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)


@pytest.mark.asyncio
async def test_evolve_requires_completed_review_ref_before_approval():
    runtime = EvolutionReviewRuntime()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(None)

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "evolution_review_ref" in ctx.inputs.tool_result.error
    assert isinstance(ctx.inputs.tool_msg, ToolMessage)
    assert ctx.inputs.tool_msg.name == "evolve_skill_experiences"


@pytest.mark.asyncio
async def test_evolve_unknown_review_ref_returns_stable_error_before_approval():
    runtime = EvolutionReviewRuntime()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx("evrr_unknown")

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert ctx.inputs.tool_result.error == "unknown or expired evolution_review_ref"
    assert ctx.inputs.tool_msg.content == "unknown or expired evolution_review_ref"


@pytest.mark.asyncio
async def test_evolve_rejects_unknown_proposal_before_approval():
    runtime, review_ref = _runtime_with_completed_proposal()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(review_ref, selected_proposal_ids=["prop_unknown"])

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "unknown proposal_id" in ctx.inputs.tool_result.error
    scope = runtime.resolve_scope(review_ref, session_id="session-1")
    assert scope.status == "review_completed"


@pytest.mark.asyncio
async def test_interrupt_without_runtime_submission_fails_fast():
    rail = EvolutionInterruptRail()
    ctx = _evolve_ctx("evrr_1")

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "EvolutionInterruptRail is not configured" in ctx.inputs.tool_result.error
    assert isinstance(ctx.inputs.tool_msg, ToolMessage)
    assert ctx.inputs.tool_msg.name == "evolve_skill_experiences"


@pytest.mark.asyncio
async def test_evolve_gate_failure_does_not_create_resume_trap():
    runtime = EvolutionReviewRuntime()
    rail = _make_rail(runtime)
    ctx = _evolve_ctx(None)

    await rail.before_tool_call(ctx)
    ctx.extra.pop("_skip_tool")
    ctx.inputs.tool_args = json.dumps(ctx.inputs.tool_args, ensure_ascii=False, sort_keys=True)
    ctx.extra[EVOLUTION_RESUME_USER_INPUT_KEY] = {"action": "allow_once"}

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "evolution_review_ref" in ctx.inputs.tool_result.error
    assert "approval snapshot" not in ctx.inputs.tool_result.error


@pytest.mark.asyncio
async def test_simplify_invalid_action_rejects_before_approval():
    runtime = EvolutionReviewRuntime()
    submission = _submission_service(
        validate_simplify_actions=AsyncMock(side_effect=ValueError("record_id is required"))
    )
    rail = _make_rail(runtime, submission=submission)
    ctx = AgentCallbackContext(
        agent=object(),
        session=_StateSession(),
        inputs=ToolCallInputs(
            tool_name="simplify_skill_experiences",
            tool_args={
                "subject": {"kind": "skill", "name": "skill-a"},
                "actions": [{"action": "REFINE", "new_content": "Updated content."}],
            },
            tool_call=ToolCall(
                id="call_invalid_simplify",
                type="function",
                name="simplify_skill_experiences",
                arguments="{}",
            ),
        ),
    )

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert ctx.inputs.tool_result.success is False
    assert "record_id is required" in ctx.inputs.tool_result.error
    submission.prepare_simplify_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_simplify_interrupt_uses_generic_request_message():
    runtime = EvolutionReviewRuntime()
    submission = _submission_service()
    rail = _make_rail(runtime, submission=submission)
    ctx = AgentCallbackContext(
        agent=object(),
        session=_StateSession(),
        inputs=ToolCallInputs(
            tool_name="simplify_skill_experiences",
            tool_args={
                "subject": {"kind": "skill", "name": "skill-a"},
                "actions": [{"action": "KEEP", "record_id": "rec-1"}],
            },
            tool_call=ToolCall(
                id="call_simplify",
                type="function",
                name="simplify_skill_experiences",
                arguments="{}",
            ),
        ),
    )

    with pytest.raises(AbortError) as exc_info:
        await rail.before_tool_call(ctx)

    request = exc_info.value.cause.request
    assert "skill-a" in request.message
    assert "精简" in request.message
    assert "KEEP" in request.message
    assert "rec-1" in request.message
    assert "approval_detail" not in request.metadata
    submission.prepare_simplify_submission.assert_awaited_once()
