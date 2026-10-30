# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""interrupt_answer builds the answer shape each interrupt kind expects."""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.interrupt import InterruptKind, interrupt_answer


@pytest.mark.parametrize(("kind", "kwargs", "expected"), [
    (InterruptKind.PERMISSION, {"approved": True}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.CONFIRM, {"approved": True, "remember": True},
     {"approved": True, "feedback": "", "auto_confirm": True, "persist_allow": False}),
    (InterruptKind.TEAM_TOOL_APPROVAL, {"approved": False, "feedback": "no"},
     {"approved": False, "feedback": "no", "auto_confirm": False}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"approved": True}, {"action": "allow_once"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "always"}, {"action": "allow_always"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "not this one"},
     {"action": "reject", "feedback": "not this one"}),
    (InterruptKind.PERMISSION, {"text": "yes"}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.PERMISSION, {"text": ""}, {"approved": False, "feedback": "", "auto_confirm": False}),
    (InterruptKind.ASK_USER, {"text": "blue"}, "blue"),
    (InterruptKind.UNDECLARED, {"approved": True}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.UNDECLARED, {"text": "a"},
     {"approved": True, "feedback": "", "auto_confirm": True, "persist_allow": False}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "allow_once"}, {"action": "allow_once"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "reject"}, {"action": "reject", "feedback": ""}),
])
def test_answer_shape_per_kind(kind, kwargs, expected):
    assert interrupt_answer(kind, **kwargs) == expected


@pytest.mark.parametrize(("kind", "kwargs"), [
    (InterruptKind.ASK_USER, {"approved": True}),
    (InterruptKind.PERMISSION, {}),
])
def test_an_answer_that_does_not_fit_the_kind_is_refused(kind, kwargs):
    with pytest.raises(ValueError):
        interrupt_answer(kind, **kwargs)


@pytest.mark.parametrize("word", ["y", "Yes", "ok", "approve", "true", "1", "allow", "allow_once"])
def test_words_that_approve_once(word):
    assert interrupt_answer(InterruptKind.PERMISSION, text=word) == {
        "approved": True, "feedback": "", "auto_confirm": False,
    }


@pytest.mark.parametrize("word", ["n", "no", "deny", "reject"])
def test_words_that_reject_without_feedback(word):
    assert interrupt_answer(InterruptKind.CONFIRM, text=word) == {
        "approved": False, "feedback": "", "auto_confirm": False,
    }
