# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import session_logger as logger
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.session.session_controller.chain_session import ChainSession
from openjiuwen.core.session.session_controller.data_container import (
    DEFAULT_DATA_CONTAINER_TYPE,
    SharingPolicy,
)
from openjiuwen.core.session.session_controller.schema import SessionMeta
from openjiuwen.core.session.session_controller.scope import SessionScope, SessionScopeKey
from openjiuwen.core.session.session_controller.scope_factory import SessionScopeFactory
from openjiuwen.core.session.session_controller.session_controller import SessionController
from openjiuwen.core.session.session_controller.utils import SessionPaths


class GlobalSessionConfig(BaseModel):
    """Configuration for the global session controller."""

    base_path: str = Field(
        default="./agents",
        description="Root directory for all Agent data storage"
    )


class GlobalSessionController(metaclass=Singleton):
    """
    Global session controller (Singleton pattern).

    Serves as the unified entry point for the system, managing all Agent SessionController
    instances and providing cross-Agent batch async load/flush operations.
    """

    def __init__(self):
        """
        Initialize the global controller (executed only on first instantiation).

        Args:
            base_path (str): Root directory for all Agent data storage, defaults to "./agents".
        """
        self.base_path = Path("./agents")
        self.controllers: dict[str, SessionController] = {}
        self._data_container_type: str = DEFAULT_DATA_CONTAINER_TYPE
        self._lock = asyncio.Lock()
        GlobalSessionController._register_team_event_callbacks()

    def set_config(
            self,
            config: dict[str, Any] | GlobalSessionConfig
    ) -> None:
        if isinstance(config, GlobalSessionConfig):
            self.base_path = Path(config.base_path)
        else:
            self.base_path = Path(config.get("base_path", "./agents"))

    # ==================== Persistence ====================
    async def load_agent(self, agent_id: str, load_active_only: bool = True) -> None:
        """
        Asynchronously load session data for the specified Agent.

        Args:
            agent_id (str): Agent ID.
            load_active_only (bool, optional): Whether to load only active sessions. Defaults to True.
                - True: Load only active sessions for better performance
                - False: Load all sessions (including historical sessions)
        """
        async with self._lock:
            logger.debug(
                f"Loading agent {agent_id}, load_active_only={load_active_only}")
            controller = self._get_or_create_controller(agent_id)
            await controller.load(load_active_only)

    async def load_scope(self, session_scope: SessionScope, load_active_only: bool = True) -> None:
        """
        Asynchronously load session data for the specified session scope.

        This method loads session data under the specified session scope across all Agents.

        Args:
            session_scope (SessionScope): The session scope to load.
            load_active_only (bool, optional): Whether to load only active sessions. Defaults to True.
                - True: Load only active sessions for better performance
                - False: Load all sessions (including historical sessions)
        """
        async with self._lock:
            logger.debug(f"Loading scope {session_scope} across all agents")
            tasks = []
            for agent_id, controller in self.controllers.items():
                tasks.append(controller.load_scope(session_scope, load_active_only))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info(f"Loaded scope {session_scope} across {len(tasks)} agents")

    async def load_all(self, load_active_only: bool = True) -> None:
        """
        Asynchronously load session data for all registered Agents.

        Args:
            load_active_only (bool, optional): Whether to load only active sessions. Defaults to True.
                - True: Load only active sessions for better performance
                - False: Load all sessions (including historical sessions)
        """
        async with self._lock:
            logger.debug(f"Loading all agents, load_active_only={load_active_only}")
            if self.base_path.exists():
                for item in self.base_path.iterdir():
                    if item.is_dir():
                        agent_id = item.name
                        controller = self._get_or_create_controller(agent_id)
                        await controller.load(load_active_only)
            logger.info(f"Loaded all agents from {self.base_path}")

    async def flush_agent(self, agent_id: str) -> None:
        """
        Asynchronously flush the specified Agent's session data to disk.

        Args:
            agent_id (str): Agent ID.
        """
        async with self._lock:
            if agent_id in self.controllers:
                logger.debug(f"Flushing agent {agent_id}")
                await self.controllers[agent_id].flush()
            else:
                logger.warning(f"Agent {agent_id} not found, skip flushing")

    async def flush_session(self, session_id: str) -> None:
        """
        Asynchronously flush the specified session's data to disk.

        This method finds all Agents containing the given session_id and flushes
        the corresponding session data.

        Args:
            session_id (str): The session ID to flush.
        """
        async with self._lock:
            logger.debug(f"Flushing session {session_id} across agents")
            tasks = []
            for controller in self.controllers.values():
                if session_id in controller.session_cache:
                    tasks.append(controller.flush_session(session_id))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info(f"Flushed session {session_id} across {len(tasks)} agents")
            else:
                logger.warning(f"Session {session_id} not found in any agent cache")

    async def flush_scope(self, session_scope: SessionScope) -> None:
        """
        Asynchronously flush all session data for the specified session scope to disk.

        This method flushes all session data under the specified session scope across all Agents.

        Args:
            session_scope (SessionScope): The session scope to flush.
        """
        async with self._lock:
            logger.debug(f"Flushing scope {session_scope} across all agents")
            tasks = []
            for controller in self.controllers.values():
                tasks.append(controller.flush_scope(session_scope))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info(f"Flushed scope {session_scope} across {len(tasks)} agents")

    async def flush_all(self) -> None:
        """Asynchronously flush all registered Agents' session data to disk."""
        async with self._lock:
            logger.debug("Flushing all agents")
            tasks = []
            for controller in self.controllers.values():
                tasks.append(controller.flush())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info(f"Flushed all {len(tasks)} agents")

    async def cleanup_agent_inactive_sessions(
            self,
            agent_id: str
    ) -> dict[str, list[tuple[SessionScope, SessionMeta]]]:
        """
        Asynchronously clean up inactive session data for the specified Agent.

        Cleanup logic:
        1. Call the specified Agent's SessionController cleanup_scope_inactive_sessions method
        2. Summarize the cleaned session information

        Args:
            agent_id (str): The Agent ID to clean up.

        Returns:
            Dict[str, List[Tuple[SessionScope, SessionMeta]]]: Mapping of cleaned session metadata grouped by Agent ID
                - Key: Agent ID
                - Value: List of cleaned session metadata for that Agent, each element contains
                  the session scope and the list of cleaned session metadata within that scope

        Raises:
            ValueError: If the specified agent_id does not exist
        """
        async with self._lock:
            if agent_id not in self.controllers:
                raise ValueError(f"Agent {agent_id} not found")

            logger.debug(
                f"Cleaning up inactive sessions for agent {agent_id}"
            )

            controller = self.controllers[agent_id]

            scope_metas = controller.list_metas()
            cleaned_sessions = {}

            for session_scope in scope_metas.keys():
                scope_cleaned = await controller.cleanup_scope_inactive_sessions(session_scope)
                if scope_cleaned:
                    cleaned_sessions[agent_id] = (
                            cleaned_sessions.get(agent_id, []) + scope_cleaned
                    )

            logger.info(
                f"Cleaned up inactive sessions for agent {agent_id}, "
                f"scopes_cleaned={len(cleaned_sessions)}"
            )

            return cleaned_sessions

    async def cleanup_scope_inactive_sessions(
            self,
            session_scope: SessionScope
    ) -> dict[str, list[SessionMeta]]:
        """
        Asynchronously clean up inactive session data for the specified session scope,
        cleaning inactive sessions for that session scope across all Agents.

        Cleanup logic:
        1. For each registered Agent, call its SessionController's cleanup_scope_inactive_sessions method
        2. Summarize all cleaned session information, grouped by agent_id

        Args:
            session_scope (SessionScope): The session scope to clean up

        Returns:
            Dict[str, List[SessionMeta]]: Mapping of cleaned session metadata grouped by Agent ID
                - Key: Agent ID
                - Value: List of cleaned session metadata for that Agent
        """
        async with self._lock:
            cleaned_sessions = {}

            for agent_id, controller in self.controllers.items():
                # Check if the Agent has sessions in the specified session scope
                scope_metas = controller.list_metas()
                if session_scope in scope_metas:
                    # Clean up inactive sessions for this session scope
                    scope_cleaned_list = await controller.cleanup_scope_inactive_sessions(session_scope)

                    # Extract cleaned session metadata
                    for scope_cleaned in scope_cleaned_list:
                        cleaned_scope, session_metas_list = scope_cleaned
                        if session_metas_list:
                            cleaned_sessions[agent_id] = (
                                    cleaned_sessions.get(agent_id, [])
                                    + session_metas_list
                            )

            return cleaned_sessions

    def get_agent(self, agent_id: str) -> Optional[SessionController]:
        """
        Synchronously get the Agent's session controller (does not perform loading).

        Args:
            agent_id (str): Agent ID.

        Returns:
            Optional[SessionController]: Returns the controller if it exists, otherwise None.
        """
        return self.controllers.get(agent_id)

    async def create_if_not_exist_agent(
            self,
            agent_id: str
    ) -> tuple[bool, SessionController]:
        """
        Asynchronously get or create the Agent's session controller.

        If the controller does not exist, a new instance is created and its load() method
        is automatically called.

        Args:
            agent_id (str): Agent ID.

        Returns:
            Tuple[bool, SessionController]: (operation type, controller instance)
            Operation type: True means newly created, False means already exists.
        """
        async with self._lock:
            if agent_id in self.controllers:
                return False, self.controllers[agent_id]

            # Create new controller
            self._ensure_base_path()
            controller = SessionController(
                agent_id, self.base_path,
                data_container_type=self._data_container_type
            )
            await controller.load()
            self.controllers[agent_id] = controller

            return True, controller

    async def remove_agent(self, agent_id: str) -> bool:
        """
        Asynchronously delete all data for the specified Agent, including session files and metadata.

        Args:
            agent_id (str): Agent ID.

        Returns:
            bool: True if deletion succeeded.
        """
        async with self._lock:
            if agent_id in self.controllers:
                # Delete all session data
                controller = self.controllers[agent_id]
                await controller.remove_all()

                # Remove from controller mapping
                del self.controllers[agent_id]

                # Delete Agent directory
                agent_dir = SessionPaths.agent_dir(self.base_path, agent_id)
                if agent_dir.exists():
                    shutil.rmtree(agent_dir, ignore_errors=True)

                return True
            return False

    async def remove_all(self) -> None:
        """Asynchronously clear all Agents' session data."""
        async with self._lock:
            # Delete all controller data
            tasks = []
            for controller in self.controllers.values():
                tasks.append(controller.remove_all())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Clear controller mapping
            self.controllers.clear()

            # Delete the entire base directory
            if self.base_path.exists():
                shutil.rmtree(self.base_path, ignore_errors=True)

    async def cleanup_orphan_files(
            self,
            agent_id: Optional[str] = None,
            dry_run: bool = False
    ) -> dict[str, list[str]]:
        """
        Scan and clean up orphan directories (session directories on disk that are not
        referenced in sessions.json).
        1. Iterate through the sessions/ directory of the specified Agent (or all registered Agents).
        2. Read sessions.json to get all registered session_ids.
        3. List all subdirectories under sessions/ and compare with registered session_ids.
        4. Directories not in sessions.json are orphan directories.
        5. If dry_run=False, delete orphan directories one by one (including state.data + downstreams/).

        Args:
            agent_id (Optional[str]): Specified Agent ID.
                If None, process all registered Agents. Defaults to None.
            dry_run (bool): Whether to scan only without deleting.
                - True: Only scan and return orphan directory list, without deleting.
                - False: Scan and delete orphan directories. Defaults to False.

        Returns:
            Dict[str, List[str]]: Mapping of orphan directories grouped by Agent ID.
                - Key: Agent ID
                - Value: List of orphan session_ids for that Agent (scan results when dry_run=True,
                  deleted results when dry_run=False)
        """
        async with self._lock:
            result = {}

            # Determine the list of Agents to process
            agents_to_process = []
            if agent_id:
                if agent_id in self.controllers:
                    agents_to_process = [agent_id]
                else:
                    # Even without a controller, check if the directory exists
                    agent_dir = SessionPaths.agent_dir(self.base_path, agent_id)
                    if agent_dir.exists():
                        agents_to_process = [agent_id]
            else:
                # Process all Agent directories
                if self.base_path.exists():
                    for item in self.base_path.iterdir():
                        if item.is_dir():
                            agents_to_process.append(item.name)

            for current_agent_id in agents_to_process:
                agent_sessions_dir = SessionPaths.sessions_dir(self.base_path, current_agent_id)
                if not agent_sessions_dir.exists():
                    continue

                # Read sessions.json to get registered session_ids
                registered_sessions = set()
                meta_file = SessionPaths.meta_file(self.base_path, current_agent_id)
                if meta_file.exists():
                    try:
                        with open(meta_file, 'r', encoding='utf-8') as f:
                            meta_data = json.load(f)

                        # Extract all session_ids
                        for scope_meta_data in meta_data.values():
                            if 'sessions' in scope_meta_data:
                                for session_data in scope_meta_data['sessions']:
                                    if 'session_id' in session_data:
                                        registered_sessions.add(session_data['session_id'])
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.error(
                            f"Error reading sessions.json for "
                            f"agent {current_agent_id}: {e}"
                        )

                # Scan all subdirectories under the sessions directory
                orphan_dirs = []
                for item in agent_sessions_dir.iterdir():
                    if item.is_dir() and item.name != "downstreams":
                        # Check if it's a session directory (by checking for state.data file)
                        state_file = SessionPaths.state_file(item)
                        if state_file.exists():
                            # Check if it's in the registered session_ids
                            if item.name not in registered_sessions:
                                orphan_dirs.append(item.name)

                if orphan_dirs:
                    result[current_agent_id] = orphan_dirs

                    if dry_run:
                        logger.info(
                            f"Found {len(orphan_dirs)} orphan dirs for agent {current_agent_id} "
                            f"(dry_run=True)"
                        )
                    else:
                        # Delete orphan directories
                        for orphan_dir_name in orphan_dirs:
                            orphan_dir = SessionPaths.session_dir(
                                self.base_path, current_agent_id, orphan_dir_name
                            )
                            if orphan_dir.exists():
                                shutil.rmtree(orphan_dir, ignore_errors=True)
                        logger.info(
                            f"Deleted {len(orphan_dirs)} orphan dirs for agent {current_agent_id}"
                        )

            return result

    @staticmethod
    async def create_direct_session(
            agent_id: str,
            user_id: str,
            session_id: str,
            data_params=None,
    ) -> tuple[bool, ChainSession]:
        """
        Convenience method for creating a direct chat session.

        Users don't need to understand SessionScope, SessionController, etc.; they only
        need to provide the necessary parameters to create a direct chat session.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.
            session_id (str): Session ID (UUID).

        Returns:
            Tuple[bool, ChainSession]: (whether newly created, session object)
        """
        if data_params is None:
            data_params = {}
        instance = get_global_session_controller()

        # Create session scope
        session_scope = SessionScopeFactory.create_direct(user_id)

        # Get or create Agent controller
        _, controller = await instance.create_if_not_exist_agent(agent_id)

        # Create session
        return await controller.create_if_not_exists(
            session_scope=session_scope,
            session_id=session_id,
            **data_params
        )

    @staticmethod
    async def create_group_session(
            agent_id: str,
            group_id: str,
            session_id: str,
            data_params=None,
    ) -> tuple[bool, ChainSession]:
        """
        Convenience method for creating a group chat session.

        Args:
            agent_id (str): Agent ID.
            group_id (str): Group ID.
            session_id (str): Session ID (UUID).
            data_params (dict | None): Additional parameters passed to the session creation.
                If None, defaults to an empty dict.

        Returns:
            Tuple[bool, ChainSession]: (whether newly created, session object)
        """
        if data_params is None:
            data_params = {}
        instance = get_global_session_controller()

        # Create session scopes
        session_scope = SessionScopeFactory.create_group(group_id)

        # Get or create Agent controller
        _, controller = await instance.create_if_not_exist_agent(agent_id)

        # Create session
        return await controller.create_if_not_exists(
            session_scope=session_scope,
            session_id=session_id,
            **data_params
        )

    @staticmethod
    async def get_direct_session_data(agent_id: str, user_id: str) -> Optional[Any]:
        """
        Convenience method for getting direct chat session data.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.

        Returns:
            Optional[Any]: Session data, or None if it doesn't exist
        """
        instance = get_global_session_controller()

        # Create session scope
        session_scope = SessionScopeFactory.create_direct(user_id)

        # Get Agent controller
        controller = instance.get_agent(agent_id)
        if not controller:
            return None

        # Get active session
        session = await controller.get_scope_active_session(session_scope)
        if not session:
            return None

        return session.get_data()

    @staticmethod
    async def update_direct_session_data(
            agent_id: str,
            user_id: str,
            data: dict
    ) -> bool:
        """
        Convenience method for updating direct chat session data.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.
            data (dict): Data dictionary to update.

        Returns:
            bool: True if update succeeded
        """
        instance = get_global_session_controller()

        # Create session scope
        session_scope = SessionScopeFactory.create_direct(user_id)

        # Get Agent controller
        controller = instance.get_agent(agent_id)
        if not controller:
            return False

        # Get active session
        session = await controller.get_scope_active_session(session_scope)
        if not session:
            return False

        # Update data
        return await session.update_data(data)

    @staticmethod
    async def add_direct_session_downstream(
            caller_agent_id: str,
            caller_user_id: str,
            target_agent_id: str,
            target_user_id: str,
            policy: SharingPolicy = SharingPolicy()
    ) -> bool:
        """
        Convenience method for adding a downstream relationship to a direct chat session.

        Args:
            caller_agent_id (str): Caller Agent ID.
            caller_user_id (str): Caller user ID.
            target_agent_id (str): Target Agent ID.
            target_user_id (str): Target user ID.
            policy (SharingPolicy): Sharing policy, defaults to read-only for all fields.

        Returns:
            bool: True if addition succeeded
        """
        instance = get_global_session_controller()

        # Get caller Agent controller
        caller_controller = instance.get_agent(caller_agent_id)
        if not caller_controller:
            return False

        # Get target Agent controller
        target_controller = instance.get_agent(target_agent_id)
        if not target_controller:
            return False

        # Create session scope
        caller_scope = SessionScopeFactory.create_direct(caller_user_id)
        target_scope = SessionScopeFactory.create_direct(target_user_id)

        # Get caller active session
        caller_session = await caller_controller.get_scope_active_session(caller_scope)
        if not caller_session:
            return False

        # Get target active session
        target_session = await target_controller.get_scope_active_session(target_scope)
        if not target_session:
            return False

        # Add downstream relationship
        caller_session.add_downstream(
            target_agent=target_agent_id,
            target_session=target_session.session_id,
            policy=policy
        )

        # Flush to disk
        await caller_session.flush()

        return True

    @staticmethod
    async def cleanup_user_sessions(
            agent_id: str,
            user_id: str
    ) -> list[tuple[SessionScope, list[SessionMeta]]]:
        """
        Convenience method for cleaning up all inactive sessions for a user.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.

        Returns:
            List[Tuple[SessionScope, List[SessionMeta]]]: Cleaned session metadata
        """
        instance = get_global_session_controller()

        session_scope = SessionScopeFactory.create_direct(user_id)

        controller = instance.get_agent(agent_id)
        if not controller:
            return []

        return await controller.cleanup_scope_inactive_sessions(session_scope)

    @staticmethod
    async def get_user_session_history(
            agent_id: str,
            user_id: str
    ) -> list[ChainSession]:
        """
        Convenience method for getting user session history.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.

        Returns:
            List[ChainSession]: All historical sessions for the user
        """
        instance = get_global_session_controller()

        session_scope = SessionScopeFactory.create_direct(user_id)

        controller = instance.get_agent(agent_id)
        if not controller:
            return []

        return await controller.get_scope_sessions(session_scope)

    @staticmethod
    async def flush_user_session(agent_id: str, user_id: str) -> bool:
        """
        Convenience method for flushing user active session data.

        Args:
            agent_id (str): Agent ID.
            user_id (str): User ID.

        Returns:
            bool: True if flush succeeded
        """
        instance = get_global_session_controller()

        session_scope = SessionScopeFactory.create_direct(user_id)

        controller = instance.get_agent(agent_id)
        if not controller:
            logger.warning(
                f"Agent {agent_id} not found for flush_user_session"
            )
            return False

        return await controller.flush_scope(session_scope)

    @staticmethod
    async def visualize_call_chain(agent_id: str, session_id: str, depth: int = 3) -> str:
        """
        Generate a call chain visualization text for the specified session.

        Displays downstream call relationships in a tree structure, supporting
        recursive display of multi-level call relationships.

        Args:
            agent_id (str): Agent ID.
            session_id (str): Session ID.
            depth (int, optional): Maximum recursion depth, defaults to 3.

        Returns:
            str: Call chain visualization text
        """
        instance = get_global_session_controller()

        controller = instance.get_agent(agent_id)
        if not controller:
            return f"Agent {agent_id} not found"

        if session_id not in controller.session_cache:
            return f"Session {session_id} not found in agent {agent_id}"

        session = controller.session_cache[session_id]
        lines = []
        lines.append("ChainSession Call Chain Visualization")
        lines.append("=" * 50)
        scope_key = SessionScopeKey(agent_id, session.session_scope)
        status = "Active" if session.is_active else "Inactive"
        lines.append(f"Current session: {scope_key} [{session_id[:8]}...]")
        lines.append(f"Status: {status}")
        lines.append("")
        lines.append(f"Call chain relationships (depth: {depth}):")
        lines.append("-" * 50)

        def _build_tree(s: ChainSession, prefix: str, current_depth: int):
            if current_depth > depth:
                return
            for (target_agent, target_session_id), policy in s.get_downstreams().items():
                connector = "├─►" if current_depth < depth else "└─►"
                lines.append(f"{prefix}{connector} {target_agent} [{target_session_id[:8]}...]")
                perm_str = str(policy.permission.name)
                lines.append(f"{prefix}│   ├─ Permissions: {perm_str}")
                if policy.field_scopes:
                    lines.append(f"{prefix}│   ├─ Field scope: {policy.field_scopes}")
                else:
                    lines.append(f"{prefix}│   ├─ Field scope: All fields")
                target_ctrl = instance.get_agent(target_agent)
                if target_ctrl and target_session_id in target_ctrl.session_cache:
                    target_s = target_ctrl.session_cache[target_session_id]
                    _build_tree(target_s, prefix + "│   ", current_depth + 1)
                else:
                    lines.append(f"{prefix}│   └─ (not loaded)")

        _build_tree(session, "", 1)
        return "\n".join(lines)

    # ==================== Internal Methods ===================
    @staticmethod
    def _register_team_event_callbacks() -> None:
        try:
            from openjiuwen.core.runner import Runner
            from openjiuwen.core.runner.callback.events import AgentTeamEvents
            from openjiuwen.core.runner.callback.events import SessionEvents

            Runner.callback_framework.register_sync(
                AgentTeamEvents.AGENT_P2P_RECEIVED,
                _on_agent_p2p_received,
            )
            Runner.callback_framework.register_sync(
                AgentTeamEvents.AGENT_PUBSUB_RECEIVED,
                _on_agent_pubsub_received,
            )
            Runner.callback_framework.register_sync(
                SessionEvents.AGENT_SESSION_CREATED,
                _on_agent_session_created,
            )
        except Exception as e:
            logger.debug(f"Skip team event callbacks registration: {e}")

    def _ensure_base_path(self) -> None:
        if not self.base_path.exists():
            self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_or_create_controller(self, agent_id: str) -> SessionController:
        if agent_id not in self.controllers:
            self._ensure_base_path()
            self.controllers[agent_id] = SessionController(
                agent_id, self.base_path, data_container_type=self._data_container_type
            )
        return self.controllers[agent_id]


