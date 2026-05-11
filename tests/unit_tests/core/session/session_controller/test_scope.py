# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.session.session_controller.scope import (
    MainScope,
    DirectSubject,
    GroupSubject,
    GroupUserSubject,
    SessionScope,
    SessionScopeKey,
)


class TestMainScope:
    def test_str(self):
        # MainScope serializes to "main"
        assert str(MainScope()) == "main"

    def test_from_string_valid(self):
        # Parsing "main" returns a MainScope instance
        scope = MainScope.from_string("main")
        assert isinstance(scope, MainScope)

    def test_from_string_invalid(self):
        # Parsing a non-"main" string raises ValueError
        with pytest.raises(ValueError, match="Expected 'main'"):
            MainScope.from_string("other")

    def test_equality(self):
        # Two MainScope instances are equal
        assert MainScope() == MainScope()

    def test_hash(self):
        # Two MainScope instances have the same hash
        assert hash(MainScope()) == hash(MainScope())

    def test_not_equal_other_type(self):
        # MainScope is not equal to a plain string
        assert MainScope() != "main"


class TestDirectSubject:
    def test_str(self):
        # DirectSubject serializes to "direct:{user_id}"
        assert str(DirectSubject("user1")) == "direct:user1"

    def test_from_string_valid(self):
        # Parsing "direct:user1" returns a DirectSubject with user_id="user1"
        subject = DirectSubject.from_string("direct:user1")
        assert subject.user_id == "user1"

    def test_from_string_invalid_prefix(self):
        # Parsing a string without "direct:" prefix raises ValueError
        with pytest.raises(ValueError, match="must start with 'direct:'"):
            DirectSubject.from_string("group:user1")

    def test_from_string_empty_user_id(self):
        # Parsing "direct:" with no user_id raises ValueError
        with pytest.raises(ValueError, match="user_id cannot be empty"):
            DirectSubject.from_string("direct:")

    def test_equality(self):
        # DirectSubjects with the same user_id are equal; different ids are not
        assert DirectSubject("user1") == DirectSubject("user1")
        assert DirectSubject("user1") != DirectSubject("user2")

    def test_hash(self):
        # DirectSubjects with the same user_id have the same hash and deduplicate in sets
        assert hash(DirectSubject("user1")) == hash(DirectSubject("user1"))
        s = {DirectSubject("user1"), DirectSubject("user1")}
        assert len(s) == 1


class TestGroupSubject:
    def test_str(self):
        # GroupSubject serializes to "group:{group_id}"
        assert str(GroupSubject("grp1")) == "group:grp1"

    def test_from_string_valid(self):
        # Parsing "group:grp1" returns a GroupSubject with group_id="grp1"
        subject = GroupSubject.from_string("group:grp1")
        assert subject.group_id == "grp1"

    def test_from_string_invalid_prefix(self):
        # Parsing a string without "group:" prefix raises ValueError
        with pytest.raises(ValueError, match="must start with 'group:'"):
            GroupSubject.from_string("direct:grp1")

    def test_from_string_empty_group_id(self):
        # Parsing "group:" with no group_id raises ValueError
        with pytest.raises(ValueError, match="group_id cannot be empty"):
            GroupSubject.from_string("group:")

    def test_equality(self):
        # GroupSubjects with the same group_id are equal; different ids are not
        assert GroupSubject("grp1") == GroupSubject("grp1")
        assert GroupSubject("grp1") != GroupSubject("grp2")

    def test_hash(self):
        # GroupSubjects with the same group_id have the same hash
        assert hash(GroupSubject("grp1")) == hash(GroupSubject("grp1"))


class TestGroupUserSubject:
    def test_str(self):
        # GroupUserSubject serializes to "group:{group_id}:user:{user_id}"
        assert str(GroupUserSubject("grp1", "user1")) == "group:grp1:user:user1"

    def test_from_string_valid(self):
        # Parsing "group:grp1:user:user1" returns a GroupUserSubject
        subject = GroupUserSubject.from_string("group:grp1:user:user1")
        assert subject.group_id == "grp1"
        assert subject.user_id == "user1"

    def test_from_string_invalid_format(self):
        # Parsing a string with wrong format raises ValueError
        with pytest.raises(ValueError, match="format"):
            GroupUserSubject.from_string("group:grp1")

    def test_from_string_empty_ids(self):
        # Empty group_id or user_id raises ValueError
        with pytest.raises(ValueError, match="cannot be empty"):
            GroupUserSubject.from_string("group::user:user1")
        with pytest.raises(ValueError, match="cannot be empty"):
            GroupUserSubject.from_string("group:grp1:user:")

    def test_equality(self):
        # Equality depends on both group_id and user_id
        assert GroupUserSubject("g1", "u1") == GroupUserSubject("g1", "u1")
        assert GroupUserSubject("g1", "u1") != GroupUserSubject("g1", "u2")
        assert GroupUserSubject("g1", "u1") != GroupUserSubject("g2", "u1")

    def test_hash(self):
        # Same group_id + user_id produce the same hash
        assert hash(GroupUserSubject("g1", "u1")) == hash(GroupUserSubject("g1", "u1"))


