# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class Scope(ABC):
    """
    Scope abstract base class.

    A scope defines the basic boundary for data isolation. Users can implement
    custom scopes by inheriting from this class. Each scope corresponds to an
    independent storage namespace for distinguishing different isolation strategies.
    """

    @abstractmethod
    def __str__(self) -> str:
        """
        Convert the scope object to a string representation for serialization
        and storage key generation.

        Returns:
            str: The string form of the scope, e.g., "main" or a custom format.
        """
        pass

    @classmethod
    @abstractmethod
    def from_string(cls, scope_str: str) -> 'Scope':
        """
        Parse and construct a scope object from a string.

        Args:
            scope_str (str): The string representation of the scope.

        Returns:
            Scope: A scope instance of the corresponding type.

        Raises:
            ValueError: Raised when the string format does not match expectations.
        """
        pass


class MainScope(Scope):
    """
    Main scope, the system's built-in default scope.

    Used for general scenarios that do not involve additional tenant or
    application-level isolation. The string representation is the fixed value "main".
    """

    def __str__(self) -> str:
        return "main"

    @classmethod
    def from_string(cls, scope_str: str) -> 'MainScope':
        if scope_str != "main":
            raise ValueError(f"Expected 'main', got '{scope_str}'")
        return cls()

    def __eq__(self, other):
        if not isinstance(other, MainScope):
            return False
        return True

    def __hash__(self):
        return hash("main")


class Subject(ABC):
    """
    Subject abstract base class.

    A subject identifies a conversation participant (e.g., user, group) in a session,
    further subdividing data isolation within a scope. Users can implement custom
    subject types by inheriting from this class.
    """

    @abstractmethod
    def __str__(self) -> str:
        """
        Convert the subject object to a string representation.

        Returns:
            str: The string form of the subject, format defined by concrete subclasses.
        """
        pass

    @classmethod
    @abstractmethod
    def from_string(cls, subject_str: str) -> 'Subject':
        """
        Parse and construct a subject object from a string.

        Args:
            subject_str (str): The string representation of the subject.

        Returns:
            Subject: A subject instance of the corresponding type.

        Raises:
            ValueError: Raised when the string format is incorrect.
        """
        pass


class DirectSubject(Subject):
    """
    Direct/private chat subject.

    Used for one-on-one private conversation scenarios, with data isolated
    to a specific user.
    String format: direct:{user_id}
    """

    def __init__(self, user_id: str):
        """
        Args:
            user_id (str): The user's unique identifier.
        """
        self.user_id = user_id

    def __str__(self) -> str:
        return f"direct:{self.user_id}"

    @classmethod
    def from_string(cls, subject_str: str) -> 'DirectSubject':
        if not subject_str.startswith("direct:"):
            raise ValueError(f"DirectSubject must start with 'direct:', got '{subject_str}'")
        user_id = subject_str[7:]
        if not user_id:
            raise ValueError("DirectSubject user_id cannot be empty")
        return cls(user_id)

    def __eq__(self, other):
        if not isinstance(other, DirectSubject):
            return False
        return self.user_id == other.user_id

    def __hash__(self):
        return hash(f"direct:{self.user_id}")


class GroupSubject(Subject):
    """
    Group subject.

    Used for group chat scenarios, where data is isolated by group and
    group members share session context.
    String format: group:{group_id}
    """

    def __init__(self, group_id: str):
        """
        Args:
            group_id (str): The group's unique identifier.
        """
        self.group_id = group_id

    def __str__(self) -> str:
        return f"group:{self.group_id}"

    @classmethod
    def from_string(cls, subject_str: str) -> 'GroupSubject':
        if not subject_str.startswith("group:"):
            raise ValueError(
                f"GroupSubject must start with 'group:', got '{subject_str}'"
            )
        group_id = subject_str[6:]
        if not group_id:
            raise ValueError("GroupSubject group_id cannot be empty")
        return cls(group_id)

    def __eq__(self, other):
        if not isinstance(other, GroupSubject):
            return False
        return self.group_id == other.group_id

    def __hash__(self):
        return hash(f"group:{self.group_id}")


