# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.session.session_controller.scope import (
    MainScope,
    DirectSubject,
    GroupSubject,
    GroupUserSubject,
)
from openjiuwen.core.session.session_controller.scope_factory import SessionScopeFactory


class TestSessionScopeFactory:
    def test_create_main(self):
        # create_main returns a SessionScope with MainScope and no subject
        scope = SessionScopeFactory.create_main()
        assert isinstance(scope.scope, MainScope)
        assert scope.subject is None
        assert str(scope) == "main"

    def test_create_direct(self):
        # create_direct returns a SessionScope with MainScope and DirectSubject
        scope = SessionScopeFactory.create_direct("user1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, DirectSubject)
        assert scope.subject.user_id == "user1"
        assert str(scope) == "main:direct:user1"

    def test_create_group(self):
        # create_group returns a SessionScope with MainScope and GroupSubject
        scope = SessionScopeFactory.create_group("grp1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, GroupSubject)
        assert scope.subject.group_id == "grp1"
        assert str(scope) == "main:group:grp1"

    def test_create_group_user(self):
        # create_group_user returns a SessionScope with MainScope and GroupUserSubject
        scope = SessionScopeFactory.create_group_user("grp1", "user1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, GroupUserSubject)
        assert scope.subject.group_id == "grp1"
        assert scope.subject.user_id == "user1"
        assert str(scope) == "main:group:grp1:user:user1"

    def test_create_custom(self):
        # create_custom accepts arbitrary Scope and Subject
        scope = SessionScopeFactory.create_custom(
            scope=MainScope(), subject=DirectSubject("u1")
        )
        assert str(scope) == "main:direct:u1"

    def test_create_custom_no_subject(self):
        # create_custom with no subject returns a scope-only SessionScope
        scope = SessionScopeFactory.create_custom(scope=MainScope())
        assert scope.subject is None
        assert str(scope) == "main"

    def test_from_string_main(self):
        # from_string delegates to SessionScope.from_string for "main"
        scope = SessionScopeFactory.from_string("main")
        assert isinstance(scope.scope, MainScope)
        assert scope.subject is None

    def test_from_string_direct(self):
        # from_string delegates to SessionScope.from_string for direct scope
        scope = SessionScopeFactory.from_string("main:direct:user1")
        assert isinstance(scope.subject, DirectSubject)
        assert scope.subject.user_id == "user1"

    def test_from_string_group(self):
        # from_string delegates to SessionScope.from_string for group scope
        scope = SessionScopeFactory.from_string("main:group:grp1")
        assert isinstance(scope.subject, GroupSubject)
        assert scope.subject.group_id == "grp1"

    def test_from_string_group_user(self):
        # from_string delegates to SessionScope.from_string for group-user scope
        scope = SessionScopeFactory.from_string("main:group:grp1:user:user1")
        assert isinstance(scope.subject, GroupUserSubject)
        assert scope.subject.group_id == "grp1"
        assert scope.subject.user_id == "user1"