class TestSessionScope:
    def test_str_scope_only(self):
        # SessionScope without subject serializes to just the scope string
        scope = SessionScope(scope=MainScope())
        assert str(scope) == "main"

    def test_str_scope_with_direct_subject(self):
        # SessionScope with DirectSubject serializes to "main:direct:{user_id}"
        scope = SessionScope(scope=MainScope(), subject=DirectSubject("user1"))
        assert str(scope) == "main:direct:user1"

    def test_str_scope_with_group_subject(self):
        # SessionScope with GroupSubject serializes to "main:group:{group_id}"
        scope = SessionScope(scope=MainScope(), subject=GroupSubject("grp1"))
        assert str(scope) == "main:group:grp1"

    def test_str_scope_with_group_user_subject(self):
        # SessionScope with GroupUserSubject serializes to "main:group:{gid}:user:{uid}"
        scope = SessionScope(scope=MainScope(), subject=GroupUserSubject("grp1", "user1"))
        assert str(scope) == "main:group:grp1:user:user1"

    def test_from_string_main_only(self):
        # Parsing "main" returns a SessionScope with no subject
        scope = SessionScope.from_string("main")
        assert isinstance(scope.scope, MainScope)
        assert scope.subject is None

    def test_from_string_direct(self):
        # Parsing "main:direct:user1" returns a SessionScope with DirectSubject
        scope = SessionScope.from_string("main:direct:user1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, DirectSubject)
        assert scope.subject.user_id == "user1"

    def test_from_string_group(self):
        # Parsing "main:group:grp1" returns a SessionScope with GroupSubject
        scope = SessionScope.from_string("main:group:grp1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, GroupSubject)
        assert scope.subject.group_id == "grp1"

    def test_from_string_group_user(self):
        # Parsing "main:group:grp1:user:user1" returns a SessionScope with GroupUserSubject
        scope = SessionScope.from_string("main:group:grp1:user:user1")
        assert isinstance(scope.scope, MainScope)
        assert isinstance(scope.subject, GroupUserSubject)
        assert scope.subject.group_id == "grp1"
        assert scope.subject.user_id == "user1"

    def test_from_string_unknown_scope(self):
        # Parsing an unrecognized scope string raises ValueError
        with pytest.raises(ValueError, match="Unknown scope"):
            SessionScope.from_string("unknown:direct:user1")

    def test_from_string_unknown_subject(self):
        # Parsing an unrecognized subject format raises ValueError
        with pytest.raises(ValueError, match="Unknown subject format"):
            SessionScope.from_string("main:unknown_format")

    def test_frozen(self):
        # SessionScope is frozen and does not allow attribute reassignment
        scope = SessionScope(scope=MainScope())
        with pytest.raises(AttributeError):
            scope.scope = MainScope()


class TestSessionScopeKey:
    def test_str(self):
        # SessionScopeKey serializes to "agent:{agent_id}:{session_scope}"
        scope = SessionScope(scope=MainScope(), subject=DirectSubject("user1"))
        key = SessionScopeKey(agent_id="agent1", session_scope=scope)
        assert str(key) == "agent:agent1:main:direct:user1"

    def test_from_string_valid(self):
        # Parsing a full key string restores agent_id and session_scope
        key = SessionScopeKey.from_string("agent:agent1:main:direct:user1")
        assert key.agent_id == "agent1"
        assert isinstance(key.session_scope.scope, MainScope)
        assert isinstance(key.session_scope.subject, DirectSubject)
        assert key.session_scope.subject.user_id == "user1"

    def test_from_string_main_only(self):
        # Parsing a key with no subject returns a scope with subject=None
        key = SessionScopeKey.from_string("agent:agent2:main")
        assert key.agent_id == "agent2"
        assert key.session_scope.subject is None

    def test_from_string_invalid_prefix(self):
        # Parsing a string without "agent:" prefix raises ValueError
        with pytest.raises(ValueError, match="must start with 'agent:'"):
            SessionScopeKey.from_string("main:direct:user1")

    def test_equality(self):
        # Two keys with the same agent_id and scope are equal
        scope = SessionScope(scope=MainScope())
        key1 = SessionScopeKey(agent_id="a1", session_scope=scope)
        key2 = SessionScopeKey(agent_id="a1", session_scope=scope)
        assert key1 == key2

    def test_hash(self):
        # Two equal keys have the same hash and deduplicate in sets
        scope = SessionScope(scope=MainScope())
        key1 = SessionScopeKey(agent_id="a1", session_scope=scope)
        key2 = SessionScopeKey(agent_id="a1", session_scope=scope)
        assert hash(key1) == hash(key2)
        assert len({key1, key2}) == 1
