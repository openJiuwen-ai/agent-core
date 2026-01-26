# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Optional
import datetime
from datetime import timezone

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.session import Session
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.token.base import TokenCounter


class ContextEngine:
    """
    Manages the lifecycle and processing of conversational context.

    ContextEngine acts as the central entry-point for:
    1. Registering and configuring message processors.
    2. Creating isolated ModelContext instances tied to a session.
    3. Applying processor chains to enforce window limits, compression, etc.

    Parameters
    ----------
    config : ContextEngineConfig, optional
        Global engine settings (message/token limits, processor defaults).
        If omitted, a default configuration is used.
    """

    def __init__(self,
                 config: ContextEngineConfig = None,
                 ):
        self._config = config or ContextEngineConfig()
        self._context_pool: Dict[str, ModelContext] = dict()

    async def create_context(self,
                             context_id: str = "default_context_id",
                             session: Session = None,
                             *,
                             history_messages: List[BaseMessage] = None,
                             token_counter: TokenCounter = None,
                             mem_scope_id: str = None,
                             ) -> ModelContext:
        """
        Create or retrieve a ModelContext for the given session & context ID.

        Token counting: if `token_counter` is None, defaults to `TiktokenCounter`.

        Message seeding:
        - if `history_messages` is provided, it is used as-is;
        - else if `mem_scope_id` is given, the engine attempts to restore
          previous messages from long-term memory under that scope;
        - otherwise an empty message list is adopted.

        Args:
            context_id: Unique identifier for this context within the session.
            session: Session object supplying session_id; if None, a default
                     session ID is used.
            history_messages: Initial message list; when omitted, behaviour
                              depends on `mem_scope_id`.
            token_counter: Strategy for counting tokens; defaults to
                           TiktokenCounter if not provided.
            mem_scope_id: Optional memory scope key; when given, messages
                          are loaded from long-term memory if `history_messages`
                          is None.

        Returns:
            ModelContext: The newly created or cached context instance.
        """
        session_id = session.get_session_id() if session else "default_session_id"
        full_context_id = f"{session_id}_{context_id}"
        if full_context_id in self._context_pool:
            return self._context_pool.get(full_context_id)

        if not history_messages and mem_scope_id:
            history_messages = await self._load_context_from_memory(
                session_id=session_id,
                mem_scope_id=mem_scope_id,
                message_num=self._config.memory_message_num
            )

        context = SessionModelContext(
            context_id=context_id,
            session_id=session_id,
            history_messages=history_messages or [],
            window_size_limit=self._config.default_window_message_num,
            token_counter=token_counter,
        )

        self._context_pool[full_context_id] = context
        return context

    def get_context(self,
                    context_id: str = "default_context_id",
                    session_id: str = "default_session_id"
                    ) -> Optional[ModelContext]:
        """
        Retrieve an existing ModelContext from the pool.

        Args:
            context_id: Context identifier within the session.
            session_id: Session identifier.

        Returns:
            ModelContext instance if found, otherwise None.
        """
        full_context_id = f"{session_id}_{context_id}"
        return self._context_pool.get(full_context_id, None)

    def clear_context(self,
                      context_id: str = None,
                      session_id: str = None
                      ):
        """
        Remove contexts from the internal pool.

        Behavior depends on the arguments provided:
        1. Neither argument supplied  -> delete the all context.
        2. Only `session_id` supplied -> delete every context belonging to that session.
        3. Both arguments supplied    -> delete the single context identified..

        Parameters
        ----------
        context_id : str, optional
            Logical context identifier.  When provided, `session_id` must also
            be supplied and only the exact matching context is removed.
        session_id : str, optional
            Session identifier used to scope the deletion.  If omitted, the
            operation targets all contexts.

        Warnings
        --------
        Logs a warning when the requested session or context cannot be found.
        """
        if session_id is None:
            self._context_pool.clear()
            return

        if context_id is None:
            delete_context_list = [
                context_id for context_id, context in self._context_pool.items()
                if context.session_id() == session_id
            ]

            if not delete_context_list:
                logger.warning(f"Delete context failed, session {session_id} does not exist")
                return

            for context_id in delete_context_list:
                del self._context_pool[context_id]
            return

        full_context_id = f"{session_id}_{context_id}"
        if full_context_id not in self._context_pool:
            logger.warning(f"Delete context failed, context {session_id} does not exist")

        del self._context_pool[full_context_id]

    async def save_contexts(self,
                            context_ids: List[str],
                            session: Session = None,
                            *,
                            mem_scope_id: str = None,
                            ):
        """
        Batch-persist multiple contexts and their runtime states.

        Each context's messages, sliding-window position, token count and statistics
        are saved locally. If `mem_scope_id` is provided, the same snapshots are
        also written to long-term memory under that scope for later cross-session
        restoration.

        Args:
            context_ids: List of target context identifiers to save.
            session: Session object; if None, "default_session_id" is used.
            mem_scope_id: Optional memory scope key; when given, all listed
                          contexts are additionally saved to long-term memory
                          with this ID.
        """
        for context_id in context_ids:
            session_id = session.get_session_id() if session else "default_session_id"
            full_context_id = f"{session_id}_{context_id}"
            context: SessionModelContext = self._context_pool.get(full_context_id)
            if not context:
                continue
            if mem_scope_id:
                new_messages = context.get_messages(with_history=False)
                await self._save_context_to_memory(
                    session_id=session_id,
                    mem_scope_id=mem_scope_id,
                    messages=new_messages
                )
            context.on_save()

    @staticmethod
    async def _load_context_from_memory(session_id: str, mem_scope_id: str, message_num: int) -> List[BaseMessage]:
        from openjiuwen.core.memory import LongTermMemory
        messages = await LongTermMemory().get_recent_messages(
            scope_id=mem_scope_id,
            session_id=session_id,
            num=message_num
        )
        return messages

    @staticmethod
    async def _save_context_to_memory(session_id: str, mem_scope_id: str, messages: List[BaseMessage]):
        if not messages:
            return

        from openjiuwen.core.memory import LongTermMemory, AgentMemoryConfig
        await LongTermMemory().add_messages(
            messages,
            AgentMemoryConfig(),
            timestamp=datetime.datetime.now(tz=timezone.utc),
            scope_id=mem_scope_id,
            session_id=session_id
        )
