# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stateful avatar sessions for swarmflow ``agent_session`` / ``human_session``.

Where :class:`TeamWorkerBackend` runs each ``agent()`` as a single-shot worker
(``run_once`` → ``DeepAgent.invoke`` → dispose), a *session* keeps a long-lived
:class:`TeamHarness` and drives it across many turns so context persists. Each
turn is one round on the same supervisor: ``harness.send(prompt)`` then wait for
the harness to settle back to ``IDLE`` (which absorbs any task-loop continuation
rounds), and take the last finished round's output as the turn's reply.

An *agent* session derives from the team's teammate spec (a teammate without
team tools, exactly like a worker, but multi-turn). A *human* session derives
from the human_agent spec and sources each turn's input from a real person — its
turn handling lands in a later stage; the agent path is wired here.

This object is owned by :class:`TeamWorkerBackend`, which delegates the engine's
``open_session`` / ``send_turn`` / ``close_session`` / ``aclose`` to it. It keeps
no process-global state: every session is an instance-scoped row, cleaned up on
``close_session`` / ``aclose``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from openjiuwen.agent_teams.kv_cache.kv_cache_cleanup import (
    cancellation_safe_release_then_dispose,
)
from openjiuwen.agent_teams.kv_cache import kv_cache_harness_session_lifecycle_hook
from openjiuwen.core.kv_cache.kv_cache_types import KVCacheRuntimeProtocol
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.workflow.backends._member_spec import (
    derive_member_build_context,
    derive_member_spec,
)
from openjiuwen.agent_teams.workflow.backends._result_text import (
    prefer_natural_or_structured_text,
)
from openjiuwen.agent_teams.tools.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentResult
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.errors import BackendError, WorkflowAborted
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.core.session.vcs.codec import encode_message

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Multi-turn role prompt — the session counterpart of the worker's single-shot prompt.
_SESSION_SYS_PROMPT_AGENT = (
    "You are a stateful swarmflow session agent in a multi-turn conversation. "
    "You remember every prior turn; answer each new message directly and "
    "concisely, using the accumulated context. Do not restate the whole history."
)
# Role prompt for a human session's avatar: it does NOT invent answers — it renders a
# real person's reply faithfully into the form the turn asked for.
_SESSION_SYS_PROMPT_HUMAN = (
    "You are the avatar of a human team member in a multi-turn conversation. Each "
    "turn you are given the question put to the person and the person's raw reply; "
    "render their reply faithfully into the requested answer (structured output "
    "when a schema is given). Never invent content the person did not express; if "
    "their reply is ambiguous or empty, say so rather than guessing."
)
# Appended to a turn's user prompt when that turn requested structured output.
_SCHEMA_TURN_NUDGE = (
    "When you have the answer for THIS message, call the `structured_output` tool "
    "EXACTLY ONCE with the result conforming to its schema. Do NOT write the "
    "result as plain text — it is captured only through that tool call."
)
# Default ceiling on how long a human turn waits for a person before giving up.
_DEFAULT_HUMAN_TIMEOUT = 600.0

# One-shot intent classification (see ``_classify_intent``): is the person's raw
# reply a request to edit the script/flow and rerun, or a plain continue? Kept
# as module constants (like the tiny-agent title/summary presets) — minimal.
_INTENT_CLASSIFY_PROMPT = (
    "Decide whether the user's reply asks to edit the script and re-run. "
    "If they want to change the script/prompt/workflow and re-run, set "
    "intent=edit_rerun and note the edit points; otherwise intent=continue. "
    "Output structured results only."
)

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["continue", "edit_rerun"]},
        "edit_instructions": {"type": ["string", "null"]},
    },
    "required": ["intent"],
}


@dataclass
class _SessionState:
    """One live session row (instance-scoped; no process-global state)."""

    kind: str  # "agent" | "human"
    spec_base: Any  # base DeepAgentSpec this session derives from
    instructions: str | None
    member_name: str
    budget_rail: SwarmflowBudgetRail
    """This avatar's token rail, mounted for the session's whole life (unlike a
    worker's, which lives for one call). Its tally is cumulative across turns, so
    a turn's own cost is the delta across the round."""
    harness: Any = None  # TeamHarness, built lazily by open_session
    turns_executed: int = 0
    # The child session id this avatar runs on — a stable derived id (not a
    # random uuid) so ``pre_run`` recovery / ``_read_persisted_messages`` can
    # locate the prior run's saved ``state["context"]`` (see _derive_avatar_session_id).
    session_id: str | None = None
    # Guards the fork-context / mirror seeding so a resumed child never seeds its
    # context twice (a second seed would clobber what the avatar already rebuilt).
    context_seeded: bool = False
    # Per-turn rendezvous: the round driver awaits ``turn_future``; the harness
    # callbacks (running in its supervisor coroutine) fill ``last_finished`` and
    # resolve the future on the RUNNING→IDLE settle.
    turn_future: asyncio.Future | None = None
    last_finished: dict | None = None
    failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one turn at a time


