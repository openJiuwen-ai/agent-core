# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path


class SessionPaths:
    """Static utility methods for constructing session storage paths."""

    @staticmethod
    def agent_dir(base_path: Path, agent_id: str) -> Path:
        """Get the root directory path for a specific agent."""
        return base_path / agent_id

    @staticmethod
    def sessions_dir(base_path: Path, agent_id: str) -> Path:
        """Get the sessions directory path for a specific agent."""
        return base_path / agent_id / "sessions"

    @staticmethod
    def meta_file(base_path: Path, agent_id: str) -> Path:
        """Get the path to the sessions.json metadata file for a specific agent."""
        return base_path / agent_id / "sessions" / "sessions.json"

    @staticmethod
    def session_dir(base_path: Path, agent_id: str, session_id: str) -> Path:
        """Get the directory path for a specific session."""
        return base_path / agent_id / "sessions" / session_id

    @staticmethod
    def state_file(session_dir: Path) -> Path:
        """Get the path to the state.data file within a session directory."""
        return session_dir / "state.data"

    @staticmethod
    def downstreams_dir(session_dir: Path) -> Path:
        """Get the path to the downstreams directory within a session directory."""
        return session_dir / "downstreams"

    @staticmethod
    def link_file(session_dir: Path, target_agent: str, target_session: str) -> Path:
        """Get the path to a downstream link file.

        Args:
            session_dir: The session directory containing the link.
            target_agent: The target agent identifier.
            target_session: The target session identifier.
        """
        return session_dir / "downstreams" / f"{target_agent}_{target_session}.link"
