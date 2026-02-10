# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""AgentCallbackManager Class Definition
"""
import asyncio
from typing import Dict, List, Tuple, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.middleware.base import AgentCallbackEvent, AgentCallback, AgentMiddleware, \
    AnyAgentCallback, AgentCallbackContext


class AgentCallbackManager:
    """Manager for Middlewares.

    Supports both function-style and middleware-style callbacks with priority ordering.
    """

    def __init__(self):
        self._callbacks: Dict[AgentCallbackEvent, List[Tuple[int, AgentCallback]]] = {
            event: [] for event in AgentCallbackEvent
        }
        self._middlewares: List[AgentMiddleware] = []

    def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: AnyAgentCallback,
        priority: int = 100
    ) -> 'AgentCallbackManager':
        """Register an agent callback for an event.

        Args:
            event: The agent callback event to register for
            callback: The callback function (sync or async)
            priority: Execution priority (lower = runs first)

        Returns:
            self for chaining
        """
        # Wrap sync callbacks to async
        if not asyncio.iscoroutinefunction(callback):
            original = callback

            async def async_wrapper(ctx: AgentCallbackContext):
                original(ctx)
            callback = async_wrapper

        self._callbacks[event].append((priority, callback))
        # Sort by priority
        self._callbacks[event].sort(key=lambda x: x[0])
        return self

    def register_middleware(self, middleware: AgentMiddleware) -> 'AgentCallbackManager':
        """Register a middleware instance.

        Args:
            middleware: AgentMiddle instance

        Returns:
            self for chaining
        """
        self._middlewares.append(middleware)

        # Extract and register hooks from plugin
        for event, callback in middleware.get_callbacks().items():
            self.register_callback(event, callback, middleware.priority)

        return self

    def unregister(self, event: AgentCallbackEvent, callback: AnyAgentCallback) -> bool:
        """Unregister a hook callback.

        Args:
            event: The hook event
            callback: The callback to remove

        Returns:
            True if callback was found and removed
        """
        original_len = len(self._callbacks[event])
        self._callbacks[event] = [
            (p, cb) for p, cb in self._callbacks[event]
            if cb != callback
        ]
        return len(self._callbacks[event]) < original_len

    def clear(self, event: Optional[AgentCallbackEvent] = None) -> None:
        """Clear hooks.

        Args:
            event: Specific event to clear, or None to clear all
        """
        if event:
            self._callbacks[event] = []
        else:
            for e in AgentCallbackEvent:
                self._callbacks[e] = []

    def has_hooks(self, event: AgentCallbackEvent) -> bool:
        """Check if any hooks are registered for an event.

        Args:
            event: The hook event to check

        Returns:
            True if hooks are registered
        """
        return len(self._callbacks[event]) > 0

    async def execute(
        self,
        event: AgentCallbackEvent,
        ctx: AgentCallbackContext,
    ) -> AgentCallbackContext:
        """Execute all hooks for an event.

        Args:
            event: The hook event
            ctx: The hook context

        Returns:
            The (potentially modified) context
        """
        for priority, callback in self._callbacks[event]:
            try:
                await callback(ctx)
            except Exception as e:
                logger.error(f"Hook error for {event.value}: {e}", exc_info=True)
                raise
        return ctx