class AvatarSessionManager:
    """Owns the live ``TeamHarness`` per stateful session and drives their turns.

    Args:
        worker_base_spec: Base ``DeepAgentSpec`` for agent sessions (the team's
            teammate spec, or leader fallback) — same source as workers.
        human_base_spec: Base spec for human sessions (the human_agent avatar);
            ``None`` until the human path is wired.
        team_name: Team name used to namespace member ids.
        language: Prompt language hint (drives the structured-output tool i18n).
        model_resolver: Optional ``agent(model=...)`` name → ``TeamModelConfig``
            resolver (same contract as the worker backend).
        build_context: Optional leader ``BuildContext`` forwarded to each avatar.
        budget: The run's shared token ledger — every avatar bills its model
            calls to it and is cut short once it is dry, so sessions draw down
            the same pool as single-shot workers. ``None`` runs unbounded.
    """

    def __init__(
        self,
        *,
        worker_base_spec: Any = None,
        human_base_spec: Any = None,
        team_name: str = "swarmflow",
        language: str = "cn",
        model_resolver: Any = None,
        build_context: Any = None,
        t: Translator | None = None,
        messager: Any = None,
        session_id: str | None = None,
        run_id: str | None = None,
        workflow_name: str | None = None,
        on_human_prompt: Callable[[str, str, str], None] | None = None,
        on_human_replied: Callable[[str, str, str | None], None] | None = None,
        human_timeout: float | None = None,
        budget: BudgetLedger | None = None,
        workflow_budget: BudgetLedger | None = None,
        kv_cache_runtime: KVCacheRuntimeProtocol | None = None,
    ) -> None:
        self._budget = budget if budget is not None else BudgetLedger()
        self._workflow_budget = workflow_budget
        self._worker_base_spec = worker_base_spec
        self._human_base_spec = human_base_spec
        self._team_name = team_name
        self._language = language
        self._model_resolver = model_resolver
        self._build_context = build_context
        self._t = t if t is not None else make_translator(language if language in ("cn", "en") else "cn")
        self._sessions: dict[str, _SessionState] = {}
        self._counter = 0
        # Human turn rendezvous: correlation_id -> future awaiting the person's
        # raw reply. Instance-scoped (no process-global registry); cancelled on
        # aclose. Outbound prompt signal goes through ``on_human_prompt``; the
        # inbound reply arrives on the dedicated messager topic (subscribed lazily
        # on the first human session) and is routed by ``_on_reply_event``.
        self._pending_human: dict[str, asyncio.Future] = {}
        # Human replies that arrive during a pause window (no live future, so the
        # usual path would drop them) are parked here and consumed by
        # ``_await_human_reply`` before it registers a new future.
        self._pending_reply_buffer: dict[str, str] = {}
        self._on_human_prompt = on_human_prompt
        self._on_human_replied = on_human_replied
        self._human_timeout = human_timeout if human_timeout is not None else _DEFAULT_HUMAN_TIMEOUT
        self._messager = messager
        self._session_id = session_id
        # Scopes the reply topic so concurrent runs don't cross-resolve.
        # None falls back to the legacy session+team scope.
        self._run_id = run_id
        self._workflow_name = workflow_name
        self._kv_cache_runtime = kv_cache_runtime
        self._reply_topic_subscribed = False

    # ------------------------------------------------------------------
    # Engine session-backend surface (delegated from TeamWorkerBackend)
    # ------------------------------------------------------------------

    async def ensure_member_name(self, *, kind: str, opts: dict) -> str:
        """Reserve this session's member identity without building its avatar.

        Pure in-process bookkeeping (a counter increment + name mint) — no
        harness, no LLM, no spawn/budget slot. Called on a session's first turn
        regardless of cache hit so a fully-hit resume still knows the member
        name ``capture_fork`` needs to locate the parent's persisted context.
        """
        return self._next_member_name(kind, opts)

    async def open_session(
        self,
        *,
        kind: str,
        instructions: str | None,
        opts: dict,
        fork_data: dict | None = None,
        member_name: str | None = None,
    ) -> str:
        """Build the avatar harness for a session and start it.

        ``member_name`` is the identity already reserved by :meth:`ensure_member_name`
        on the first turn — reused here so we never re-mint a name (which would
        drift the counter across a resume). When ``None`` (human sessions, or a
        backend invoked directly) a fresh name is minted.
        """
        base = self._human_base_spec if kind == "human" else self._worker_base_spec
        if base is None:
            raise BackendError(f"no base spec available for {kind!r} sessions")
        if member_name is None:
            member_name = self._next_member_name(kind, opts)
        state = _SessionState(
            kind=kind,
            spec_base=base,
            instructions=instructions,
            member_name=member_name,
            budget_rail=SwarmflowBudgetRail(self._budget, workflow_budget=self._workflow_budget),
            session_id=self._derive_avatar_session_id(member_name),
        )
        self._sessions[member_name] = state
        if kind == "human":
            await self._ensure_reply_subscription()
        await self._start_avatar(state, opts, fork_data)
        return member_name

    async def capture_fork(self, session_id: str, *, keep_rounds: int | None, fork_mode: str) -> dict | None:
        """Eagerly snapshot a session's context per ``fork_mode`` / ``keep_rounds``.

        Called at ``AgentSession.fork()`` time so the parent's context is frozen
        at the fork point. ``session_id`` is the parent's reserved member name.

        Two capture sources, in order of preference:
        1. a **live** parent avatar (in this run's ``_sessions``) — snapshotted
           from its native context, which carries ToolMessage;
        2. the parent's **persisted** ``state["context"]`` (after a fully-hit
           resume the avatar was never rebuilt, so there is no live native) —
           recovered from the checkpointer by the reserved member name, also
           carrying ToolMessage.

        Returns a serializable ``fork_data`` dict for injection into a fresh
        child session, or ``None`` when neither source is available — the engine
        then falls back to its history mirror (degraded, no ToolMessage).

        ``keep_rounds`` is the split point in **rounds** (each prior ``send()``
        is one round). The engine requires it for every mode but ``full``;
        a value beyond the parent's actual round count is not an error — a
        warning is logged and the fork silently degrades to full-context,
        mirroring the team fork's "wrong name → full-context" guard.
        """
        state = self._sessions.get(session_id)
        if state is not None and state.harness is not None:
            native = state.harness.get_deep_agent()
            return self._fork_data_from_native(native, session_id, fork_mode, keep_rounds)

        # No live parent avatar (fully-hit resume): recover from the persisted
        # session context via the reserved member name.
        persisted = await self._persisted_messages(session_id)
        if persisted is None:
            return None
        fork_ctx = self._fork_ctx_from_messages(persisted, session_id, fork_mode, keep_rounds)
        if fork_ctx is None:
            return None
        return {
            "messages": fork_ctx.messages,
            "compact_split": fork_ctx.compact_split,
            "compact_direction": fork_ctx.compact_direction,
        }

    def _fork_data_from_native(self, native, member_name: str, fork_mode: str, keep_rounds: int | None) -> dict:
        """Build fork_data from a live parent avatar's native context."""
        from openjiuwen.agent_teams.fork import ForkContext

        if fork_mode == "full":
            fork_ctx = ForkContext.from_agent(native)
        elif fork_mode in ("before", "after"):
            idx = _round_boundary_index(
                native.get_current_context(),
                keep_rounds,
                keep_after=(fork_mode == "after"),
            )
            if idx is None:
                self._warn_out_of_range(member_name, fork_mode, keep_rounds)
                fork_ctx = ForkContext.from_agent(native)  # out-of-range → full
            else:
                fork_ctx = ForkContext.from_agent(
                    native,
                    checkpoint=idx,
                    keep="after" if fork_mode == "after" else "before",
                )
        else:  # compact modes: full capture + mark split; compaction at inject time
            fork_ctx = ForkContext.from_agent(native)
            idx = _round_boundary_index(
                native.get_current_context(),
                keep_rounds,
                keep_after=False,
            )
            if idx is None:
                self._warn_out_of_range(member_name, fork_mode, keep_rounds)
                fork_ctx.compact_split = len(fork_ctx.messages)
            else:
                fork_ctx.compact_split = idx
            fork_ctx.compact_direction = "after" if fork_mode == "keep_before_compact_after" else "before"
        return {
            "messages": fork_ctx.messages,
            "compact_split": fork_ctx.compact_split,
            "compact_direction": fork_ctx.compact_direction,
        }

    def _fork_ctx_from_messages(self, msgs, member_name: str, fork_mode: str, keep_rounds: int | None):
        """Build fork_data from a persisted message list (no live native)."""
        from openjiuwen.agent_teams.fork import ForkContext

        fork_ctx = ForkContext(messages=[encode_message(m) for m in msgs])
        if fork_mode == "full":
            return fork_ctx
        idx = _round_boundary_index(msgs, keep_rounds, keep_after=(fork_mode == "after"))
        if idx is None:
            self._warn_out_of_range(member_name, fork_mode, keep_rounds)
            return fork_ctx  # full fallback
        if fork_mode in ("before", "after"):
            if fork_mode == "after":
                fork_ctx.messages = fork_ctx.messages[idx:]
            else:
                fork_ctx.messages = fork_ctx.messages[:idx]
        else:  # compact: mark split on the full capture, compaction at inject time
            fork_ctx.compact_split = idx
            fork_ctx.compact_direction = "after" if fork_mode == "keep_before_compact_after" else "before"
        return fork_ctx

    async def _persisted_messages(self, member_name: str) -> list | None:
        """Recover a parent avatar's persisted conversation from the checkpointer.

        After a fully-hit resume the parent avatar is never rebuilt, so there is
        no live native to snapshot. The checkpointer still holds its final
        ``state["context"]`` (committed at the prior run's teardown). Recover it
        by constructing a *standalone* session that reuses the parent's stable
        ``session_id`` and ``{team}_{member}`` card id (so ``pre_agent_execute``
        hits the same AgentStorage bucket) and running ``pre_run`` — which drives
        the checkpointer's restore (and also fires ``AGENT_SESSION_CREATED``);
        no harness, no LLM, no supervisor.
        """
        from openjiuwen.agent_teams.fork import ForkContext
        from openjiuwen.core.session.agent import create_agent_session
        from openjiuwen.core.single_agent import AgentCard

        member_name = member_name or self._next_member_name("agent", {})
        fixed_id = self._derive_avatar_session_id(member_name)
        try:
            # The real avatar's card.id is ``{team_name}_{member_name}``
            # (derive_member_spec, _member_spec.py:48) — that is the agent_id
            # the checkpointer keys AgentStorage by. The recovery card must
            # match it, or pre_agent_execute cannot locate the persisted state.
            sess = create_agent_session(
                session_id=fixed_id,
                card=AgentCard(id=f"{self._team_name}_{member_name}", name=member_name),
            )
            await sess.pre_run()
            states = sess.get_state("context")
        except Exception:  # noqa: BLE001 - best-effort persisted recovery
            team_logger.debug("[swarmflow] fork persisted recovery failed for %s", member_name, exc_info=True)
            return None
        if not isinstance(states, dict):
            return None
        ctx_state = states.get("default_context_id")
        if not isinstance(ctx_state, dict):
            return None
        messages = ctx_state.get("messages")
        if not isinstance(messages, list):
            return None
        return ForkContext.normalize_messages(messages)

    @staticmethod
    def _warn_out_of_range(session_id: str, fork_mode: str, keep_rounds: int | None) -> None:
        """Log when keep_rounds has no matching round (degrades to full-context fork)."""
        team_logger.warning(
            "[swarmflow] fork out-of-range: session %s fork_mode=%s keep_rounds=%r has no "
            "matching round; falling back to a full-context fork",
            session_id,
            fork_mode,
            keep_rounds,
        )

    def _derive_avatar_session_id(self, member_name: str) -> str:
        """Stable, unique child session id for an avatar.

        ``{team}/{workflow}/{member}`` — stable across a same-process resume (so
        ``pre_run`` / ``_read_persisted_messages`` can locate the prior run's
        saved ``state["context"]``) and unique across sessions (``member_name``
        is already unique per manager, ``workflow_name`` separates different
        scripts running under the same session).
        """
        wf = self._workflow_name or "workflow"
        return f"{self._team_name}/{wf}/{member_name}"

    async def _ensure_reply_subscription(self) -> None:
        """Subscribe (once) to the dedicated human-reply topic for this run.

        Lazy — only a run that actually opens a human session subscribes; the
        topic is run-scoped (session + team) and distinct from the team topic, so
        it never collides with the leader's subscription on the same messager.
        """
        if self._reply_topic_subscribed or self._messager is None or self._session_id is None:
            return
        from openjiuwen.agent_teams.schema.events import swarmflow_human_reply_topic

        topic = swarmflow_human_reply_topic(self._session_id, self._team_name, self._run_id)
        await self._messager.subscribe(topic, self._on_reply_event)
        self._reply_topic_subscribed = True

    async def _on_reply_event(self, message: Any) -> None:
        """Messager handler: route a ``WORKFLOW_HUMAN_REPLY`` to its pending turn."""
        from openjiuwen.agent_teams.schema.events import TeamEvent

        if getattr(message, "event_type", None) != TeamEvent.WORKFLOW_HUMAN_REPLY:
            return
        payload = getattr(message, "payload", None) or {}
        corr = payload.get("correlation_id")
        if corr is None:
            return
        answer = payload.get("answer")
        self.submit_human_reply(corr, "" if answer is None else str(answer))

    async def send_turn(
        self,
        session_id: str,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        *,
        history: Sequence[dict] = (),
        correlation_id: str | None = None,
    ) -> AgentResult:
        """Advance one turn on a session (serialised per session by its lock).

        ``history`` is the engine-side conversation mirror. On the live
        (cold-run) path the avatar harness keeps its own context across rounds,
        so it is unused — except as a **degraded seed** for a fork child whose
        backend snapshot was unavailable (``fork_data`` was ``None``): the
        first turn rebuilds a ``(user, assistant)`` conversation from the mirror
        so the child still has something to work on. ``correlation_id`` is the
        engine's deterministic id for a human turn (matches a person's reply).
        """
        state = self._sessions.get(session_id)
        if state is None:
            raise BackendError(f"unknown session {session_id!r}")
        async with state.lock:
            await self._seed_mirror_fallback(state, history)
            if state.kind == "human":
                return await self._human_turn(state, prompt, opts, schema_json, correlation_id)
            return await self._agent_turn(state, prompt, schema_json)

    async def _seed_mirror_fallback(self, state: _SessionState, history: Sequence[dict]) -> None:
        """Seed a fork child's context from the history mirror (degraded path).

        Only fires when the child was opened with no ``fork_data`` (the backend
        could not capture the parent's context) **and** no fork context was
        seeded, and the mirror actually carries prior turns. This is a fallback,
        not the primary path: a mirror rebuild has no ToolMessage and loses the
        KV prefix, so it only keeps the fork meaningful when nothing better is
        available. Seeding happens once (``context_seeded`` guards double-writes
        — a second seed would clobber what the avatar rebuilt).
        """
        if state.turns_executed != 0 or state.context_seeded or not history:
            return
        state.context_seeded = True
        messages: list[Any] = []
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if role == "user":
                messages.append(UserMessage(content=content))
            elif role == "assistant":
                messages.append(AssistantMessage(content=content))
        if not messages:
            return
        native = state.harness.get_deep_agent()
        child_sid = native.session_id  # bound child session, set by harness.start
        await native.create_new_context_engine(session_id=child_sid, messages=messages)
        team_logger.info(
            "[swarmflow] session %s seeded context from history mirror (%d messages)",
            state.member_name,
            len(messages),
        )

    async def close_session(self, session_id: str) -> None:
        """Dispose one session's avatar and drop its row (idempotent)."""
        state = self._sessions.pop(session_id, None)
        if state is None or state.harness is None:
            return
        session = state.harness.current_session()
        release_kvc = getattr(session, "release_kvc", None)
        try:
            await cancellation_safe_release_then_dispose(
                release_kvc=release_kvc if callable(release_kvc) else None,
                dispose=state.harness.dispose,
                owner_id=session_id,
            )
        finally:
            kv_cache_harness_session_lifecycle_hook.clear_harness_session_hooks(state.harness)

    async def aclose(self) -> None:
        """Cancel pending human waits, unsubscribe, and dispose every session."""
        for fut in list(self._pending_human.values()):
            if not fut.done():
                fut.cancel()
        self._pending_human.clear()
        self._pending_reply_buffer.clear()
        if self._reply_topic_subscribed and self._messager is not None and self._session_id is not None:
            from openjiuwen.agent_teams.schema.events import swarmflow_human_reply_topic

            try:
                await self._messager.unsubscribe(
                    swarmflow_human_reply_topic(self._session_id, self._team_name, self._run_id)
                )
            except Exception:
                team_logger.debug("[swarmflow] human-reply unsubscribe failed")
            self._reply_topic_subscribed = False
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)

    async def abort_all(self) -> None:
        """Hard-abort every live session's in-flight round (pause path).

        Both agent and human sessions run a supervisor-mode avatar harness, so
        each is stopped via ``TeamHarness.abort(immediate=True)`` (cancel the
        scheduler task, roll back to the round baseline). A human session may
        instead be blocked in ``_await_human_reply`` waiting on a person, so its
        pending future is cancelled first. The interrupted turn never journals,
        so a resume reruns it (a human turn's ``correlation_id`` is stable across
        resume, so a person's reply still matches). Avatars are disposed by
        ``aclose`` on the run's unwind; an aborted-but-not-disposed harness is
        harmless — resume rebuilds fresh avatars, and journal-hit turns build none.
        """
        for fut in list(self._pending_human.values()):
            if not fut.done():
                fut.cancel()
        self._pending_human.clear()
        self._pending_reply_buffer.clear()
        for state in list(self._sessions.values()):
            if state.harness is not None:
                try:
                    await state.harness.abort(immediate=True)
                except Exception:  # noqa: BLE001 - best effort during pause
                    team_logger.debug("[swarmflow] session abort failed for %s", state.member_name)

    def submit_human_reply(self, correlation_id: str, answer: str) -> bool:
        """Resolve a pending human turn with the person's raw reply.

        The inbound seam: whatever transport carries a real person's answer
        (messager round-trip from ``interact_agent_team``) calls this with the
        ``correlation_id`` from the outbound prompt. A live pending future is
        resolved directly; a reply for an unknown / paused correlation (no live
        future) is buffered instead of dropped, so a resume that re-awaits the
        turn still sees the person's answer.
        """
        fut = self._pending_human.get(correlation_id)
        if fut is not None and not fut.done():
            fut.set_result(answer)
            return True
        # No live future: buffer instead of drop (pause window or late reply).
        self._pending_reply_buffer[correlation_id] = answer
        team_logger.info("[swarmflow] buffered reply for pending correlation_id %r", correlation_id)
        return True

    # ------------------------------------------------------------------
    # Avatar lifecycle
    # ------------------------------------------------------------------

    async def _start_avatar(self, state: _SessionState, opts: dict, fork_data: dict | None = None) -> None:
        """Build the session's ``TeamHarness`` and start its supervisor once.

        ``fork_data`` (from :meth:`capture_fork`) seeds a fork child's context
        right after start — the child's context is only lazily materialised on
        its first model call, so it must be injected into the context engine
        here (via ``create_new_context_engine`` with the child's bound session
        id), before that call ever happens. ``compact_split`` / direction then
        drive ``compact_context`` on the child's own native.
        """
        from openjiuwen.agent_teams.harness.team_harness import TeamHarness

        model = self._resolve_model(opts.get("model"))
        spec = derive_member_spec(
            state.spec_base,
            team_name=self._team_name,
            member_name=state.member_name,
            system_prompt=self._session_system_prompt(state),
            model=model,
            description="swarmflow session",
        )
        build_context = derive_member_build_context(
            self._build_context,
            team_name=self._team_name,
            member_name=state.member_name,
            language=self._language,
        )
        harness = None
        try:
            harness = TeamHarness.build(
                agent_spec=spec,
                role=TeamRole.WORKER,
                member_name=state.member_name,
                build_context=build_context,
            )
            # Meter the avatar for its whole life: an ``agent_session`` keeps its
            # harness across turns, so the run's ceiling has to bind every one of
            # them, not just the turn that happens to cross it.
            harness.add_rail(state.budget_rail)
            # End each schema turn's round as soon as structured_output is
            # captured (added before start so it registers with the harness).
            harness.add_rail(StructuredOutputFinishRail())
            # Cold start: bind a stable child session (F_37 decision 3) so the
            # avatar's DeepAgentState / context persist across this session's
            # turns *and* across a same-process resume (pre_run recovery needs a
            # deterministic session_id to locate the prior run's state).
            team_session = self._team_session_for(state)
            kv_cache_harness_session_lifecycle_hook.configure_harness_session_hooks(
                harness,
                product_session_id=self._session_id,
                evict_on_finish=False,
            )
            await harness.start(team_session=team_session)
            if fork_data is not None and fork_data.get("messages"):
                await self._seed_fork_context(harness, state, fork_data)
            await harness.subscribe(
                on_state=self._make_state_cb(state),
                on_round=self._make_round_cb(state),
            )
        except Exception as e:
            if harness is not None:
                kv_cache_harness_session_lifecycle_hook.clear_harness_session_hooks(harness)
            team_logger.exception("[swarmflow] session avatar build/start failed for %s", state.member_name)
            raise BackendError(f"session avatar build/start failed for {state.member_name}: {e}") from e
        state.harness = harness

    def _team_session_for(self, state: _SessionState) -> Any:
        """Build the team session carrying the avatar's stable child session id."""
        from openjiuwen.core.session.agent_team import Session as TeamSession

        return TeamSession(
            session_id=state.session_id,
            team_id=self._team_name,
            kv_cache_runtime=self._kv_cache_runtime,
        )

    async def _seed_fork_context(self, harness: Any, state: _SessionState, fork_data: dict) -> None:
        """Inject the fork snapshot into the child avatar's context engine.

        Two writes, so the fork context survives the child's first ``_init_context``:
        the context engine is seeded with the fork messages, **and** the messages are
        written back to the child session's ``state["context"]`` (overwriting whatever
        ``pre_run`` recovered from the checkpointer). Without the second write,
        ``_init_context`` hits the pool and ``_load_state_from_session`` re-loads the
        stale prior context, clobbering the injected fork messages.
        """
        from openjiuwen.agent_teams.fork import ForkContext
        from openjiuwen.agent_teams.fork_compact import compact_context

        native = harness.get_deep_agent()
        child_sid = native.session_id  # bound child session, set by harness.start
        fork_ctx = ForkContext(messages=fork_data["messages"])
        await native.create_new_context_engine(
            session_id=child_sid,
            messages=fork_ctx.to_messages(),
        )
        compact_split = fork_data.get("compact_split")
        if compact_split is not None:
            await compact_context(
                native,
                split_at=compact_split,
                session_id=child_sid,
                direction=fork_data.get("compact_direction") or "before",
            )
        # Persist the (possibly compacted) messages back to the child session's
        # state so the child's first model call loads the fork context, not the
        # prior run's stale context recovered by pre_run.
        child = harness.current_session()
        if child is not None:
            try:
                final_msgs = native.get_current_context(session_id=child_sid)
            except Exception:  # noqa: BLE001 - best-effort; keep the injected pool context
                final_msgs = None
            if final_msgs is not None:
                child.update_state({"context": {"default_context_id": {"messages": final_msgs}}})
        state.context_seeded = True

    @staticmethod
    def _make_state_cb(state: _SessionState):
        """Callback resolving the turn future when the harness settles to IDLE.

        Runs inside the harness supervisor coroutine, so it stays cheap: a
        RUNNING→IDLE transition means this turn (and any task-loop continuation
        rounds) finished — hand the last finished round's result to the waiter.
        """

        async def on_state(*, old: Any, new: Any, session_id: Any) -> None:
            if new is not HarnessState.IDLE:
                return
            fut = state.turn_future
            if fut is not None and not fut.done():
                state.turn_future = None
                fut.set_result(state.last_finished)

        return on_state

    @staticmethod
    def _make_round_cb(state: _SessionState):
        """Callback caching each round's outcome (the last one wins per turn)."""

        async def on_round(*, kind: str, round_id: int, result: Any) -> None:
            if kind == "finished":
                state.last_finished = result
            elif kind == "failed":
                state.failed = True

        return on_round

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def _agent_turn(self, state: _SessionState, prompt: str, schema_json: dict | None) -> AgentResult:
        """Drive one agent-session round; capture structured output when requested."""
        submit: StructuredOutputTool | None = None
        turn_prompt = prompt
        tokens_before = state.budget_rail.call_tokens
        if schema_json is not None:
            # Mount the capture tool only for this turn; the harness is IDLE
            # between turns so add/remove is safe. The ability manager owner-
            # qualifies the id, so concurrent sessions never collide.
            submit = StructuredOutputTool(schema_json, self._t)
            state.harness.add_tool(submit)
            turn_prompt = f"{prompt}\n\n{_SCHEMA_TURN_NUDGE}"
        try:
            result = await self._drive_round(state, turn_prompt)
        finally:
            if submit is not None:
                try:
                    state.harness.remove_tool("structured_output")
                except Exception:
                    team_logger.debug("[swarmflow] structured_output detach failed for %s", state.member_name)

        state.turns_executed += 1
        turn_tokens = state.budget_rail.call_tokens - tokens_before

        try:
            self._raise_on_interrupt_or_fail(state, result)
        except BackendError as e:
            # Surface this turn's burned tokens on the failure so the run's
            # token sum does not drop a budget-exhausted/failed session turn.
            if e.tokens is None:
                e.tokens = turn_tokens or None
            raise

        if submit is not None:
            if not (submit.called and submit.captured is not None):
                raise BackendError(
                    f"session '{state.member_name}' did not submit a structured result via structured_output",
                    tokens=turn_tokens or None,
                )
            # Prefer free-text narration from the round; fall back to JSON capture.
            text = prefer_natural_or_structured_text(_output_text(result), submit.captured)
            return AgentResult(
                text=text,
                structured=submit.captured,
                tokens=turn_tokens,
            )
        text = _output_text(result)
        return AgentResult(text=text, tokens=turn_tokens)

    @staticmethod
    async def _drive_round(state: _SessionState, prompt: str) -> dict | None:
        """Send one round and await the harness settling back to IDLE."""
        loop = asyncio.get_running_loop()
        state.last_finished = None
        state.failed = False
        # Hold the future locally: the IDLE callback nulls ``state.turn_future``
        # when it resolves, so we must await our own reference, not the slot.
        fut: asyncio.Future = loop.create_future()
        state.turn_future = fut
        await state.harness.send(prompt, immediate=False)
        return await fut

    @staticmethod
    def _raise_on_interrupt_or_fail(state: _SessionState, result: Any) -> None:
        """Reject a failed or HITL-interrupted round rather than return a partial."""
        if state.failed:
            raise BackendError(f"session '{state.member_name}' round failed")
        if isinstance(result, dict) and result.get("result_type") == "interrupt":
            # A swarmflow session is a request/response turn; an avatar that pauses
            # for human-in-the-loop input mid-turn is not yet supported. Surface it
            # loudly instead of returning a half-finished turn.
            # TODO(future feature): drive avatar-internal human interaction here.
            team_logger.error(
                "[swarmflow] session '%s' raised a HITL interrupt; avatar-internal "
                "human interaction is a future feature",
                state.member_name,
            )
            raise BackendError(f"session '{state.member_name}' interrupted (avatar HITL not supported)")

    async def _human_turn(
        self,
        state: _SessionState,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        correlation_id: str | None,
    ) -> AgentResult:
        """Human-session turn: push the question to a person, format their reply.

        Push the question out (so a UI can surface it), wait for the person's raw
        reply, then drive the avatar harness to render that reply into the turn's
        answer (structured when a schema is requested). A timeout / no answer
        yields ``skipped`` so the engine returns ``None`` for the turn.
        """
        raw = await self._await_human_reply(state, prompt, opts, correlation_id)
        if raw is None:  # timed out / no answer
            return AgentResult(skipped=True)
        # NEW: classify intent before formatting (best-effort; any failure
        # degrades to the existing formatting path rather than failing the turn).
        try:
            intent = await self._classify_intent(raw, prompt)
        except Exception:
            team_logger.debug("[swarmflow] intent classify raised; degrade to continue")
            intent = None
        if intent and intent.get("intent") == "edit_rerun":
            raise WorkflowAborted(
                reason="early_return",
                reply=raw,
                edit_hints=intent.get("edit_instructions"),
            )
        format_prompt = (
            f"You put this question to the person:\n{prompt}\n\n"
            f"The person replied:\n{raw}\n\n"
            "Render their reply faithfully into the answer for this turn; do not "
            "add anything they did not express."
        )
        # The avatar (human stand-in) formats the raw reply; reuse the agent turn
        # path so schema capture / round settling work identically.
        return await self._agent_turn(state, format_prompt, schema_json)

    async def _classify_intent(self, raw: str, prompt: str) -> dict | None:
        """One-shot TinyAgent classification: is this reply 'edit & rerun' or 'continue'?

        Reuses the ``worker_base_spec.model`` already resolved on the base spec (the
        path ``TeamWorkerBackend._sessions()`` takes). Ephemeral: async with
        auto-dispose. Returns None when there is no base model or on any failure
        (degrades to existing formatting path).
        """
        from openjiuwen.agent_teams.tiny_agent import TinyAgent
        from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness.prompts import PromptMode

        base_model = self._worker_base_spec.model if self._worker_base_spec else None
        if base_model is None:
            return None
        user_prompt = f"Question:\n{prompt}\n\nReply:\n{raw}"
        try:
            # Build a TinyAgent straight from the already-resolved TeamModelConfig,
            # skipping the name → resolver hop entirely.
            spec = DeepAgentSpec(
                card=AgentCard(id="intent-classifier", name="intent-classifier", description="tiny agent"),
                system_prompt=_INTENT_CLASSIFY_PROMPT,
                model=base_model,
                tools=None,
                auto_create_workspace=False,
                max_iterations=3,
                enable_security_rail=False,
                language=self._language,
                enable_read_image_multimodal=False,
                prompt_mode=PromptMode.NONE.value,
                enable_sys_operation=False,
            )
            classifier = TinyAgent(spec, default_schema=_INTENT_SCHEMA, language=self._language, budget=self._budget)
            async with classifier:
                return await classifier.run(
                    user_prompt,
                    schema=_INTENT_SCHEMA,
                )
        except Exception:
            team_logger.debug("[swarmflow] intent classify failed; degrade to continue")
            return None

    async def _await_human_reply(
        self,
        state: _SessionState,
        prompt: str,
        opts: dict,
        correlation_id: str | None,
    ) -> str | None:
        """Register a pending turn, signal the prompt out, await the person's reply.

        Returns the raw reply text, or ``None`` on timeout. Only the manager's own
        ``wait_for`` timeout is caught — an outer cancellation (e.g. the engine's
        per-call ``timeout``) propagates so it is handled where it belongs.

        ``correlation_id`` is the engine's deterministic id for this turn; a
        person's reply carries it back. It is stable across a resume, so a reply
        issued for an interrupted-then-resumed turn still matches.
        """
        corr = correlation_id or f"{state.member_name}:{state.turns_executed}"
        # Consume a buffered reply first (don't re-push the prompt).
        buffered = self._pending_reply_buffer.pop(corr, None)
        if buffered is not None:
            if self._on_human_replied is not None:
                try:
                    self._on_human_replied(state.member_name, corr, buffered)
                except Exception:
                    team_logger.debug("[swarmflow] human-replied notify failed for %s", state.member_name)
            return buffered
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_human[corr] = fut
        if self._on_human_prompt is not None:
            try:
                self._on_human_prompt(state.member_name, corr, prompt)
            except Exception:
                team_logger.debug("[swarmflow] human-prompt notify failed for %s", state.member_name)
        timeout = opts.get("timeout") or self._human_timeout
        try:
            raw = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            team_logger.warning(
                "[swarmflow] human session %s timed out after %ss waiting for a reply",
                state.member_name,
                timeout,
            )
            return None
        finally:
            self._pending_human.pop(corr, None)
        if self._on_human_replied is not None:
            try:
                self._on_human_replied(state.member_name, corr, raw)
            except Exception:
                team_logger.debug("[swarmflow] human-replied notify failed for %s", state.member_name)
        return raw

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _session_system_prompt(state: _SessionState) -> str:
        """Compose the avatar's system prompt: role prompt + caller instructions."""
        base = _SESSION_SYS_PROMPT_HUMAN if state.kind == "human" else _SESSION_SYS_PROMPT_AGENT
        if state.instructions:
            return f"{base}\n\n{state.instructions}"
        return base

    def _resolve_model(self, model_name: str | None) -> Any:
        """Resolve an ``agent(model=...)`` hint to a config (same as the worker)."""
        if self._model_resolver is None:
            return None
        return self._model_resolver(model_name) if model_name else self._model_resolver(None)

    def _next_member_name(self, kind: str, opts: dict) -> str:
        """Mint a unique, pattern-valid session member name from the call label.

        ``wf-sess-<label-slug>-<n>`` (or ``wf-human-...``) — lowercase ASCII with a
        leading letter, so it satisfies member-name routing constraints. ``n`` is a
        per-manager counter; the synchronous read-increment keeps it collision-free
        under the engine's concurrent fan-out.
        """
        n = self._counter
        self._counter += 1
        label = str(opts.get("label") or kind)
        slug = _SLUG_RE.sub("-", label.lower()).strip("-") or kind
        prefix = "wf-human" if kind == "human" else "wf-sess"
        return f"{prefix}-{slug}-{n}"


