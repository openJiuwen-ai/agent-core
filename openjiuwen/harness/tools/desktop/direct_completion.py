"""Owned worker cleanup through the existing host lifecycle; no model-loop policy."""

import weakref

from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent


class DirectCompletionGuard:
    """Release an owned input worker when the normal host turn ends."""

    def __init__(self, callback, state):
        self.agent = weakref.ref(callback.agent)
        self.context = weakref.ref(callback.context)
        self.state = weakref.ref(state)
        self.session_id = callback.session.get_session_id()
        self.registrations = []

    async def install(self):
        """Attach cleanup to the existing host turn-end callback."""
        agent = self.agent()
        try:
            for event, handler in ((AgentCallbackEvent.AFTER_INVOKE, self.after_invoke),):
                await agent.register_callback(event, handler, priority=20)
                self.registrations.append((event, handler))
        except BaseException:
            await self.detach()
            raise

    async def detach(self):
        """Remove only callbacks registered by this guard."""
        agent = self.agent()
        if agent is not None:
            for event, handler in self.registrations:
                await agent.agent_callback_manager.unregister(event, handler)
        self.registrations.clear()

    def owned_state(self, ctx):
        """Match both session and context before releasing a worker."""
        state = self.state()
        session = getattr(ctx, "session", None)
        if (
            state is None
            or ctx.context is not self.context()
            or getattr(session, "get_session_id", lambda: None)() != self.session_id
        ):
            return None
        return state

    async def after_invoke(self, ctx):
        """Release an unfinished owned session when its host turn ends."""
        state = self.owned_state(ctx)
        if state is None:
            return
        try:
            if not state.closed:
                await state.close("cancelled", "host turn ended without done or close")
        finally:
            await self.detach()


async def install_completion_guard(callback, state):
    """Use the actual invoking SDK agent, avoiding host-specific factory wiring."""
    agent = getattr(callback, "agent", None)
    if (
        not callable(getattr(agent, "register_callback", None))
        or not callable(getattr(getattr(agent, "agent_callback_manager", None), "unregister", None))
        or getattr(callback, "context", None) is None
        or getattr(callback, "session", None) is None
    ):
        return None  # Standalone tool callers keep explicit done/close and the absolute deadline.
    guard = DirectCompletionGuard(callback, state)
    await guard.install()
    return guard