def configure_global_session_controller(
        config: GlobalSessionConfig | dict
) -> None:
    """Configure the global session controller with the given configuration.

    Args:
        config: Configuration object or dictionary. Supports GlobalSessionConfig
                instance or a dict with a 'base_path' key.
    """
    instance = get_global_session_controller()
    instance.set_config(config)


def get_global_session_controller() -> GlobalSessionController:
    return GlobalSessionController()


async def _on_agent_p2p_received(sender: str, recipient: str, message_id: str, session_id: str = None, **kwargs):
    """Callback for AGENT_P2P_RECEIVED: record downstream from sender to recipient."""
    try:
        from openjiuwen.core.runner.runner_config import get_runner_config

        runner_config = get_runner_config()
        if not runner_config.enable_session_controller:
            return
        instance = get_global_session_controller()
        sender_controller = instance.get_agent(sender)
        if not sender_controller:
            logger.debug(f"[downstream] sender agent '{sender}' not found, skip")
            return
        sender_session = await sender_controller.get_scope_active_session(
            sender_controller.meta_map and next(iter(sender_controller.meta_map))
        )
        if not sender_session:
            logger.debug(f"[downstream] no active session for sender '{sender}', skip")
            return
        if sender_session.has_downstream(recipient, session_id or ""):
            return
        sender_session.add_downstream(
            target_agent=recipient,
            target_session=session_id or "",
        )
        await sender_session.flush()
        logger.debug(f"[downstream] P2P: {sender}/{sender_session.session_id} -> {recipient}/{session_id}")
    except Exception as e:
        logger.error(f"[downstream] Error recording P2P downstream: {e}", exc_info=True)