class GroupUserSubject(Subject):
    """
    Group user subject.

    Used for the isolated perspective of a specific user within a group chat,
    e.g., recording a user's personal preferences or temporary state within the group.
    String format: group:{group_id}:user:{user_id}
    """

    def __init__(self, group_id: str, user_id: str):
        """
        Args:
            group_id (str): Group identifier.
            user_id (str): User identifier.
        """
        self.group_id = group_id
        self.user_id = user_id

    def __str__(self) -> str:
        return f"group:{self.group_id}:user:{self.user_id}"

    @classmethod
    def from_string(cls, subject_str: str) -> 'GroupUserSubject':
        parts = subject_str.split(":")
        if len(parts) != 4 or parts[0] != "group" or parts[2] != "user":
            raise ValueError(
                f"GroupUserSubject must have format "
                f"'group:{{group_id}}:user:{{user_id}}', got '{subject_str}'"
            )
        group_id, user_id = parts[1], parts[3]
        if not group_id or not user_id:
            raise ValueError("GroupUserSubject group_id and user_id cannot be empty")
        return cls(group_id, user_id)

    def __eq__(self, other):
        if not isinstance(other, GroupUserSubject):
            return False
        return self.group_id == other.group_id and self.user_id == other.user_id

    def __hash__(self):
        return hash(f"group:{self.group_id}:user:{self.user_id}")


@dataclass(frozen=True)
class SessionScope:
    """
    Session scope, composed of a scope and an optional subject, defining the boundary of data isolation.

    SessionScope is the logical grouping identifier for sessions within an Agent;
    different SessionScopes under the same Agent have completely isolated data.
    """

    scope: Scope
    """Scope object, defining the basic type of data isolation."""

    subject: Optional[Subject] = None
    """Optional subject object, used to further subdivide isolation within the scope."""

    def __str__(self) -> str:
        """
        Convert to string representation, format is "{scope}" or "{scope}:{subject}".

        Returns:
            str: Serializable string, used as a component of storage keys.
        """
        if self.subject:
            return f"{self.scope}:{self.subject}"
        return str(self.scope)

    @classmethod
    def from_string(cls, key_str: str) -> 'SessionScope':
        """
        Parse SessionScope from a string.

        Parsing rules:
        - If the string does not contain ':', the entire string is treated as the scope.
        - If it contains ':', the first part is the scope, and the rest is the subject.

        Args:
            key_str (str): E.g., "main:direct:user123" or "main".

        Returns:
            SessionScope: Parsed instance.

        Raises:
            ValueError: Raised when the scope or subject cannot be recognized.
        """
        parts = key_str.split(":", 1)
        scope_str = parts[0]
        subject_str = parts[1] if len(parts) > 1 else None

        # Scope parsing
        if scope_str == "main":
            scope = MainScope()
        else:
            # Custom scope parsing logic can be added here
            raise ValueError(f"Unknown scope: {scope_str}")

        # Subject parsing
        subject = None
        if subject_str:
            if subject_str.startswith("direct:"):
                subject = DirectSubject.from_string(subject_str)
            elif subject_str.startswith("group:") and ":user:" in subject_str:
                subject = GroupUserSubject.from_string(subject_str)
            elif subject_str.startswith("group:"):
                subject = GroupSubject.from_string(subject_str)
            else:
                raise ValueError(f"Unknown subject format: {subject_str}")

        return cls(scope, subject)


class SessionScopeKey:
    """
    Session key, globally uniquely identifying the session collection for a specific
    scope and subject under an Agent.

    Format: agent:{agent_id}:{SessionScope}

    This key is used to index and manage sessions at the Agent level, e.g., as
    top-level keys in sessions.json.
    """

    def __init__(self, agent_id: str, session_scope: SessionScope):
        """
        Args:
            agent_id (str): The Agent's unique identifier.
            session_scope (SessionScope): Session scope object.
        """
        self.agent_id = agent_id
        self.session_scope = session_scope

    def __str__(self) -> str:
        """
        Convert to full string representation.

        Returns:
            str: Format is "agent:{agent_id}:{session_scope}"
        """
        return f"agent:{self.agent_id}:{self.session_scope}"

    @classmethod
    def from_string(cls, key_str: str) -> 'SessionScopeKey':
        """
        Parse session key from a string.

        Args:
            key_str (str): Must start with "agent:", followed by agent_id and SessionScope string.

        Returns:
            SessionScopeKey: Parsed instance.

        Raises:
            ValueError: Raised when the format does not meet requirements.
        """
        if not key_str.startswith("agent:"):
            raise ValueError("SessionScopeKey must start with 'agent:'")
        rest = key_str[6:]
        parts = rest.split(":", 1)
        if len(parts) < 1:
            raise ValueError("SessionScopeKey missing agent_id")
        agent_id = parts[0]
        session_scope_str = parts[1] if len(parts) > 1 else ""
        session_scope = SessionScope.from_string(session_scope_str)
        return cls(agent_id, session_scope)

    def __hash__(self):
        return hash(str(self))

    def __eq__(self, other):
        if not isinstance(other, SessionScopeKey):
            return False
        return str(self) == str(other)
