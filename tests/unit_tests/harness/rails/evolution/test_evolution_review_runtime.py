# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for Skill evolution review runtime state and gate."""

from datetime import datetime, timedelta, timezone

from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime


def _proposal(proposal_id: str = "prop_1", content: str = "Prefer parser fields.") -> dict:
    return {
        "proposal_id": proposal_id,
        "experience": {
            "summary": "Use parser fields",
            "content": content,
            "target": "body",
            "section": "Troubleshooting",
        },
        "reason": "reviewed evidence supports this proposal",
        "evidence_refs": ["step-1"],
    }


def _complete_review(runtime: EvolutionReviewRuntime, *, session_id: str = "session-1"):
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id=session_id,
    )
    runtime.record_trajectory_read(launch.evolution_review_ref, session_id=session_id, refs=["step-1"])
    runtime.record_review_result(
        launch.evolution_review_ref,
        session_id=session_id,
        result={
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [_proposal("prop_1", "Prefer parser fields.")],
            "summary": "one proposal",
        },
    )
    return launch


def test_create_scope_returns_ref_and_subject():
    runtime = EvolutionReviewRuntime()

    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
        user_intent="capture parser lesson",
        scoped_materials={"trajectory_steps": [{"ref": "step-1"}]},
    )

    assert created.evolution_review_ref.startswith("evrr_")
    assert created.subject == {"kind": "skill", "name": "skill-a"}
    scope = runtime.resolve_scope(created.evolution_review_ref, session_id="session-1")
    assert scope.scope_id == created.scope_id
    assert scope.scoped_materials == {"trajectory_steps": [{"ref": "step-1"}]}


def test_swarm_runtime_normalizes_legacy_team_skill_subject():
    runtime = EvolutionReviewRuntime()

    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "team-skill", "name": "team-a"},
        session_id="session-1",
    )

    assert created.subject == {"kind": "swarm-skill", "name": "team-a"}
    scope = runtime.resolve_scope(created.evolution_review_ref, session_id="session-1")
    assert scope.subject == {"kind": "swarm-skill", "name": "team-a"}


def test_default_runtime_accepts_supported_swarm_skill_subject():
    runtime = EvolutionReviewRuntime()

    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "team-skill", "name": "team-a"},
        session_id="session-1",
    )

    assert created.subject == {"kind": "swarm-skill", "name": "team-a"}


def test_swarm_runtime_accepts_review_result_and_submission():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
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
            "proposals": [_proposal("prop_1", "Clarify leader handoff.")],
        },
    )
    resolved = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "team-skill", "name": "team-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )

    assert resolved.subject == {"kind": "swarm-skill", "name": "team-a"}
    assert resolved.experience_drafts[0]["content"] == "Clarify leader handoff."


def test_blank_session_scope_binds_to_first_concrete_session():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="",
    )

    scope = runtime.resolve_scope(created.evolution_review_ref, session_id="session-1")

    assert scope.session_id == "session-1"
    try:
        runtime.resolve_scope(created.evolution_review_ref, session_id="session-2")
    except KeyError:
        pass
    else:
        raise AssertionError("bound evolution review scope accepted another session")


def test_prune_scopes_removes_expired_scope():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    scope = runtime.resolve_scope(created.evolution_review_ref, session_id="session-1")
    scope.expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

    runtime.prune_scopes()

    assert not runtime.has_scope(created.evolution_review_ref)


def test_record_review_result_accepts_proposals():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")

    assert scope.status == "review_completed"
    assert scope.proposal_ids == {"prop_1"}
    assert scope.proposal_drafts["prop_1"]["content"] == "Prefer parser fields."
    assert scope.result["proposals"][0]["proposal_id"] == "prop_1"


def test_resolve_selected_proposals_returns_detail_and_execution_drafts():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    resolved = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )

    assert resolved.selected_proposal_ids == ("prop_1",)
    assert resolved.proposals[0].proposal_id == "prop_1"
    assert resolved.proposals[0].content == "Prefer parser fields."
    assert resolved.experience_drafts[0]["content"] == "Prefer parser fields."
    assert resolved.experience_drafts[0]["reason"] == "reviewed evidence supports this proposal"


