# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionScope group/user ids containing ':' must round-trip."""

from openjiuwen.core.session.session_controller.scope import (
    GroupUserSubject,
    MainScope,
    SessionScope,
)


def test_group_user_subject_with_colons_round_trips() -> None:
    subject = GroupUserSubject("team:a", "user:b")
    parsed = GroupUserSubject.from_string(str(subject))

    assert parsed == subject


def test_session_scope_with_colon_ids_round_trips() -> None:
    scope = SessionScope(MainScope(), GroupUserSubject("team:a", "user:b"))
    parsed = SessionScope.from_string(str(scope))

    assert parsed == scope
