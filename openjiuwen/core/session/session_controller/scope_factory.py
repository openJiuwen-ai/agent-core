# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Optional

from openjiuwen.core.session.session_controller.scope import (
    Scope,
    Subject,
    SessionScope,
    MainScope,
    DirectSubject,
    GroupSubject,
    GroupUserSubject,
)


class SessionScopeFactory:
    """
    Session scope factory class, providing static methods for creating common SessionScope instances.

    This factory simplifies the creation of built-in scope and subject combinations,
    while also supporting custom extensions.
    """

    @staticmethod
    def create_main() -> SessionScope:
        """
        Create a session scope with the main scope (no subject).

        Returns:
            SessionScope: Instance containing only MainScope.

        Example:
            >>> scope = SessionScopeFactory.create_main()
            >>> str(scope)
            'main'
        """
        return SessionScope(scope=MainScope())

    @staticmethod
    def create_direct(user_id: str) -> SessionScope:
        """
        Create a session scope for direct chat scenarios.

        Args:
            user_id (str): User unique identifier.

        Returns:
            SessionScope: Instance containing MainScope and DirectSubject.

        Example:
            >>> scope = SessionScopeFactory.create_direct("user123")
            >>> str(scope)
            'main:direct:user123'
        """
        return SessionScope(scope=MainScope(), subject=DirectSubject(user_id))

    @staticmethod
    def create_group(group_id: str) -> SessionScope:
        """
        Create a session scope for group chat scenarios.

        Args:
            group_id (str): Group unique identifier.

        Returns:
            SessionScope: Instance containing MainScope and GroupSubject.

        Example:
            >>> scope = SessionScopeFactory.create_group("group456")
            >>> str(scope)
            'main:group:group456'
        """
        return SessionScope(scope=MainScope(), subject=GroupSubject(group_id))

    @staticmethod
    def create_group_user(group_id: str, user_id: str) -> SessionScope:
        """
        Create a session scope for the user's perspective within a group chat.

        Args:
            group_id (str): Group identifier.
            user_id (str): User identifier.

        Returns:
            SessionScope: Instance containing MainScope and GroupUserSubject.

        Example:
            >>> scope = SessionScopeFactory.create_group_user("group456", "user789")
            >>> str(scope)
            'main:group:group456:user:user789'
        """
        return SessionScope(
            scope=MainScope(),
            subject=GroupUserSubject(group_id, user_id)
        )

    @staticmethod
    def create_custom(scope: Scope, subject: Optional[Subject] = None) -> SessionScope:
        """
        Create a session scope using custom scope and subject.

        Args:
            scope (Scope): Custom scope instance.
            subject (Optional[Subject]): Optional custom subject instance.

        Returns:
            SessionScope: Combined session scope object.
        """
        return SessionScope(scope=scope, subject=subject)

    @staticmethod
    def from_string(key_str: str) -> SessionScope:
        """
        Parse session scope from a string (delegates to SessionScope.from_string).

        Args:
            key_str (str): String representation of SessionScope.

        Returns:
            SessionScope: Parsed instance.
        """
        return SessionScope.from_string(key_str)
