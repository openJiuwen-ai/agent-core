# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

from openjiuwen.core.session.session_controller.utils import SessionPaths


class TestSessionPaths:
    def test_agent_dir(self):
        # agent_dir returns base_path / agent_id
        result = SessionPaths.agent_dir(Path("/base"), "agent1")
        assert result == Path("/base/agent1")

    def test_sessions_dir(self):
        # sessions_dir returns base_path / agent_id / "sessions"
        result = SessionPaths.sessions_dir(Path("/base"), "agent1")
        assert result == Path("/base/agent1/sessions")

    def test_meta_file(self):
        # meta_file returns the sessions.json path under the sessions directory
        result = SessionPaths.meta_file(Path("/base"), "agent1")
        assert result == Path("/base/agent1/sessions/sessions.json")

    def test_session_dir(self):
        # session_dir returns the directory for a specific session
        result = SessionPaths.session_dir(Path("/base"), "agent1", "sess1")
        assert result == Path("/base/agent1/sessions/sess1")

    def test_state_file(self):
        # state_file returns the state.data path inside a session directory
        result = SessionPaths.state_file(Path("/sess1"))
        assert result == Path("/sess1/state.data")

    def test_downstreams_dir(self):
        # downstreams_dir returns the downstreams subdirectory path
        result = SessionPaths.downstreams_dir(Path("/sess1"))
        assert result == Path("/sess1/downstreams")

    def test_link_file(self):
        # link_file returns the .link file path for a downstream relationship
        result = SessionPaths.link_file(Path("/sess1"), "agent2", "sess2")
        assert result == Path("/sess1/downstreams/agent2_sess2.link")
