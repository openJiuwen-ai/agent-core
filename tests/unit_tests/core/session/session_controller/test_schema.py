# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.session.session_controller.schema import (
    SessionMeta,
    ScopeSessionsMeta,
)


class TestSessionMeta:
    def test_create_new(self):
        # create_new sets session_id, is_active=True, version=1, and timestamps
        meta = SessionMeta.create_new("session-1")
        assert meta.session_id == "session-1"
        assert meta.is_active is True
        assert meta.version == 1
        assert meta.created_at > 0
        assert meta.updated_at > 0
        assert meta.created_at == meta.updated_at

    def test_create_new_with_version(self):
        # create_new accepts a custom initial version
        meta = SessionMeta.create_new("session-2", version=5)
        assert meta.version == 5

    def test_update_timestamp(self):
        # update_timestamp advances the updated_at field
        meta = SessionMeta.create_new("session-3")
        old_updated = meta.updated_at
        meta.update_timestamp()
        assert meta.updated_at >= old_updated

    def test_increment_version(self):
        # increment_version increases the version by 1
        meta = SessionMeta.create_new("session-4")
        meta.increment_version()
        assert meta.version == 2

    def test_to_dict(self):
        # to_dict includes all required fields
        meta = SessionMeta.create_new("session-5")
        d = meta.to_dict()
        assert d["session_id"] == "session-5"
        assert "created_at" in d
        assert "updated_at" in d
        assert "version" in d
        assert "is_active" in d

    def test_from_dict(self):
        # from_dict round-trips with to_dict
        meta = SessionMeta.create_new("session-6")
        d = meta.to_dict()
        restored = SessionMeta.from_dict(d)
        assert restored.session_id == meta.session_id
        assert restored.version == meta.version
        assert restored.is_active == meta.is_active

    def test_from_dict_missing_container_type(self):
        d = {
            "session_id": "s1",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "version": 1,
            "is_active": True,
        }
        restored = SessionMeta.from_dict(d)
        assert restored.data_container_type == "agent"


class TestScopeSessionsMeta:
    def test_init(self):
        # Fresh ScopeSessionsMeta has no active session and empty sessions list
        meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        assert meta.session_scope_key == "agent:a1:main"
        assert meta.active_session is None
        assert meta.sessions == []

    def test_add_session(self):
        # Adding a session sets it as active
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        session_meta = SessionMeta.create_new("s1")
        scope_meta.add_session(session_meta)
        assert len(scope_meta.sessions) == 1
        assert scope_meta.active_session == "s1"

    def test_add_inactive_session(self):
        # Adding an inactive session does not change the active session
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        active = SessionMeta.create_new("s1")
        active.is_active = True
        scope_meta.add_session(active)
        inactive = SessionMeta.create_new("s2")
        inactive.is_active = False
        scope_meta.add_session(inactive)
        assert scope_meta.active_session == "s1"
        assert len(scope_meta.sessions) == 2

    def test_add_active_deactivates_others(self):
        # Adding a new active session deactivates the previous one
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        scope_meta.add_session(s1)
        assert s1.is_active is True
        s2 = SessionMeta.create_new("s2")
        scope_meta.add_session(s2)
        assert s1.is_active is False
        assert s2.is_active is True
        assert scope_meta.active_session == "s2"

    def test_get_session(self):
        # get_session returns the matching session meta
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        session_meta = SessionMeta.create_new("s1")
        scope_meta.add_session(session_meta)
        found = scope_meta.get_session("s1")
        assert found is session_meta

    def test_get_session_not_found(self):
        # get_session returns None for non-existent session_id
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        assert scope_meta.get_session("nonexistent") is None

    def test_remove_session(self):
        # Removing a session clears it from the list and resets active_session
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        session_meta = SessionMeta.create_new("s1")
        scope_meta.add_session(session_meta)
        removed = scope_meta.remove_session("s1")
        assert removed is session_meta
        assert len(scope_meta.sessions) == 0
        assert scope_meta.active_session is None

    def test_remove_session_not_found(self):
        # Removing a non-existent session returns None
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        assert scope_meta.remove_session("nonexistent") is None

    def test_activate_session(self):
        # Activating a session deactivates all others
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        s1.is_active = True
        scope_meta.add_session(s1)
        s2 = SessionMeta.create_new("s2")
        s2.is_active = False
        scope_meta.add_session(s2)
        result = scope_meta.activate_session("s2")
        assert result is True
        assert s1.is_active is False
        assert s2.is_active is True
        assert scope_meta.active_session == "s2"

    def test_activate_nonexistent_session(self):
        # Activating a non-existent session returns False
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        result = scope_meta.activate_session("nonexistent")
        assert result is False

    def test_deactivate_all_sessions(self):
        # deactivate_all_sessions sets all sessions to inactive
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        scope_meta.add_session(s1)
        scope_meta.deactivate_all_sessions()
        assert s1.is_active is False
        assert scope_meta.active_session is None

    def test_get_active_session(self):
        # get_active_session returns the currently active session meta
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        scope_meta.add_session(s1)
        active = scope_meta.get_active_session()
        assert active is s1

    def test_get_active_session_none(self):
        # get_active_session returns None when no session is active
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        assert scope_meta.get_active_session() is None

    def test_update_session_timestamp(self):
        # update_session_timestamp advances the session's updated_at
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        old_updated = s1.updated_at
        scope_meta.add_session(s1)
        result = scope_meta.update_session_timestamp("s1")
        assert result is True
        assert s1.updated_at >= old_updated

    def test_update_session_timestamp_not_found(self):
        # update_session_timestamp returns False for non-existent session
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        result = scope_meta.update_session_timestamp("nonexistent")
        assert result is False

    def test_increment_session_version(self):
        # increment_session_version increases the session's version by 1
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        scope_meta.add_session(s1)
        result = scope_meta.increment_session_version("s1")
        assert result is True
        assert s1.version == 2

    def test_increment_session_version_not_found(self):
        # increment_session_version returns False for non-existent session
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        result = scope_meta.increment_session_version("nonexistent")
        assert result is False

    def test_to_dict_and_from_dict(self):
        # ScopeSessionsMeta round-trips through to_dict/from_dict
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        scope_meta.add_session(s1)
        d = scope_meta.to_dict()
        restored = ScopeSessionsMeta.from_dict(d)
        assert restored.session_scope_key == "agent:a1:main"
        assert restored.active_session == "s1"
        assert len(restored.sessions) == 1
        assert restored.sessions[0].session_id == "s1"

    def test_sort_sessions(self):
        # sort_sessions orders sessions by updated_at descending
        scope_meta = ScopeSessionsMeta(session_scope_key="agent:a1:main")
        s1 = SessionMeta.create_new("s1")
        s1.updated_at = 1000.0
        scope_meta.sessions.append(s1)
        s2 = SessionMeta.create_new("s2")
        s2.updated_at = 2000.0
        scope_meta.sessions.append(s2)
        scope_meta.sort_sessions()
        assert scope_meta.sessions[0].session_id == "s2"
        assert scope_meta.sessions[1].session_id == "s1"
