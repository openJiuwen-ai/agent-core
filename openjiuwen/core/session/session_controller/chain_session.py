# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, Optional, TypeVar

from openjiuwen.core.common.logging import session_logger as logger
from openjiuwen.core.session.session_controller.data_container import (
    DataContainer,
    SharingPolicy,
    Permission,
    DEFAULT_DATA_CONTAINER_TYPE,
)
from openjiuwen.core.session.session_controller.schema import SessionMeta
from openjiuwen.core.session.session_controller.scope import (
    SessionScope,
    SessionScopeKey,
)
from openjiuwen.core.session.session_controller.utils import SessionPaths

T = TypeVar('T')


class ChainSession(Generic[T]):
    """
    Chained session data container.

    Each ChainSession instance represents a specific conversation session with its own
    storage directory, and maintains downstream call relationships with other sessions
    to implement unidirectional data visibility.

    Type parameter T is the data type stored in the DataContainer.
    """

    def __init__(
            self,
            agent_id: str,
            session_scope: SessionScope,
            session_id: str,
            data_container: DataContainer,
            session_dir: Path
    ):
        """
        Initialize the session object (typically called by SessionController).

        Args:
            agent_id (str): The owning Agent identifier.
            session_scope (SessionScope): The session scope.
            session_id (str): Globally unique session ID (UUID).
            data_container (DataContainer[T]): Initialized data container instance.
            session_dir (Path): Dedicated storage directory path for this session.
        """
        self.agent_id = agent_id
        self.session_scope = session_scope
        self.session_id = session_id
        self.data_container = data_container
        self._session_dir = session_dir
        self._data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE

        # Downstream relationship mapping: key is (target_agent, target_session), value is sharing policy
        self._downstream_policies: dict[tuple[str, str], SharingPolicy] = {}

        # Metadata
        self._created_at: float = 0.0
        self._updated_at: float = 0.0
        self._version: int = 1
        self._is_active: bool = False

        # Lock for protecting concurrent access
        self._lock = asyncio.Lock()

    @property
    def session_key(self) -> SessionScopeKey:
        """Get the globally unique key for this session."""
        return SessionScopeKey(self.agent_id, self.session_scope)

    @property
    def created_at(self) -> float:
        """Get the creation timestamp."""
        return self._created_at

    @property
    def updated_at(self) -> float:
        """Get the update timestamp."""
        return self._updated_at

    @property
    def version(self) -> int:
        """Get the version number."""
        return self._version

    @property
    def is_active(self) -> bool:
        """Get the active status."""
        return self._is_active

    @is_active.setter
    def is_active(self, value: bool) -> None:
        """Set the active status."""
        self._is_active = value
        if value:
            self._updated_at = datetime.now(timezone.utc).timestamp()

    # ==================== Persistence ====================
    async def load(self) -> bool:
        """
        Load session data and downstream relationships from disk.

        Loading logic:
        1. Read {session_dir}/state.data and call data_container.load() to restore data.
        2. Scan {session_dir}/downstreams/*.link files to rebuild _downstream_policies.
        3. Consistency check: if a .link file contains a "removed": true marker,
           skip restoring that downstream relationship and delete the .link file
           (completing cleanup that was interrupted by a crash).

        Returns:
            bool: True if loading succeeded, False on any IO or parsing error.
        """
        async with self._lock:
            try:
                logger.debug(
                    f"Loading session {self.session_id} from {self._session_dir}"
                )
                # Load session data
                state_file = SessionPaths.state_file(self._session_dir)
                if state_file.exists():
                    with open(state_file, 'r', encoding='utf-8') as f:
                        state_data = json.load(f)

                    # Load metadata
                    if 'meta' in state_data:
                        meta = state_data['meta']
                        self._created_at = meta.get('created_at', 0.0)
                        self._updated_at = meta.get('updated_at', 0.0)
                        self._version = meta.get('version', 1)
                        self._is_active = meta.get('is_active', False)
                        self._data_container_type = meta.get('data_container_type', 'agent')

                    # Load data container
                    if 'data' in state_data:
                        self.data_container = await self.data_container.load(self.agent_id, self.session_id,
                                                                             state_data['data']
                                                                             )

                # Load downstream relationships
                downstreams_dir = SessionPaths.downstreams_dir(self._session_dir)
                if downstreams_dir.exists():
                    for link_file in downstreams_dir.glob("*.link"):
                        try:
                            with open(link_file, 'r', encoding='utf-8') as f:
                                link_data = json.load(f)

                            # Check if marked as removed
                            if link_data.get('removed', False):
                                # Delete files marked as removed
                                link_file.unlink()
                                continue

                            # Parse filename to get target info
                            # Filename format: {target_agent}_{target_session}.link
                            filename = link_file.stem
                            if '_' in filename:
                                target_agent, target_session = filename.split('_', 1)

                                # Parse sharing policy
                                policy_data = link_data.get('permission', {})
                                permission_level = policy_data.get('level', 1)
                                field_scopes = policy_data.get('field_scopes')

                                policy = SharingPolicy(
                                    permission=Permission(permission_level),
                                    field_scopes=set(field_scopes) if field_scopes else None
                                )

                                self._downstream_policies[(target_agent, target_session)] = policy
                                logger.debug(
                                    f"Loaded downstream link: {self.session_id} -> {target_agent}/{target_session}"
                                )
                        except (ValueError, KeyError) as e:
                            logger.error(
                                f"Error loading downstream link {link_file}: {e}"
                            )
                            continue

                logger.info(
                    f"Session {self.session_id} loaded successfully, "
                    f"downstreams={len(self._downstream_policies)}, active={self._is_active}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error loading session {self.session_id}"
                )
                return False

    async def flush(self) -> bool:
        """
        Persist current session state to disk.

        Write operations:
        1. Call data_container.dump() and write the result to state.data.
        2. Create/update a .link file for each downstream relationship.
        3. Clean up .link files for removed downstream relationships:
           a. First mark the .link file to be deleted with "removed": true and write to disk;
           b. Then delete the .link file.

        Returns:
            bool: True if flush succeeded.
        """
        async with self._lock:
            try:
                logger.debug(
                    f"Flushing session {self.session_id} to disk"
                )
                # Update metadata timestamp
                self._updated_at = datetime.now(timezone.utc).timestamp()

                # Prepare data
                state_data = {
                    'meta': {
                        'created_at': self._created_at,
                        'updated_at': self._updated_at,
                        'version': self._version,
                        'is_active': self._is_active,
                        'data_container_type': self._data_container_type
                    },
                    'data': await self.data_container.dump() if hasattr(self.data_container, 'dump') else {}
                }

                # Write session data
                state_file = SessionPaths.state_file(self._session_dir)
                self._session_dir.mkdir(parents=True, exist_ok=True)
                with open(state_file, 'w', encoding='utf-8') as f:
                    json.dump(state_data, f, indent=2, ensure_ascii=False)

                # Process downstream relationships
                downstreams_dir = SessionPaths.downstreams_dir(self._session_dir)
                downstreams_dir.mkdir(exist_ok=True)

                # Get all existing link files
                existing_links = set(downstreams_dir.glob("*.link"))
                current_links = set()

                # Write current downstream relationships
                for (target_agent, target_session), policy in self._downstream_policies.items():
                    link_filename = f"{target_agent}_{target_session}.link"
                    link_file = downstreams_dir / link_filename
                    current_links.add(link_file)

                    link_data = {
                        'permission': {
                            'level': policy.permission.value,
                            'field_scopes': (
                                list(policy.field_scopes)
                                if policy.field_scopes else None
                            )
                        },
                        'created_at': self._updated_at
                    }

                    with open(link_file, 'w', encoding='utf-8') as f:
                        json.dump(link_data, f, indent=2, ensure_ascii=False)

                # Clean up deleted downstream relationships
                for link_file in existing_links - current_links:
                    try:
                        with open(link_file, 'r', encoding='utf-8') as f:
                            link_data = json.load(f)
                        link_data['removed'] = True
                        with open(link_file, 'w', encoding='utf-8') as f:
                            json.dump(link_data, f, indent=2, ensure_ascii=False)
                    except (json.JSONDecodeError, OSError):
                        pass

                    try:
                        link_file.unlink()
                    except OSError:
                        pass

                logger.info(
                    f"Session {self.session_id} flushed successfully, "
                    f"version={self._version}, downstreams={len(self._downstream_policies)}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error flushing session {self.session_id}"
                )
                return False

    # ==================== Downstream Relationship Management ====================
    def add_downstream(
            self,
            target_agent: str,
            target_session: str,
            policy: SharingPolicy = SharingPolicy()
    ) -> None:
        """
        Add a downstream session (callee), indicating the current session can
        unidirectionally read the target session's data.

        Args:
            target_agent (str): Target Agent ID.
            target_session (str): Target session ID.
            policy (SharingPolicy): Sharing policy, defaults to read-only for all fields.
        """
        self._downstream_policies[(target_agent, target_session)] = policy
        self._updated_at = datetime.now(timezone.utc).timestamp()
        logger.debug(
            f"Added downstream: {self.session_id} -> {target_agent}/{target_session}, policy={policy.permission}"
        )

    def remove_downstream(self, target_agent: str, target_session: str) -> None:
        """
        Remove the specified downstream relationship, and delete the corresponding
        .link file on flush.

        Args:
            target_agent (str): Target Agent ID.
            target_session (str): Target session ID.
        """
        if (target_agent, target_session) in self._downstream_policies:
            del self._downstream_policies[(target_agent, target_session)]
            self._updated_at = datetime.now(timezone.utc).timestamp()
            logger.debug(
                f"Removed downstream: {self.session_id} -> {target_agent}/{target_session}"
            )

    def has_downstream(self, target_agent: str, target_session: str) -> bool:
        """
        Check if the specified downstream relationship exists.

        Args:
            target_agent (str): Target Agent ID.
            target_session (str): Target session ID.

        Returns:
            bool: True if it exists.
        """
        return (target_agent, target_session) in self._downstream_policies

    def get_downstreams(self) -> dict[tuple[str, str], SharingPolicy]:
        """
        Get a copy of all downstream relationships and their sharing policies.

        Returns:
            Dict[Tuple[str, str], SharingPolicy]: Downstream relationship mapping.
        """
        return self._downstream_policies.copy()

    def get_downstream_policy(self, target_agent: str, target_session: str) -> Optional[SharingPolicy]:
        """
        Get the sharing policy for a specific downstream session.

        Args:
            target_agent (str): Target Agent ID.
            target_session (str): Target session ID.

        Returns:
            Optional[SharingPolicy]: The policy object if it exists, otherwise None.
        """
        return self._downstream_policies.get((target_agent, target_session))

    def remove_all_downstreams(self) -> None:
        """Clear all downstream relationships; the corresponding .link files will be deleted on next flush."""
        self._downstream_policies.clear()
        self._updated_at = datetime.now(timezone.utc).timestamp()
        logger.debug(
            f"Cleared all downstreams for session {self.session_id}"
        )

    def get_data(self) -> T:
        """
        Get the complete data of this session.

        Returns:
            T: The current data container's data object.
        """
        return self.data_container.get()

    async def update_data(self, data: dict) -> bool:
        """
        Atomically modify self data via an update function.

        Args:
            updater (Callable[[T], T]): Data update function.

        Returns:
            bool: True if update succeeded.
        """
        async with self._lock:
            try:
                success = self.data_container.update(data)
                if success:
                    self._version += 1
                    self._updated_at = datetime.now(timezone.utc).timestamp()
                return success
            except Exception as e:
                logger.exception(
                    f"Error updating session data {self.session_id}"
                )
                return False

    def can_see(self, target_agent: str, target_session: str) -> bool:
        """
        Check whether the current session has read permission for the target session.

        Visibility rules:
        1. Always returns True when the target is self.
        2. Returns True when the target is an added downstream session.
        3. Returns False in all other cases.

        Args:
            target_agent (str): Target Agent ID.
            target_session (str): Target session ID.

        Returns:
            bool: True if visible.
        """
        if target_agent == self.agent_id and target_session == self.session_id:
            return True
        return self.has_downstream(target_agent, target_session)

    # ==================== Metadata Operations ====================
    def to_session_meta(self) -> SessionMeta:
        """
        Convert to session metadata object.

        Returns:
            SessionMeta: Session metadata
        """
        return SessionMeta(
            session_id=self.session_id,
            created_at=self._created_at,
            updated_at=self._updated_at,
            version=self._version,
            is_active=self._is_active,
            data_container_type=self._data_container_type
        )

    def update_from_meta(self, meta: SessionMeta) -> None:
        """
        Update session information from metadata.

        Args:
            meta (SessionMeta): Session metadata
        """
        self._created_at = meta.created_at
        self._updated_at = meta.updated_at
        self._version = meta.version
        self._is_active = meta.is_active
        if hasattr(meta, 'data_container_type') and meta.data_container_type:
            self._data_container_type = meta.data_container_type