async def _on_agent_pubsub_received(sender: str, subscriber: str, topic_id: str, message_id: str,
                                    session_id: str = None, **kwargs):
    """Callback for AGENT_PUBSUB_RECEIVED: record downstream from sender to subscriber."""
    try:
        from openjiuwen.core.runner.runner_config import get_runner_config

        runner_config = get_runner_config()
        if not runner_config.enable_session_controller:
            return
        instance = get_global_session_controller()
        sender_controller = instance.get_agent(sender)
        if not sender_controller:
            logger.debug(f"[downstream] sender agent '{sender}' not found, skip")
            return
        sender_session = await sender_controller.get_scope_active_session(
            sender_controller.meta_map and next(iter(sender_controller.meta_map))
        )
        if not sender_session:
            logger.debug(f"[downstream] no active session for sender '{sender}', skip")
            return
        if sender_session.has_downstream(subscriber, session_id or ""):
            return
        sender_session.add_downstream(
            target_agent=subscriber,
            target_session=session_id or "",
        )
        await sender_session.flush()
        logger.debug(
            f"[downstream] PubSub: {sender}/{sender_session.session_id} -> {subscriber}/{session_id} via"
            f" topic={topic_id}")
    except Exception as e:
        logger.error(f"[downstream] Error recording PubSub downstream: {e}", exc_info=True)


async def _on_agent_session_created(session_id: str = None, card=None, session=None, **kwargs):
    """Callback for AGENT_SESSION_CREATED: update chain_session's data_container session."""
    from openjiuwen.core.runner.runner_config import get_runner_config

    runner_config = get_runner_config()
    if not runner_config.enable_session_controller:
        return

    if session_id is None or card is None or session is None:
        return

    agent_id = card.id if card else None
    if agent_id is None:
        return

    instance = get_global_session_controller()
    controller = instance.get_agent(agent_id)
    if not controller:
        logger.debug(f"[session_created] agent '{agent_id}' not found, skip")
        return

    chain_session = controller.session_cache.get(session_id)
    if not chain_session:
        logger.debug(f"[session_created] chain_session '{session_id}' not found for agent '{agent_id}', skip")
        return

    data_container = chain_session.data_container
    if hasattr(data_container, 'session'):
        data_container.session = session
        logger.debug(
            f"[session_created] updated data_container session for "
            f"agent '{agent_id}', session_id '{session_id}'"
        )
