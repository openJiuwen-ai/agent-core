# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from openjiuwen.core.session.session_controller.data_container import (
    DEFAULT_DATA_CONTAINER_TYPE,
)


@dataclass
class SessionMeta:
    """
    Metadata for a single session.

    Each session entry stored in sessions.json.
    """

    session_id: str
    """Session unique identifier (UUID)."""

    created_at: float
    """Session creation timestamp (UTC seconds)."""

    updated_at: float
    """Session last update timestamp (UTC seconds)."""

    version: int
    """Session data version number, used for optimistic locking or migration."""

    is_active: bool
    """Whether this is the currently active session. Only one active session is allowed per SessionScopeKey."""

    data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE
    """Type of data container used by this session, must be registered in DataContainerFactory."""

    @classmethod
    def create_new(
            cls,
            session_id: str,
            version: int = 1,
            data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE
    ) -> 'SessionMeta':
        """
        Create new session metadata.

        Args:
            session_id (str): Session ID
            version (int): Initial version number, defaults to 1
            data_container_type (str): Data container type, defaults to DEFAULT_DATA_CONTAINER_TYPE

        Returns:
            SessionMeta: Newly created metadata object
        """
        now = datetime.now(timezone.utc).timestamp()
        return cls(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            version=version,
            is_active=True,
            data_container_type=data_container_type
        )

    def update_timestamp(self) -> None:
        """Update the update timestamp."""
        self.updated_at = datetime.now(timezone.utc).timestamp()

    def increment_version(self) -> None:
        """Increment the version number."""
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionMeta':
        """Create instance from dictionary."""
        if 'data_container_type' not in data:
            data['data_container_type'] = DEFAULT_DATA_CONTAINER_TYPE
        return cls(**data)


@dataclass
class ScopeSessionsMeta:
    """
    Session collection metadata under a specific SessionScopeKey.

    Used by SessionController to manage multiple historical sessions
    within the same session scope.
    """

    session_scope_key: str
    """The string representation of the corresponding SessionScopeKey."""

    active_session: Optional[str] = None
    """The session_id of the currently active session, or None if there is none."""

    sessions: list['SessionMeta'] = field(default_factory=list)
    """List of metadata for all sessions under this scope, sorted by update time descending."""

    def get_session(self, session_id: str) -> Optional[SessionMeta]:
        """
        Get session metadata by session ID.

        Args:
            session_id (str): Session ID

        Returns:
            Optional[SessionMeta]: Found session metadata, or None if not found
        """
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def add_session(self, session_meta: SessionMeta) -> None:
        """
        Add session metadata.

        Args:
            session_meta (SessionMeta): Session metadata to add
        """
        # If adding an active session, deactivate other sessions first
        if session_meta.is_active:
            self.deactivate_all_sessions()
            self.active_session = session_meta.session_id
        self.sessions.append(session_meta)
        self.sort_sessions()

    def remove_session(self, session_id: str) -> Optional[SessionMeta]:
        """
        Remove metadata for the specified session.

        Args:
            session_id (str): Session ID to remove

        Returns:
            Optional[SessionMeta]: Removed session metadata, or None if not found
        """
        for i, session in enumerate(self.sessions):
            if session.session_id == session_id:
                removed_session = self.sessions.pop(i)
                # If removing the active session, clear the active session marker
                if self.active_session == session_id:
                    self.active_session = None
                return removed_session
        return None

    def activate_session(self, session_id: str) -> bool:
        """
        Activate the specified session.

        Args:
            session_id (str): Session ID to activate

        Returns:
            bool: Whether activation succeeded
        """
        session = self.get_session(session_id)
        if session:
            self.deactivate_all_sessions()
            session.is_active = True
            session.update_timestamp()
            self.active_session = session_id
            self.sort_sessions()
            return True
        return False

    def deactivate_all_sessions(self) -> None:
        """Deactivate all sessions."""
        for session in self.sessions:
            session.is_active = False
        self.active_session = None

    def sort_sessions(self) -> None:
        """Sort session list by update time descending."""
        self.sessions.sort(key=lambda x: x.updated_at, reverse=True)

    def get_active_session(self) -> Optional[SessionMeta]:
        """
        Get the active session's metadata.

        Returns:
            Optional[SessionMeta]: Active session metadata, or None if no active session
        """
        if self.active_session:
            return self.get_session(self.active_session)
        return None

    def update_session_timestamp(self, session_id: str) -> bool:
        """
        Update the timestamp of the specified session.

        Args:
            session_id (str): Session ID

        Returns:
            bool: Whether the update succeeded
        """
        session = self.get_session(session_id)
        if session:
            session.update_timestamp()
            self.sort_sessions()
            return True
        return False

    def increment_session_version(self, session_id: str) -> bool:
        """Increment the version number of the specified session.

        Args:
            session_id: Session ID.

        Returns:
            Whether the increment succeeded.
        """
        session = self.get_session(session_id)
        if session:
            session.increment_version()
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            'session_scope_key': self.session_scope_key,
            'active_session': self.active_session,
            'sessions': [session.to_dict() for session in self.sessions]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ScopeSessionsMeta':
        """Create instance from dictionary."""
        sessions = [
            SessionMeta.from_dict(session_data)
            for session_data in data.get('sessions', [])
        ]
        return cls(
            session_scope_key=data['session_scope_key'],
            active_session=data.get('active_session'),
            sessions=sessions
        )
