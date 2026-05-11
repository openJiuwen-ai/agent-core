# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Union

DEFAULT_DATA_CONTAINER_TYPE = "agent"


class DataContainer(ABC):
    """
    Generic data container abstract base class.

    Used to encapsulate the core business data of a session, providing unified access,
    update, and serialization interfaces. Concrete implementation classes can decide
    the internal storage structure (e.g., dict, Pydantic model, etc.).
    """

    @abstractmethod
    def get(self, key: Union[str, list, dict] = None) -> dict:
        """
        Get a read-only view or copy of the current data object.

        Args:
            key: Optional schema to filter data. If None, returns all data.

        Returns:
            dict: The stored data object.
        """
        pass

    @abstractmethod
    def update(self, data: dict) -> bool:
        """
        Atomically update data with the given dictionary.

        This method should guarantee thread/coroutine safety (depending on implementation),
        and may involve locking or version checking before and after updates.

        Args:
            data (dict): A dictionary of key-value pairs to update.

        Returns:
            bool: True if the update succeeded, False if it failed (e.g., due to version conflict).
        """
        pass

    @abstractmethod
    async def dump(self) -> Any:
        """
        Asynchronously serialize the data object to a persistable format (e.g., dict, JSON string).

        Returns:
            Any: The serialized data representation.
        """
        pass

    @classmethod
    @abstractmethod
    async def load(cls, agent_id: str, session_id: str, serialized: Any) -> 'DataContainer':
        """
        Asynchronously reconstruct a DataContainer instance from serialized data.

        Args:
            agent_id (str): The agent identifier.
            session_id (str): The session identifier.
            serialized (Any): Data produced by dump().

        Returns:
            DataContainer[T]: A new container instance containing the deserialized data.
        """
        pass


class Permission(Enum):
    """
    Data access permission level enum.

    Currently only supports read-only permission; WRITE, ADMIN, etc. may be added in the future.
    """
    READ = 1
    """Read-only permission, allows reading specified fields."""


@dataclass
class SharingPolicy:
    """
    Downstream session sharing policy.

    Defines the permission level and field scope that a caller can access
    from the callee's data.
    """

    permission: Permission = Permission.READ
    """The granted permission level; currently only READ is supported."""

    field_scopes: Optional[set[str]] = None
    """
    Set of field names allowed to be accessed.

    - If None, all contents of the data container are accessible.
    - If a set, only the specified fields are accessible.
      (Field-level filtering is implemented in the data container or session layer)
    """


class DataContainerFactory:
    """Factory for creating DataContainer instances by type name."""

    _registry: dict[str, type[DataContainer]] = {}

    @classmethod
    def register(
            cls,
            data_container_type: str,
            container_cls: type[DataContainer] = None
    ):
        """Register a DataContainer class for a given type name.

        Can be used as a decorator or called directly.

        Args:
            data_container_type: The type name to register.
            container_cls: Optional DataContainer class. If None, returns a decorator.
        """
        if container_cls is not None:
            cls._registry[data_container_type] = container_cls
            return container_cls

        def wrapper(container_cls_fn: type[DataContainer]):
            cls._registry[data_container_type] = container_cls_fn
            return container_cls_fn

        return wrapper

    @classmethod
    def create(
            cls,
            data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE,
            **kwargs
    ) -> DataContainer:
        """Create a DataContainer instance by type name.

        Args:
            data_container_type: The registered type name.
            **kwargs: Additional keyword arguments passed to the container constructor.

        Returns:
            A new DataContainer instance.

        Raises:
            ValueError: If the type name is not registered.
        """
        if data_container_type not in cls._registry:
            raise ValueError(
                f"Unknown data_container_type: '{data_container_type}'. "
                f"Available types: {list(cls._registry.keys())}"
            )
        return cls._registry[data_container_type](**kwargs)

    @classmethod
    async def load(
            cls,
            data_container_type: str,
            agent_id: str,
            session_id: str,
            serialized: Any = None,
            **kwargs
    ) -> DataContainer:
        """Reconstruct a DataContainer instance from serialized data.

        Args:
            data_container_type: The registered type name.
            agent_id: The agent identifier.
            session_id: The session identifier.
            serialized: Data produced by dump().
            **kwargs: Additional keyword arguments passed to the container's load method.

        Returns:
            A new DataContainer instance containing the deserialized data.

        Raises:
            ValueError: If the type name is not registered.
        """
        if data_container_type not in cls._registry:
            raise ValueError(
                f"Unknown data_container_type: '{data_container_type}'. "
                f"Available types: {list(cls._registry.keys())}"
            )
        return await cls._registry[data_container_type].load(
            agent_id, session_id, serialized, **kwargs
        )

    @classmethod
    def has(cls, data_container_type: str) -> bool:
        """Check if a data container type is registered."""
        return data_container_type in cls._registry

    @classmethod
    def list_types(cls) -> list:
        """List all registered data container type names."""
        return list(cls._registry.keys())


class AgentSessionContainer(DataContainer):
    """Session-backed data container that delegates to an agent session."""

    def __init__(self, session=None):
        self.session = session

    @classmethod
    async def load(cls, agent_id: str, session_id: str, serialized: Any) -> 'DataContainer':
        """Asynchronously reconstruct an AgentSessionContainer by creating and initializing an agent session."""
        from openjiuwen.core.single_agent import create_agent_session
        from openjiuwen.core.single_agent import AgentCard
        agent_session = create_agent_session(session_id=session_id, card=AgentCard(id=agent_id))
        await agent_session.pre_run()
        return AgentSessionContainer(agent_session)

    async def dump(self) -> Any:
        """Asynchronously serialize the session container, returning self."""
        return {}

    def update(self, data: dict) -> bool:
        """Update data by delegating to the session's update_state."""
        if not self.session:
            return False
        self.session.update_state(data)
        return True

    def get(self, key: Union[str, list, dict] = None) -> dict:
        """Get data by delegating to the session's get_state."""
        if not self.session:
            return None
        if key is None:
            return self.session.dump_state()
        return self.session.get_state(key)


DataContainerFactory.register(DEFAULT_DATA_CONTAINER_TYPE, AgentSessionContainer)