def test_resolve_selected_proposals_rejects_uncompleted_review():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )

    try:
        runtime.resolve_selected_proposals(
            created.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "not completed" in str(exc)
    else:
        raise AssertionError("accepted uncompleted review")


def test_consume_submission_marks_scope_submitted_and_rejects_replay():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    prepared = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )

    runtime.consume_prepared_submission(prepared, session_id="session-1")

    assert prepared.selected_proposal_ids == ("prop_1",)
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.status == "submitted"
    assert scope.consumed_at is not None

    try:
        prepared2 = runtime.resolve_selected_proposals(
            launch.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1"],
            session_id="session-1",
        )
        runtime.consume_prepared_submission(prepared2, session_id="session-1")
    except ValueError as exc:
        assert "already submitted" in str(exc)
    else:
        raise AssertionError("accepted replayed proposal selection")


def test_prune_scopes_keeps_recent_submitted_scope_for_replay():
    runtime = EvolutionReviewRuntime(submitted_scope_retention_secs=60)
    launch = _complete_review(runtime)

    prepared = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )
    runtime.consume_prepared_submission(prepared, session_id="session-1")
    runtime.prune_scopes()

    assert runtime.has_scope(launch.evolution_review_ref)
    # Re-resolve proposals on an already-submitted scope should fail
    try:
        runtime.resolve_selected_proposals(
            launch.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "already submitted" in str(exc)
    else:
        raise AssertionError("accepted replayed proposal selection inside replay window")


def test_prune_scopes_removes_submitted_scope_after_retention_window():
    runtime = EvolutionReviewRuntime(submitted_scope_retention_secs=1)
    launch = _complete_review(runtime)

    prepared = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )
    runtime.consume_prepared_submission(prepared, session_id="session-1")
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    scope.consumed_at = datetime.now(tz=timezone.utc) - timedelta(seconds=2)

    runtime.prune_scopes()

    assert not runtime.has_scope(launch.evolution_review_ref)


