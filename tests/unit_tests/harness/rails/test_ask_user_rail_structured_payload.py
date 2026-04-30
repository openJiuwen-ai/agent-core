# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail, AskUserPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import InterruptResult, RejectResult


def _build_tool_call(arguments: dict, tool_call_id: str = "tool_ask_1") -> ToolCall:
    return ToolCall(
        id=tool_call_id,
        type="function",
        name="ask_user",
        arguments=json.dumps(arguments),
        index=0,
    )


def _single_question_args() -> dict:
    return {
        "questions": [
            {
                "header": "Feature",
                "question": "Which feature should be enabled?",
                "options": [
                    {"label": "Dark Mode", "description": "Enable dark theme."},
                    {"label": "Auto Save", "description": "Save changes automatically."},
                ],
                "multi_select": False,
            }
        ]
    }


def _single_question_with_preview_args() -> dict:
    return {
        "questions": [
            {
                "header": "Design",
                "question": "Which design do you prefer?",
                "options": [
                    {
                        "label": "Option A",
                        "description": "Simple layout with sidebar.",
                        "preview": "┌──────┬──────────┐\n│ nav  │ content  │\n│ bar  │ area     │\n└──────┴──────────┘",
                    },
                    {
                        "label": "Option B",
                        "description": "Full-width layout.",
                        "preview": "┌────────────────────┐\n│     content area   │\n└────────────────────┘",
                    },
                ],
                "multi_select": False,
            }
        ]
    }


def _multi_question_args() -> dict:
    return {
        "questions": [
            {
                "header": "Framework",
                "question": "Which framework?",
                "options": [
                    {"label": "React", "description": "React ecosystem."},
                    {"label": "Vue", "description": "Vue ecosystem."},
                ],
                "multi_select": False,
            },
            {
                "header": "Auth",
                "question": "How to authenticate?",
                "options": [
                    {"label": "JWT", "description": "Token auth."},
                    {"label": "Session", "description": "Session-based auth."},
                ],
                "multi_select": False,
            },
        ]
    }


@pytest.mark.asyncio
async def test_first_call_interrupt_contains_questions_field():
    """First call with questions should return InterruptResult with questions field."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_args())

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=None)

    assert isinstance(decision, InterruptResult)
    questions = decision.request.questions
    assert questions is not None
    assert len(questions) == 1
    assert questions[0]["question"] == "Which feature should be enabled?"
    assert questions[0]["header"] == "Feature"
    assert decision.request.message == ""
    payload_schema = decision.request.payload_schema
    assert "answers" in payload_schema.get("properties", {})


@pytest.mark.asyncio
async def test_resume_with_answer_string_returns_formatted_result():
    """Resume with answer string should return formatted tool result."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_args())
    user_input = {"answers": {"Which feature should be enabled?": "Dark Mode"}}

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert "User has answered your questions:" in decision.tool_result
    assert '"Which feature should be enabled?"="Dark Mode"' in decision.tool_result


@pytest.mark.asyncio
async def test_resume_with_ask_user_payload_returns_formatted_result():
    """Resume with AskUserPayload should return formatted tool result."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_args())
    user_input = AskUserPayload(answers={"Which feature should be enabled?": "Auto Save"})

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert "User has answered your questions:" in decision.tool_result
    assert '"Which feature should be enabled?"="Auto Save"' in decision.tool_result


@pytest.mark.asyncio
async def test_resume_with_string_for_multi_question_only_answers_first():
    """Resume with string for multi-question should only answer first question."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_multi_question_args())
    user_input = "React"

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert "User has answered your questions:" in decision.tool_result
    assert '"Which framework?"="React"' in decision.tool_result
    assert '"How to authenticate?"=""' in decision.tool_result


@pytest.mark.asyncio
async def test_resume_with_structured_answers_returns_formatted_result():
    """Resume with structured answers should return formatted tool result."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_multi_question_args())
    user_input = {
        "answers": {
            "Which framework?": "React",
            "How to authenticate?": "JWT",
        }
    }

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert "User has answered your questions:" in decision.tool_result
    assert '"Which framework?"="React"' in decision.tool_result
    assert '"How to authenticate?"="JWT"' in decision.tool_result


@pytest.mark.asyncio
async def test_resume_with_string_directly_returns_formatted_result():
    """Resume with string directly should return formatted tool result."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_args())
    user_input = "Dark Mode"

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert "User has answered your questions:" in decision.tool_result
    assert '"Which feature should be enabled?"="Dark Mode"' in decision.tool_result


@pytest.mark.asyncio
async def test_multi_question_interrupt_contains_all_questions():
    """Multi-question interrupt should contain all questions in questions field."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_multi_question_args())

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=None)

    assert isinstance(decision, InterruptResult)
    questions = decision.request.questions
    assert questions is not None
    assert len(questions) == 2
    assert questions[0]["header"] == "Framework"
    assert questions[1]["header"] == "Auth"


@pytest.mark.asyncio
async def test_invalid_user_input_returns_interrupt():
    """Invalid user input should return InterruptResult to re-prompt."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_args())
    user_input = {"invalid_field": "value"}

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, InterruptResult)


@pytest.mark.asyncio
async def test_empty_questions_returns_interrupt():
    """Empty questions should still return InterruptResult."""
    rail = AskUserRail()
    tool_call = _build_tool_call({"questions": []})

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=None)

    assert isinstance(decision, InterruptResult)
    assert decision.request.questions == []
    assert decision.request.message == ""


@pytest.mark.asyncio
async def test_no_tool_call_returns_interrupt():
    """No tool_call should return InterruptResult with empty questions."""
    rail = AskUserRail()

    decision = await rail.resolve_interrupt(ctx=None, tool_call=None, user_input=None)

    assert isinstance(decision, InterruptResult)
    assert decision.request.questions == []
    assert decision.request.message == ""


@pytest.mark.asyncio
async def test_preview_field_passed_through_in_questions():
    """Preview field on options should be passed through in questions field."""
    rail = AskUserRail()
    tool_call = _build_tool_call(_single_question_with_preview_args())

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=None)

    assert isinstance(decision, InterruptResult)
    questions = decision.request.questions
    assert questions is not None
    assert len(questions) == 1
    options = questions[0].get("options", [])
    assert len(options) == 2
    assert "preview" in options[0]
    assert options[0]["preview"].startswith("┌")
    assert "preview" in options[1]
    assert options[1]["preview"].startswith("┌")


@pytest.mark.asyncio
async def test_no_questions_returns_simple_answer():
    """No questions in tool_call should return simple answers dict."""
    rail = AskUserRail()
    tool_call = _build_tool_call({"questions": []})
    user_input = {"answers": {"": "simple answer"}}

    decision = await rail.resolve_interrupt(ctx=None, tool_call=tool_call, user_input=user_input)

    assert isinstance(decision, RejectResult)
    assert decision.tool_result == "{'': 'simple answer'}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
