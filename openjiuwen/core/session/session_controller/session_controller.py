# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional

from openjiuwen.core.common.logging import session_logger as logger
from openjiuwen.core.session.session_controller.chain_session import ChainSession
from openjiuwen.core.session.session_controller.data_container import (
    DataContainerFactory,
    DEFAULT_DATA_CONTAINER_TYPE,
)
from openjiuwen.core.session.session_controller.schema import SessionMeta, ScopeSessionsMeta
from openjiuwen.core.session.session_controller.scope import (
    SessionScope,
    SessionScopeKey,
)
from openjiuwen.core.session.session_controller.utils import SessionPaths


class SessionController:
    """
    Session controller for a single Agent.

    Responsible for managing the lifecycle of all sessions under this Agent,
    including creation, querying, activation, deletion, as well as maintaining
    the sessions.json metadata file and session object cache.
    """

    def __init__(
            self,
            agent_id: str,
            base_path: Path,
            data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE
    ):
        """
        Initialize the controller.

        Args:
            agent_id (str): The current Agent's unique identifier.
            base_path (Path): Storage root directory (e.g., Path("agents")).
                              Actual session directory is base_path / agent_id / "sessions".
            data_container_type (str): Type of data container to use. Defaults to DEFAULT_DATA_CONTAINER_TYPE.
        """
        self.agent_id = agent_id
        self._root_path = base_path
        self.base_path = SessionPaths.sessions_dir(base_path, agent_id)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._data_container_type = data_container_type

        # Session object cache: session_id -> ChainSession
        self.session_cache: dict[str, ChainSession] = {}

        # Metadata mapping: SessionScope -> ScopeSessionsMeta
        self.meta_map: dict[SessionScope, ScopeSessionsMeta] = {}

        # Lock for protecting concurrent access
        self._lock = asyncio.Lock()

    # ==================== Persistence ====================
    async def flush(self) -> bool:
        """
        Persist all changes to disk.

        Includes:
        - Flushing all modified session data in cache (calling ChainSession.flush()).
        - Writing current meta_map to sessions.json.

        Returns:
            bool: True if all succeeded, False if any failed.
        """
        async with self._lock:
            try:
                logger.debug(
                    f"Flushing all sessions for agent {self.agent_id}, "
                    f"cache_size={len(self.session_cache)}"
                )
                # Flush all cached sessions
                flush_tasks = []
                for session in self.session_cache.values():
                    flush_tasks.append(session.flush())

                # Wait for all flushes to complete
                results = await asyncio.gather(*flush_tasks, return_exceptions=True)

                # Check if any flush failed
                for result in results:
                    if isinstance(result, Exception) or result is False:
                        logger.error(
                            f"Error flushing session for agent {self.agent_id}: {result}"
                        )
                        return False

                # Write metadata file
                await self._write_meta_file()

                logger.info(
                    f"Flushed all sessions for agent {self.agent_id}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error flushing controller for agent {self.agent_id}"
                )
                return False

    async def flush_session(self, session_id: str) -> bool:
        """
        Persist changes for the specified session to disk.

        Flushes the session data and writes the metadata file.

        Args:
            session_id (str): The session ID to flush.

        Returns:
            bool: True if succeeded, False if failed.
        """
        async with self._lock:
            try:
                if session_id not in self.session_cache:
                    return True

                result = await self.session_cache[session_id].flush()
                if isinstance(result, Exception) or result is False:
                    logger.error(
                        f"Error flushing session {session_id}: {result}"
                    )
                    return False

                await self._write_meta_file()
                logger.debug(
                    f"Flushed session {session_id} for agent {self.agent_id}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error flushing session {session_id} for agent {self.agent_id}"
                )
                return False

    async def flush_scope(self, session_scope: SessionScope) -> bool:
        """
        Persist changes for the specified session scope to disk.

        Only flushes sessions belonging to the given session_scope.

        Args:
            session_scope (SessionScope): The session scope to flush.

        Returns:
            bool: True if all succeeded, False if any failed.
        """
        async with self._lock:
            try:
                if session_scope not in self.meta_map:
                    return True

                flush_tasks = []
                for session_id, session in self.session_cache.items():
                    if session.session_scope == session_scope:
                        flush_tasks.append(session.flush())

                if flush_tasks:
                    results = await asyncio.gather(*flush_tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, Exception) or result is False:
                            logger.error(
                                f"Error flushing session in scope {session_scope}: {result}"
                            )
                            return False

                await self._write_meta_file()
                logger.debug(
                    f"Flushed scope {session_scope} for agent {self.agent_id}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error flushing scope for agent {self.agent_id}"
                )
                return False

    async def load(self, load_active_only: bool = True) -> bool:
        """
        Load this Agent's session metadata from disk.

        Args:
            load_active_only (bool, optional): Whether to load only active sessions. Defaults to True.
                - True: Load only active sessions for better performance
                - False: Load all sessions (including historical sessions)

        Returns:
            bool: True if loading succeeded.
        """
        async with self._lock:
            try:
                logger.debug(
                    f"Loading sessions for agent {self.agent_id}, "
                    f"load_active_only={load_active_only}"
                )
                # Read metadata file
                meta_file = SessionPaths.meta_file(self._root_path, self.agent_id)
                if not meta_file.exists():
                    return True

                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)

                # Clear current metadata
                self.meta_map.clear()

                # Parse metadata
                for scope_key_str, scope_meta_data in meta_data.items():
                    try:
                        # Parse SessionScopeKey
                        session_scope_key = SessionScopeKey.from_string(scope_key_str)
                        session_scope = session_scope_key.session_scope

                        # Create ScopeSessionsMeta
                        scope_meta = ScopeSessionsMeta.from_dict(scope_meta_data)

                        # Add to mapping
                        self.meta_map[session_scope] = scope_meta

                        # Load session data
                        if load_active_only:
                            # Load only active sessions
                            if scope_meta.active_session:
                                await self._load_session(session_scope, scope_meta.active_session)
                        else:
                            # Load all sessions
                            for session_meta in scope_meta.sessions:
                                await self._load_session(session_scope, session_meta.session_id)
                    except Exception as e:
                        logger.error(
                            f"Error loading scope {scope_key_str}: {e}"
                        )
                        continue

                logger.info(
                    f"Loaded sessions for agent {self.agent_id}, "
                    f"scopes={len(self.meta_map)}, cache_size={len(self.session_cache)}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error loading controller for agent {self.agent_id}"
                )
                return False

    async def load_scope(self, session_scope: SessionScope, load_active_only: bool = True) -> bool:
        """
        Load session data for the specified session scope from disk.

        Only loads sessions belonging to the given session_scope, leaving other
        scopes untouched.

        Args:
            session_scope (SessionScope): The session scope to load.
            load_active_only (bool, optional): Whether to load only active sessions. Defaults to True.

        Returns:
            bool: True if loading succeeded.
        """
        async with self._lock:
            try:
                meta_file = SessionPaths.meta_file(self._root_path, self.agent_id)
                if not meta_file.exists():
                    return True

                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)

                for scope_key_str, scope_meta_data in meta_data.items():
                    try:
                        session_scope_key = SessionScopeKey.from_string(scope_key_str)
                        if session_scope_key.session_scope != session_scope:
                            continue

                        scope_meta = ScopeSessionsMeta.from_dict(scope_meta_data)
                        self.meta_map[session_scope] = scope_meta

                        if load_active_only:
                            if scope_meta.active_session:
                                await self._load_session(session_scope, scope_meta.active_session)
                        else:
                            for session_meta in scope_meta.sessions:
                                await self._load_session(session_scope, session_meta.session_id)
                        break
                    except Exception as e:
                        logger.error(
                            f"Error loading scope {scope_key_str}: {e}"
                        )
                        continue

                logger.debug(
                    f"Loaded scope {session_scope} for agent {self.agent_id}"
                )
                return True
            except Exception as e:
                logger.exception(
                    f"Error loading scope for agent {self.agent_id}"
                )
                return False

    async def _load_session(self, session_scope: SessionScope, session_id: str, **params) -> bool:
        """
        Load the specified session.

        Args:
            session_scope (SessionScope): Session scope
            session_id (str): Session ID

        Returns:
            bool: True if loading succeeded
        """
        try:
            # Check if already loaded
            if session_id in self.session_cache:
                return True

            # Look up data_container_type from SessionMeta
            data_container_type = self._data_container_type
            if session_scope in self.meta_map:
                session_meta = self.meta_map[session_scope].get_session(session_id)
                if session_meta and session_meta.data_container_type:
                    data_container_type = session_meta.data_container_type

            session_dir = SessionPaths.session_dir(self._root_path, self.agent_id, session_id)

            if not params:
                data_container = await DataContainerFactory.load(data_container_type, agent_id=self.agent_id,
                                                                 session_id=session_id)
            else:
                data_container = DataContainerFactory.create(data_container_type, **params)

            # Create session object
            session = ChainSession(
                agent_id=self.agent_id,
                session_scope=session_scope,
                session_id=session_id,
                data_container=data_container,
                session_dir=session_dir
            )

            # Add to cache
            self.session_cache[session_id] = session

            logger.debug(
                f"Loaded session {session_id} for agent {self.agent_id}, "
                f"container_type={data_container_type}"
            )
            return True
        except Exception as e:
            logger.exception(
                f"Error loading session {session_id}"
            )
            raise e

    async def _write_meta_file(self) -> bool:
        """
        Write the metadata file.

        Returns:
            bool: True if write succeeded
        """
        try:
            meta_data = {}
            for session_scope, scope_meta in self.meta_map.items():
                session_scope_key = SessionScopeKey(self.agent_id, session_scope)
                meta_data[str(session_scope_key)] = scope_meta.to_dict()

            meta_file = SessionPaths.meta_file(self._root_path, self.agent_id)
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta_data, f, indent=2, ensure_ascii=False)

            return True
        except Exception as e:
            logger.exception(
                f"Error writing meta file for agent {self.agent_id}"
            )
            return False

    # ==================== Session Management ====================
    async def create_if_not_exists(
            self,
            session_scope: SessionScope,
            session_id: str,
            **params
    ) -> tuple[bool, ChainSession]:
        """
        Get or create a session within the specified session scope.

        Behavior:
        - If an active session exists for this SessionScope, return it directly (is_new=False).
        - If no active session exists, create a new session object, initialize the storage
          directory, update metadata, and mark it as active (is_new=True).

        Args:
            session_scope (SessionScope): Session scope.
            session_id (str): The new session's ID (if creating). Must be globally unique.

        Returns:
            Tuple[bool, ChainSession]: (whether newly created, session object)

        Raises:
            ValueError: If session_id already exists in another session.
        """
        async with self._lock:
            try:
                # Check if session ID already exists
                if session_id in self.session_cache:
                    raise ValueError(f"Session ID {session_id} already exists")

                # Also check meta_map for sessions not yet loaded into cache
                for existing_scope, existing_scope_meta in self.meta_map.items():
                    if existing_scope_meta.get_session(session_id) is not None:
                        raise ValueError(f"Session ID {session_id} already exists")

                # Get or create session scope metadata
                if session_scope not in self.meta_map:
                    session_scope_key = SessionScopeKey(self.agent_id, session_scope)
                    scope_meta = ScopeSessionsMeta(
                        session_scope_key=str(session_scope_key),
                        active_session=None,
                        sessions=[]
                    )
                    self.meta_map[session_scope] = scope_meta
                else:
                    scope_meta = self.meta_map[session_scope]

                # Check if there is already an active session
                active_session = scope_meta.get_active_session()
                if active_session:
                    # Return the existing active session
                    if active_session.session_id not in self.session_cache:
                        await self._load_session(session_scope, active_session.session_id)
                    return False, self.session_cache[active_session.session_id]

                # Create new session
                session_dir = SessionPaths.session_dir(
                    self._root_path, self.agent_id, session_id
                )
                session_dir.mkdir(parents=True, exist_ok=True)

                # Create data container
                data_container = DataContainerFactory.create(self._data_container_type, **params)
                container_type = self._data_container_type

                # Create session object
                session = ChainSession(
                    agent_id=self.agent_id,
                    session_scope=session_scope,
                    session_id=session_id,
                    data_container=data_container,
                    session_dir=session_dir
                )

                # Create session metadata
                session_meta = SessionMeta.create_new(
                    session_id, data_container_type=container_type
                )
                session.update_from_meta(session_meta)

                # Add to metadata
                scope_meta.add_session(session_meta)

                # Add to cache
                self.session_cache[session_id] = session

                # Save to disk
                await session.flush()
                await self._write_meta_file()

                logger.info(
                    f"Created new session {session_id} for agent {self.agent_id}, "
                    f"scope={session_scope}, container_type={container_type}"
                )
                return True, session
            except Exception as e:
                logger.exception(
                    f"Error creating session {session_id} for agent {self.agent_id}"
                )
                raise

    async def get_scope_active_session(self, session_scope: SessionScope) -> Optional[ChainSession]:
        """
        Get the currently active session for the specified session scope.

        Args:
            session_scope (SessionScope): Session scope.

        Returns:
            Optional[ChainSession]: The active session if it exists, otherwise None.
        """
        async with self._lock:
            if session_scope not in self.meta_map:
                return None

            scope_meta = self.meta_map[session_scope]
            active_session_id = scope_meta.active_session

            if not active_session_id:
                return None

            # Ensure session is loaded
            if active_session_id not in self.session_cache:
                if not await self._load_session(session_scope, active_session_id):
                    return None

            return self.session_cache.get(active_session_id)

    async def get_scope_sessions(
            self,
            session_scope: SessionScope
    ) -> list[ChainSession]:
        """
        Get all historical sessions under the specified session scope (sorted by update time descending).

        Note: Only returns session objects already loaded in cache; unloaded historical
        sessions are not automatically loaded.

        Args:
            session_scope (SessionScope): Session scope.

        Returns:
            List[ChainSession]: List of session objects.
        """
        async with self._lock:
            if session_scope not in self.meta_map:
                return []

            logger.debug(
                f"Getting sessions for agent {self.agent_id}, "
                f"scope={session_scope}"
            )

            scope_meta = self.meta_map[session_scope]
            sessions = []

            for session_meta in scope_meta.sessions:
                if session_meta.session_id in self.session_cache:
                    sessions.append(self.session_cache[session_meta.session_id])

            return sessions

    async def activate_session(self, session_id: str) -> None:
        """
        Set the specified session as active.

        If the SessionScope of this session already has another active session,
        it will be replaced.

        Args:
            session_id (str): The session ID to activate.

        Raises:
            KeyError: If session_id does not exist.
        """
        async with self._lock:
            # Find the session
            session = None
            target_session_scope = None

            for cached_session in self.session_cache.values():
                if cached_session.session_id == session_id:
                    session = cached_session
                    target_session_scope = cached_session.session_scope
                    break

            if not session:
                # Try to find from metadata
                for session_scope, scope_meta in self.meta_map.items():
                    for session_meta in scope_meta.sessions:
                        if session_meta.session_id == session_id:
                            target_session_scope = session_scope
                            # Load the session
                            await self._load_session(session_scope, session_id)
                            session = self.session_cache.get(session_id)
                            break
                    if session:
                        break

            if not session:
                raise KeyError(f"Session {session_id} not found")

            logger.debug(
                f"Activating session {session_id} for agent {self.agent_id}"
            )

            # Activate the session
            if target_session_scope in self.meta_map:
                scope_meta = self.meta_map[target_session_scope]
                if scope_meta.activate_session(session_id):
                    session.is_active = True
                    await session.flush()
                    await self._write_meta_file()
                    logger.info(
                        f"Session {session_id} activated for agent {self.agent_id}"
                    )

    async def get_scope_meta(self, session_scope: SessionScope) -> ScopeSessionsMeta:
        """
        Get the metadata for the specified session scope.

        Args:
            session_scope (SessionScope): Session scope.

        Returns:
            ScopeSessionsMeta: The corresponding metadata object.
        """
        async with self._lock:
            if session_scope not in self.meta_map:
                session_scope_key = SessionScopeKey(self.agent_id, session_scope)
                return ScopeSessionsMeta(
                    session_scope_key=str(session_scope_key),
                    active_session=None,
                    sessions=[]
                )
            return self.meta_map[session_scope]

    def list_metas(self) -> dict[SessionScope, ScopeSessionsMeta]:
        """
        Get the metadata mapping for all known session scopes.

        Returns:
            Dict[SessionScope, ScopeSessionsMeta]: A copy of the metadata dictionary.
        """
        return self.meta_map.copy()

    async def cleanup_scope_inactive_sessions(
            self,
            session_scope: SessionScope
    ) -> list[tuple[SessionScope, list[SessionMeta]]]:
        """
        Clean up inactive session data for the specified session scope.

        Cleanup logic:
        1. Preserve the active session for the specified session scope
        2. Delete disk data for other inactive sessions under this scope
        3. Update metadata to remove cleaned session records

        Args:
            session_scope (SessionScope): The session scope to clean up.

        Returns:
            List[Tuple[SessionScope, List[SessionMeta]]]: List of cleaned session metadata,
                each element contains the session scope and the list of cleaned session metadata

        Raises:
            ValueError: If the specified session_scope does not exist
        """
        async with self._lock:
            if session_scope not in self.meta_map:
                raise ValueError(f"Session scope {session_scope} not found")

            logger.debug(
                f"Cleaning up inactive sessions for agent {self.agent_id}, "
                f"scope={session_scope}"
            )

            scope_meta = self.meta_map[session_scope]
            cleaned_sessions = []

            # Collect inactive sessions
            inactive_sessions = []
            for session_meta in scope_meta.sessions:
                if not session_meta.is_active:
                    inactive_sessions.append(session_meta)

            # Clean up inactive sessions
            for session_meta in inactive_sessions:
                # Remove from cache
                if session_meta.session_id in self.session_cache:
                    del self.session_cache[session_meta.session_id]

                # Delete disk data
                session_dir = SessionPaths.session_dir(
                    self._root_path, self.agent_id, session_meta.session_id
                )
                if session_dir.exists():
                    shutil.rmtree(session_dir, ignore_errors=True)

                # Remove from metadata
                scope_meta.remove_session(session_meta.session_id)
                cleaned_sessions.append(session_meta)

            # Update metadata file
            if cleaned_sessions:
                await self._write_meta_file()

            logger.info(
                f"Cleaned up {len(cleaned_sessions)} inactive sessions for "
                f"agent {self.agent_id}, scope={session_scope}"
            )

            return [(session_scope, cleaned_sessions)]

    # ==================== Cleanup ====================
    async def remove_session(
            self,
            session_id: str,
            session_scope: Optional[SessionScope] = None
    ) -> list[tuple[SessionScope, SessionMeta]]:
        """
        Delete the specified session and all its persisted data.

        Includes:
        - Removal from cache.
        - Deletion of the corresponding session directory.
        - Removal of the record from meta_map.
        - Update of sessions.json.

        Args:
            session_id (str): The session ID to delete.
            session_scope (Optional[SessionScope], optional): Optional session scope for faster lookup.
                - If None, search across all session scopes
                - If specified, search only within that session scope

        Returns:
            List[Tuple[SessionScope, SessionMeta]]: List of metadata for deleted sessions,
                each element contains the session scope and session metadata
        """
        async with self._lock:
            removed_sessions = []

            logger.debug(
                f"Removing session {session_id} for agent {self.agent_id}, "
                f"scope={session_scope}"
            )

            # Determine the list of session scopes to search
            scopes_to_search = []
            if session_scope:
                scopes_to_search = [session_scope]
            else:
                scopes_to_search = list(self.meta_map.keys())

            # Search and delete the session
            for scope in scopes_to_search:
                if scope in self.meta_map:
                    scope_meta = self.meta_map[scope]
                    session_meta = scope_meta.get_session(session_id)

                    if session_meta:
                        # Remove from cache
                        if session_id in self.session_cache:
                            del self.session_cache[session_id]

                        # Delete disk data
                        session_dir = SessionPaths.session_dir(
                            self._root_path, self.agent_id, session_id
                        )
                        if session_dir.exists():
                            shutil.rmtree(session_dir, ignore_errors=True)

                        # Remove from metadata
                        removed_meta = scope_meta.remove_session(session_id)
                        if removed_meta:
                            removed_sessions.append((scope, removed_meta))

            # Update metadata file
            if removed_sessions:
                await self._write_meta_file()
                logger.info(
                    f"Removed {len(removed_sessions)} session(s) for agent {self.agent_id}, "
                    f"session_id={session_id}"
                )

            return removed_sessions

    async def remove_scope_sessions(
            self,
            session_scope: SessionScope
    ) -> list[SessionMeta]:
        """
        Delete all sessions under the specified session scope.

        Args:
            session_scope (SessionScope): Session scope.

        Returns:
            list[SessionMeta]: List of metadata for deleted sessions
        """
        async with self._lock:
            if session_scope not in self.meta_map:
                return []

            scope_meta = self.meta_map[session_scope]
            removed_sessions = []

            # Delete all sessions
            for session_meta in scope_meta.sessions[:]:  # 使用副本遍历
                session_id = session_meta.session_id

                # Remove from cache
                if session_id in self.session_cache:
                    del self.session_cache[session_id]

                # Delete disk data
                session_dir = SessionPaths.session_dir(self._root_path, self.agent_id, session_id)
                if session_dir.exists():
                    shutil.rmtree(session_dir, ignore_errors=True)

                removed_sessions.append(session_meta)

            # Clear metadata
            scope_meta.sessions.clear()
            scope_meta.active_session = None

            # If the scope has no sessions, remove it from the mapping
            if not scope_meta.sessions:
                del self.meta_map[session_scope]

            # Update metadata file
            await self._write_meta_file()

            logger.info(
                f"Removed all sessions for agent {self.agent_id}, "
                f"scope={session_scope}, count={len(removed_sessions)}"
            )

            return removed_sessions

    async def remove_all(self) -> None:
        """Delete all session data and metadata files for this Agent."""
        async with self._lock:
            logger.debug(
                f"Removing all session data for agent {self.agent_id}"
            )

            # Clear cache
            self.session_cache.clear()
            self.meta_map.clear()

            # Delete the entire directory
            if self.base_path.exists():
                shutil.rmtree(self.base_path, ignore_errors=True)

            logger.info(
                f"Removed all session data for agent {self.agent_id}"
            )