def test_no_evolution_review_result_rejects_later_submission():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(created.evolution_review_ref, session_id="session-1", refs=["step-1"])
    runtime.record_review_result(
        created.evolution_review_ref,
        session_id="session-1",
        result={
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "no_evolution",
            "evidence_refs": ["step-1"],
            "proposals": [],
        },
    )

    scope = runtime.resolve_scope(created.evolution_review_ref, session_id="session-1")
    assert scope.status == "no_evolution"
    assert scope.proposal_ids == set()
    assert scope.result is not None
    assert scope.result["proposals"] == []
    try:
        runtime.resolve_selected_proposals(
            created.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "no evolution" in str(exc)
    else:
        raise AssertionError("accepted no-evolution review")


def test_resolve_selected_proposals_rejects_unknown_proposal_id():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    try:
        runtime.resolve_selected_proposals(
            launch.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_2"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "unknown proposal_id: prop_2" in str(exc)
    else:
        raise AssertionError("accepted unknown proposal id")


def test_resolve_selected_proposals_rejects_duplicate_proposal_ids():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    try:
        runtime.resolve_selected_proposals(
            launch.evolution_review_ref,
            subject={"kind": "skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1", "prop_1"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "duplicate proposal id" in str(exc)
    else:
        raise AssertionError("accepted duplicate selected proposal ids")


def test_record_review_result_rejects_unread_evidence_ref():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )

    try:
        runtime.record_review_result(
            created.evolution_review_ref,
            session_id="session-1",
            result={
                "subject": {"kind": "skill", "name": "skill-a"},
                "outcome": "recommend_evolve",
                "evidence_refs": ["step-1"],
                "proposals": [_proposal()],
            },
        )
    except ValueError as exc:
        assert "unread evidence refs" in str(exc)
    else:
        raise AssertionError("accepted unread evidence ref")


def test_record_review_result_rejects_proposal_without_experience_content():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(created.evolution_review_ref, session_id="session-1", refs=["step-1"])

    try:
        runtime.record_review_result(
            created.evolution_review_ref,
            session_id="session-1",
            result={
                "subject": {"kind": "skill", "name": "skill-a"},
                "outcome": "recommend_evolve",
                "evidence_refs": ["step-1"],
                "proposals": [{"proposal_id": "prop_1", "experience": {"summary": "Use parser fields"}}],
            },
        )
    except ValueError as exc:
        assert "experience content is required" in str(exc)
    else:
        raise AssertionError("accepted proposal without experience content")


def test_record_review_result_rejects_duplicate_proposal_ids():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(created.evolution_review_ref, session_id="session-1", refs=["step-1"])

    try:
        runtime.record_review_result(
            created.evolution_review_ref,
            session_id="session-1",
            result={
                "subject": {"kind": "skill", "name": "skill-a"},
                "outcome": "recommend_evolve",
                "evidence_refs": ["step-1"],
                "proposals": [
                    _proposal("prop_1", "Prefer parser fields."),
                    _proposal("prop_1", "Prefer parser fields again."),
                ],
            },
        )
    except ValueError as exc:
        assert "duplicate proposal_id" in str(exc)
    else:
        raise AssertionError("accepted duplicate proposal_id")


def test_record_review_result_rejects_cross_kind_subject_mismatch():
    runtime = EvolutionReviewRuntime()
    created = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "my-skill"},
        session_id="session-1",
    )
    runtime.record_trajectory_read(created.evolution_review_ref, session_id="session-1", refs=["step-1"])

    try:
        runtime.record_review_result(
            created.evolution_review_ref,
            session_id="session-1",
            result={
                "subject": {"kind": "swarm-skill", "name": "my-skill"},
                "outcome": "recommend_evolve",
                "evidence_refs": ["step-1"],
                "proposals": [_proposal()],
            },
        )
    except ValueError as exc:
        assert "review result subject mismatch" in str(exc)
    else:
        raise AssertionError("accepted cross-kind subject mismatch")


def test_reviewer_subsession_can_complete_parent_scope_review():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    reviewer_session_id = "session-1_sub_evolution_reviewer_1234abcd"

    runtime.record_trajectory_read(launch.evolution_review_ref, session_id=reviewer_session_id, refs=["step-1"])
    runtime.record_review_result(
        launch.evolution_review_ref,
        session_id=reviewer_session_id,
        result={
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [_proposal("prop_1", "Prefer parser fields.")],
        },
    )
    resolved = runtime.resolve_selected_proposals(
        launch.evolution_review_ref,
        subject={"kind": "skill", "name": "skill-a"},
        selected_proposal_ids=["prop_1"],
        session_id="session-1",
    )

    assert resolved.selected_proposal_ids == ("prop_1",)
    scope = runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.read_trace == {"step-1"}
    assert scope.status == "review_completed"


def test_reviewer_subsession_rejects_wrong_parent_scope():
    runtime = EvolutionReviewRuntime()
    launch = runtime.create_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )

    try:
        runtime.resolve_scope(launch.evolution_review_ref, session_id="session-2_sub_evolution_reviewer_1234abcd")
    except KeyError:
        pass
    else:
        raise AssertionError("accepted reviewer subsession from another parent session")


def test_resolve_selected_proposals_rejects_cross_kind_subject_mismatch():
    runtime = EvolutionReviewRuntime()
    launch = _complete_review(runtime)

    try:
        runtime.resolve_selected_proposals(
            launch.evolution_review_ref,
            subject={"kind": "swarm-skill", "name": "skill-a"},
            selected_proposal_ids=["prop_1"],
            session_id="session-1",
        )
    except ValueError as exc:
        assert "evolution review subject mismatch" in str(exc)
    else:
        raise AssertionError("accepted cross-kind subject mismatch in resolve_selected_proposals")
