"""Phase semantics, one source presentation, and a finite repair language."""

from __future__ import annotations

import pytest

from examples.adaptive_multi_agent_collab.workflows.prompts import (
    ROLE_SCHEMES,
    REVISION_CONTRACT,
    SCHEME_SEMANTICS,
    compose_backend_input,
    protocol_instructions,
    seed_prompt_collection,
)
from examples.adaptive_multi_agent_collab.workflows.repair import (
    REPAIR_DIAGNOSTICS,
    render_repair,
    repair_variants,
    select_repair_diagnostic,
)


@pytest.mark.parametrize("scheme", ROLE_SCHEMES)
def test_feedback_and_revision_have_different_fixed_roles_and_both_receive_answer_space(scheme: str) -> None:
    fields = {"admissible_answers": ("yes", "no")}
    role = protocol_instructions(f"role:{scheme}", fields)
    revision = protocol_instructions(f"revision:{scheme}", fields)
    assert SCHEME_SEMANTICS[scheme] in role and SCHEME_SEMANTICS[scheme] not in revision
    assert REVISION_CONTRACT in revision and REVISION_CONTRACT not in role
    for protocol in (role, revision):
        assert "('yes', 'no')" in protocol
        assert "one complete JSON object without Markdown" in protocol and "at most 60 words" in protocol
        assert "takes precedence over editable guidance" in protocol
    assert '"status" ("continue" or "complete") first' in role
    assert '"answer" first' in revision and "reject unsupported feedback and retain your answer" in revision
    assert "concrete feasible correction" in role and "completion does not certify correctness" in role
    if scheme == "S_D":
        assert "if one exists" in role and "Do not manufacture opposition" in role
    collection = seed_prompt_collection()
    for component, fixed in ((f"role:{scheme}", SCHEME_SEMANTICS[scheme]), (f"revision:{scheme}", REVISION_CONTRACT)):
        spec = collection.components[component]
        edited = collection.replace_template(component, spec.protected_scaffold + "Use decisive evidence.")
        assert fixed in "\n".join(edited.components[component].structural_constraints())
        assert edited.components[component].required_placeholders == frozenset({"message_text"})


def test_latest_message_and_each_judge_submission_are_presented_once_without_enclosing_field_copies() -> None:
    collection = seed_prompt_collection()
    payload = (
        "Query: QUERY_SENTINEL\nAnswer: ANSWER_SENTINEL\nExplanation: EXPLANATION_SENTINEL\nFeedback: FEEDBACK_SENTINEL"
    )
    for key in ("role:S_V", "revision:S_V"):
        body = collection.render(key, message_text=payload)
        messages = compose_backend_input(
            "agent", protocol_instructions(key, {"admissible_answers": ("yes", "no")}), body
        )
        assert messages[1]["content"].count(payload) == 1 and "QUERY_SENTINEL" not in messages[0]["content"]
        assert messages[0]["content"].count("Fixed protocol:") == 1
        assert SCHEME_SEMANTICS["S_V"] not in body and REVISION_CONTRACT not in body
    assert collection.components["judge:S_J"].required_placeholders == frozenset({"submissions"})
    body = collection.render("judge:S_J", submissions="tag-a: " + payload + "\ntag-b: " + payload)
    assert body.count(payload) == 2 and body.count("tag-a:") == body.count("tag-b:") == 1


def test_repair_catalogue_is_finite_immutable_and_does_not_copy_variable_errors() -> None:
    variants = repair_variants("original complete payload")
    assert len(variants) == 5 and variants[0] == "original complete payload"
    assert len(set(variants)) == len(variants)
    for error, context_invalid, key in (
        ("response is not a JSON object: PRIVATE_STACK", False, "json"),
        ("answer has missing field PRIVATE_STACK", False, "schema"),
        ("unknown choice PRIVATE_STACK", True, "admissible-answer"),
        ("output-token-limit: truncated PRIVATE_STACK", False, "truncation"),
    ):
        diagnostic = select_repair_diagnostic(error, context_invalid=context_invalid)
        assert diagnostic == REPAIR_DIAGNOSTICS[key] and "PRIVATE_STACK" not in diagnostic
        assert render_repair("original complete payload", diagnostic) in variants
    with pytest.raises(ValueError, match="catalogue"):
        render_repair("original", "arbitrary verbose failure dump")
    with pytest.raises(TypeError):
        REPAIR_DIAGNOSTICS["json"] = "mutable"  # type: ignore[index]
