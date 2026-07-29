# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import (
    Any,
    AsyncIterator,
    TYPE_CHECKING,
    Union,
)

from openjiuwen.core.session import (
    Config,
)
from openjiuwen.core.foundation.kv_cache.kv_cache_metadata import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    KV_CACHE_AFFINITY_SESSION_ID_ENV,
    KVCacheIdentity,
    team_member_cache_identity,
)
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.interaction.interaction import SimpleAgentInteraction
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.stream import BaseStreamMode, OutputSchema
from openjiuwen.core.session.workflow import Session as WorkflowSession

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard


class Session:
    def __init__(self,
                 session_id: str = None,
                 envs: dict[str, Any] = None,
                 card: "AgentCard" = None,
                 *,
                 stream_writer_manager: StreamWriterManager | None = None,
                 close_stream_on_post_run: bool = True,
                 source_metadata: dict[str, Any] | None = None,
                 parent_session_id: str | None = None):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = AgentSession(
            session_id=session_id,
            config=config,
            card=card,
            stream_writer_manager=stream_writer_manager,
        )
        self._card = card
        self._pre_run_done = False
        self._post_run_done = False
        self._interaction = None
        self._close_stream_on_post_run = close_stream_on_post_run
        self._source_metadata = source_metadata or {}
        self._parent_session_id = (
            parent_session_id.strip()
            if isinstance(parent_session_id, str) and parent_session_id.strip()
            else None
        )
        self._team_cache_scope: tuple[str, str] | None = None

    def get_session_id(self) -> str:
        return self._session_id

    def get_parent_session_id(self) -> str | None:
        """Return the optional product Session that owns this child Session."""
        return self._parent_session_id

    def bind_parent_session_id(self, parent_session_id: str) -> None:
        """Bind this child to one product Session before it starts running.

        Session lineage is immutable once set. Rebinding the same parent is
        idempotent; attempting to move a live child to another parent is an
        ownership error.
        """
        normalized = (
            parent_session_id.strip()
            if isinstance(parent_session_id, str)
            else ""
        )
        if not normalized:
            return
        if self._parent_session_id is None:
            self._parent_session_id = normalized
            return
        if self._parent_session_id != normalized:
            raise ValueError(
                "Session parent is already bound to "
                f"{self._parent_session_id!r}; cannot rebind to {normalized!r}"
            )

    def get_env(self, key: str, default: Any = None) -> Any:
        return self._inner.config().get_env(key, default)

    def get_envs(self):
        return self._inner.config().get_envs()

    def get_agent_id(self):
        return self._card.id

    def get_agent_name(self):
        return self._card.name

    def get_agent_description(self):
        return self._card.description

    def set_team_cache_scope(self, *, team_id: str, agent_id: str) -> None:
        """Bind the Team scope used to derive this member Session's cache id."""
        if not team_id or not agent_id:
            return
        self._team_cache_scope = (team_id, agent_id)

    def get_cache_identity(self) -> KVCacheIdentity:
        """Return the provider-facing identity owned by this Session.

        Team child sessions need an explicit member scope because all members
        share the product session id. Standalone child sessions, including
        Swarmflow workers, use their runtime session id as ``cache_id`` and may
        point at an explicit product-session parent.
        """
        if self._team_cache_scope is not None:
            team_id, agent_id = self._team_cache_scope
            return KVCacheIdentity(
                cache_id=team_member_cache_identity(
                    self._session_id,
                    team_id,
                    agent_id,
                ),
                parent_cache_id=self._session_id,
            )
        source_agent_id = self._source_metadata.get("source_agent_id")
        source_team_id = self._source_metadata.get("source_team_id")
        if source_team_id and source_agent_id:
            return KVCacheIdentity(
                cache_id=team_member_cache_identity(
                    self._session_id,
                    source_team_id,
                    source_agent_id,
                ),
                parent_cache_id=self._session_id,
            )
        cache_id = self.get_env(KV_CACHE_AFFINITY_SESSION_ID_ENV)
        parent_cache_id = self.get_env(KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV)
        has_cache_id = isinstance(cache_id, str) and bool(cache_id.strip())
        has_parent_cache_id = (
            isinstance(parent_cache_id, str) and bool(parent_cache_id.strip())
        )
        if has_cache_id or has_parent_cache_id or self._parent_session_id:
            cache_id = cache_id or self._session_id
            return KVCacheIdentity(
                cache_id=cache_id,
                parent_cache_id=(
                    parent_cache_id or self._parent_session_id or cache_id
                ),
            )
        return KVCacheIdentity(
            cache_id=self._session_id,
            parent_cache_id=self._session_id,
        )

    def tracer(self):
        """Return the tracer bound to this session."""
        return self._inner.tracer()

    @property
    def agent_span(self):
        """Current agent root span (set by agent invoke / OtelRail)."""
        return self._inner.span()

    @agent_span.setter
    def agent_span(self, value) -> None:
        self._inner.set_span(value)

    def update_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    def dump_state(self) -> dict:
        return self._inner.state().dump()

    async def write_stream(self, data: Union[dict, OutputSchema]):
        stream_data = self._normalize_output_stream(self._tag_stream_payload(data))
        from openjiuwen.core.runner.callback import trigger
        await trigger(self._session_id + "write_stream", data=stream_data)
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.OUTPUT).write(
            stream_data)

    async def write_custom_stream(self, data: dict):
        stream_data = self._tag_stream_payload(data)
        from openjiuwen.core.runner.callback import trigger
        await trigger(self._session_id + "write_stream", data=stream_data)
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.CUSTOM).write(stream_data)

    def stream_iterator(self) -> AsyncIterator[Any]:
        return self._inner.stream_writer_manager(
        ).stream_output()

    async def pre_run(self, **kwargs):
        if self._pre_run_done:
            return self
        from openjiuwen.core.runner.callback import trigger
        from openjiuwen.core.runner.callback.events import SessionEvents
        await trigger(SessionEvents.AGENT_SESSION_CREATED,
                                    session_id=self.get_session_id(),
                                    card=self._card,
                                    session=self)
        inputs = kwargs.get("inputs")
        await CheckpointerFactory.get_checkpointer().pre_agent_execute(self._inner, inputs)
        self._pre_run_done = True
        return self

    async def close_stream(self):
        """Close the stream emitter to send END_FRAME.

        This unblocks stream_iterator() without triggering
        full session cleanup (checkpointer). Use this when
        the caller (e.g. Runner) manages the session lifecycle.
        """
        await self._inner.stream_writer_manager().stream_emitter().close()
        from openjiuwen.core.runner.runner import Runner
        await Runner.callback_framework.unregister_event(event=self._session_id + "write_stream")

    async def post_run(self):
        if self._post_run_done:
            return self
        if self._close_stream_on_post_run:
            await self.close_stream()
        await self.commit()
        self._post_run_done = True
        return self

    async def commit(self):
        """Persist the current session state without closing the stream."""
        await self._inner.checkpointer().post_agent_execute(self._inner)

    def create_workflow_session(self) -> WorkflowSession:
        return WorkflowSession(parent=self._inner, session_id=self.get_session_id())

    async def interact(self, value):
        if self._interaction is None:
            self._interaction = SimpleAgentInteraction(self._inner)
        await self._interaction.wait_user_inputs(value)

    def _tag_stream_payload(self, data: Union[dict, OutputSchema]):
        if not self._source_metadata:
            return data
        if isinstance(data, dict):
            return {**data, **self._source_metadata}
        if isinstance(data, OutputSchema):
            payload = data.payload
            if isinstance(payload, dict):
                payload = {**payload, **self._source_metadata}
            else:
                payload = {
                    "value": payload,
                    **self._source_metadata,
                }
            return data.model_copy(update={"payload": payload})
        return data

    @staticmethod
    def _normalize_output_stream(data: Union[dict, OutputSchema]) -> OutputSchema:
        if isinstance(data, OutputSchema):
            return data
        if {"type", "index", "payload"}.issubset(data.keys()):
            return OutputSchema.model_validate(data)
        return OutputSchema(type="message", index=0, payload=data)


def create_agent_session(session_id: str = None,
                         envs: dict[str, Any] = None,
                         card: "AgentCard" = None,
                         *,
                         stream_writer_manager: StreamWriterManager | None = None,
                         source_metadata: dict[str, Any] | None = None,
                         parent_session_id: str | None = None,
                         **kwargs) -> Session:
    close_stream_on_post_run = kwargs.get("close_stream_on_post_run", True)
    session = Session(
        session_id=session_id,
        envs=envs,
        card=card,
        stream_writer_manager=stream_writer_manager,
        close_stream_on_post_run=close_stream_on_post_run,
        source_metadata=source_metadata,
        parent_session_id=parent_session_id,
    )
    return session