def _output_text(result: Any) -> str:
    """Extract the final output text from a round result dict."""
    if isinstance(result, dict):
        err = result.get("error")
        if err or result.get("result_type") == "error":
            msg = str(err or result.get("output") or "session turn failed")
            raise BackendError(msg)
        return str(result.get("output", ""))
    return str(result or "")


def _round_boundary_index(msgs: Sequence[Any], keep_rounds: int | None, keep_after: bool) -> int | None:
    """Map a round-based split to a message index, or ``None`` when out of range.

    ``keep_after=False`` (the "before" family): the boundary is the index just
    past the ``keep_rounds``-th ``UserMessage`` (its assistant reply and any
    closing ToolMessage travel with it, so the kept head is not cut mid-call).

    ``keep_after=True`` (the "after" family): the boundary is the index of the
    ``keep_rounds``-th ``UserMessage`` itself (the tail from that round on is
    kept, and ``ForkContext``'s ``_trim_leading_orphan_tool_messages`` drops any
    leading orphan ToolMessages whose assistant is not inherited).

    A ``None`` ``keep_rounds`` or a value larger than the actual round count
    returns ``None`` — the caller silently falls back to a full-context fork
    (mirroring the team fork's "wrong name silently falls back to full" guard).
    """
    if keep_rounds is None:
        return None
    count = 0
    for i, m in enumerate(msgs):
        if not isinstance(m, UserMessage):
            continue
        count += 1
        if count == keep_rounds:
            if keep_after:
                return i
            for j in range(i + 1, len(msgs)):
                if isinstance(msgs[j], UserMessage):
                    return j
            return len(msgs)
    return None


__all__ = ["AvatarSessionManager"]
