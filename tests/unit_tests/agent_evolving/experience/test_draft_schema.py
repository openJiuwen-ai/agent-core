# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.experience.draft_schema import (
    normalize_evolution_subject_kind,
    normalize_evolve_draft,
    normalize_simplify_draft,
    normalize_subject,
    validate_simplify_record_refs,
)
from openjiuwen.core.common.exception.errors import ValidationError


def _normalize_experiences(subject, drafts):
    return normalize_evolve_draft(subject, drafts).experiences


def test_normalize_subject_accepts_regular_skill():
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    assert subject.kind == "skill"
    assert subject.name == "browser"
    assert subject.to_payload() == {"kind": "skill", "name": "browser"}


def test_normalize_subject_accepts_regular_skill_only_in_regular_policy():
    subject = normalize_subject({"kind": "skill", "name": "browser"}, allowed_kinds={"skill"})

    assert subject.kind == "skill"
    assert subject.name == "browser"
    assert subject.to_payload() == {"kind": "skill", "name": "browser"}


def test_normalize_subject_accepts_team_skill_for_read_simplify_migration():
    subject = normalize_subject({"kind": "team-skill", "name": "research-team"})

    assert subject.kind == "swarm-skill"
    assert subject.name == "research-team"
    assert subject.to_payload() == {"kind": "swarm-skill", "name": "research-team"}


def test_normalize_subject_accepts_swarm_skill():
    subject = normalize_subject({"kind": "swarm-skill", "name": "research-team"})

    assert subject.kind == "swarm-skill"
    assert subject.name == "research-team"
    assert subject.to_payload() == {"kind": "swarm-skill", "name": "research-team"}


def test_team_skill_kind_normalizes_to_swarm_skill_alias():
    assert normalize_evolution_subject_kind("team-skill") == "swarm-skill"
    assert normalize_evolution_subject_kind("skill") == "skill"


def test_normalize_subject_rejects_team_skill_for_regular_skill_policy():
    with pytest.raises(ValidationError, match="subject.kind must be one of: skill"):
        normalize_subject({"kind": "team-skill", "name": "research-team"}, allowed_kinds={"skill"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "subject.kind is required"),
        ({"kind": "team_skill", "name": "research"}, "subject.kind must be one of: skill, swarm-skill, team-skill"),
        ({"kind": "skill", "name": ""}, "subject.name is required"),
        ("browser", "subject must be an object"),
    ],
)
def test_normalize_subject_rejects_invalid_subjects(raw, message):
    with pytest.raises(ValidationError, match=message):
        normalize_subject(raw)


def test_normalize_experience_draft_defaults_body_troubleshooting():
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    drafts = _normalize_experiences(
        subject,
        [
            {
                "summary": "Use structured parser output before slicing text.",
                "content": "# Prefer structured parser output\n\nUse fields before slicing.",
            }
        ],
    )

    assert drafts[0]["target"] == "body"
    assert drafts[0]["section"] == "Troubleshooting"
    assert drafts[0]["summary"] == "Use structured parser output before slicing text."


def test_normalize_experience_draft_defaults_description_instructions():
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    drafts = _normalize_experiences(
        subject,
        [
            {
                "target": "description",
                "summary": "Use browser tools when visual verification is needed.",
                "content": "Use browser tools for visual checks.",
            }
        ],
    )

    assert drafts[0]["target"] == "description"
    assert drafts[0]["section"] == "Instructions"


def test_normalize_experience_draft_normalizes_script_section_and_summary():
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    drafts = _normalize_experiences(
        subject,
        [
            {
                "target": "script",
                "section": "Anything",
                "summary": "Extract browser console errors for debugging.",
                "script_purpose": "Extract browser console errors.",
                "content": "console.log('errors')",
            }
        ],
    )

    assert drafts[0]["section"] == "Scripts"
    assert drafts[0]["summary"] == "Extract browser console errors for debugging."


@pytest.mark.parametrize(
    "script_filename",
    [
        "../SKILL.md",
        "/tmp/payload.py",
        "nested/script.py",
        r"nested\script.py",
    ],
)
def test_normalize_experience_draft_rejects_unsafe_script_filenames(script_filename):
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    with pytest.raises(ValidationError, match="script_filename must be a file name"):
        _normalize_experiences(
            subject,
            [
                {
                    "target": "script",
                    "summary": "Extract browser console errors for debugging.",
                    "script_filename": script_filename,
                    "content": "console.log('errors')",
                }
            ],
        )


@pytest.mark.parametrize(
    ("draft", "message"),
    [
        ({"target": "unknown", "content": "x"}, "invalid target: unknown"),
        ({"summary": "Invalid section", "section": "Unknown", "content": "x"}, "invalid section: Unknown"),
        ({"content": ""}, "experience content is required"),
        ({"content": "Use structured fields."}, "experience summary is required"),
    ],
)
def test_normalize_experience_draft_rejects_invalid_values(draft, message):
    subject = normalize_subject({"kind": "skill", "name": "browser"})

    with pytest.raises(ValidationError, match=message):
        _normalize_experiences(subject, [draft])


def test_normalize_evolve_draft_returns_approval_and_persistence_views():
    subject = normalize_subject({"kind": "skill", "name": "browser"}, allowed_kinds={"skill"})

    draft = normalize_evolve_draft(
        subject,
        [
            {
                "summary": "Use structured parser output before slicing text.",
                "content": "Prefer parser fields before slicing raw text.",
                "target": "body",
            }
        ],
    )

    assert draft.subject == subject
    assert draft.approval_view() == {
        "experiences": [
            {
                "summary": "Use structured parser output before slicing text.",
                "content": "Prefer parser fields before slicing raw text.",
                "target": "body",
                "section": "Troubleshooting",
                "reason": "",
            }
        ]
    }
    assert draft.persistence_view() == draft.approval_view()


def test_normalize_simplify_draft_validates_merge_shape():
    subject = normalize_subject({"kind": "skill", "name": "browser"}, allowed_kinds={"skill"})

    draft = normalize_simplify_draft(
        subject,
        [
            {
                "action": "merge",
                "record_id": "ev_1",
                "merge_remove_ids": ["ev_2"],
                "new_content": "Merged parser guidance.",
                "reason": "duplicate guidance",
            }
        ],
    )

    assert draft.actions[0]["action"] == "MERGE"
    assert draft.persistence_view()["actions"][0]["merge_remove_ids"] == ["ev_2"]


def test_validate_simplify_record_refs_rejects_missing_merge_remove_id():
    subject = normalize_subject({"kind": "skill", "name": "browser"}, allowed_kinds={"skill"})
    draft = normalize_simplify_draft(
        subject,
        [{"action": "MERGE", "record_id": "ev_1", "merge_remove_ids": ["ev_2"], "new_content": "Merged."}],
    )

    with pytest.raises(ValidationError, match="record not found: ev_2"):
        validate_simplify_record_refs(draft, existing_record_ids={"ev_1"})
