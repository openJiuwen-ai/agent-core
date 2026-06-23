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

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.workflow.backends._member_spec import (
    derive_member_build_context,
    derive_member_spec,
)
from openjiuwen.agent_teams.workflow.backends.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Multi-turn persona — the session counterpart of the worker's single-shot prompt.
_SESSION_SYS_PROMPT_AGENT = (
    "You are a stateful swarmflow session agent in a multi-turn conversation. "
    "You remember every prior turn; answer each new message directly and "
    "concisely, using the accumulated context. Do not restate the whole history."
)
# Persona for a human session's avatar: it does NOT invent answers — it renders a
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


@dataclass
class _SessionState:
    """One live session row (instance-scoped; no process-global state)."""

    kind: str  # "agent" | "human"
    spec_base: Any  # base DeepAgentSpec this session derives from
    instructions: str | None
    member_name: str
    harness: Any = None  # TeamHarness, built lazily by open_session
    turns_executed: int = 0
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
        on_human_prompt: Callable[[str, str, str], None] | None = None,
        on_human_replied: Callable[[str, str], None] | None = None,
        human_timeout: float | None = None,
    ) -> None:
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
        self._on_human_prompt = on_human_prompt
        self._on_human_replied = on_human_replied
        self._human_timeout = human_timeout if human_timeout is not None else _DEFAULT_HUMAN_TIMEOUT
        self._messager = messager
        self._session_id = session_id
        self._reply_topic_subscribed = False

    # ------------------------------------------------------------------
    # Engine session-backend surface (delegated from TeamWorkerBackend)
    # ------------------------------------------------------------------

    async def open_session(self, *, kind: str, instructions: str | None, opts: dict) -> str:
        """Mint a member identity, build the avatar harness, and start it."""
        base = self._human_base_spec if kind == "human" else self._worker_base_spec
        if base is None:
            raise BackendError(f"no base spec available for {kind!r} sessions")
        member_name = self._next_member_name(kind, opts)
        state = _SessionState(
            kind=kind,
            spec_base=base,
            instructions=instructions,
            member_name=member_name,
        )
        self._sessions[member_name] = state
        if kind == "human":
            await self._ensure_reply_subscription()
        await self._start_avatar(state, opts)
        return member_name

    async def _ensure_reply_subscription(self) -> None:
        """Subscribe (once) to the dedicated human-reply topic for this run.

        Lazy — only a run that actually opens a human session subscribes; the
        topic is run-scoped (session + team) and distinct from the team topic, so
        it never collides with the leader's subscription on the same messager.
        """
        if self._reply_topic_subscribed or self._messager is None or self._session_id is None:
            return
        from openjiuwen.agent_teams.schema.events import swarmflow_human_reply_topic

        topic = swarmflow_human_reply_topic(self._session_id, self._team_name)
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

        ``history`` is unused on the live (cold-run) path — the avatar harness
        keeps its own context across rounds. It is the seam a later stage uses to
        rebuild context after a partial-hit resume. ``correlation_id`` is the
        engine's deterministic id for a human turn (matches a person's reply).
        """
        state = self._sessions.get(session_id)
        if state is None:
            raise BackendError(f"unknown session {session_id!r}")
        async with state.lock:
            if state.kind == "human":
                return await self._human_turn(state, prompt, opts, schema_json, correlation_id)
            return await self._agent_turn(state, prompt, schema_json)

    async def close_session(self, session_id: str) -> None:
        """Dispose one session's avatar and drop its row (idempotent)."""
        state = self._sessions.pop(session_id, None)
        if state is None or state.harness is None:
            return
        try:
            await state.harness.dispose()
        except Exception:
            team_logger.debug("[swarmflow] session dispose failed for %s", session_id)

    async def aclose(self) -> None:
        """Cancel pending human waits, unsubscribe, and dispose every session."""
        for fut in list(self._pending_human.values()):
            if not fut.done():
                fut.cancel()
        self._pending_human.clear()
        if self._reply_topic_subscribed and self._messager is not None and self._session_id is not None:
            from openjiuwen.agent_teams.schema.events import swarmflow_human_reply_topic

            try:
                await self._messager.unsubscribe(swarmflow_human_reply_topic(self._session_id, self._team_name))
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
        ``correlation_id`` from the outbound prompt. An unknown / already-resolved
        correlation is rejected (returns ``False``) — an illegal id from an
        external caller is dropped, not applied to some other turn.
        """
        fut = self._pending_human.get(correlation_id)
        if fut is None or fut.done():
            team_logger.warning(
                "[swarmflow] rejected human reply for unknown/closed correlation_id %r",
                correlation_id,
            )
            return False
        fut.set_result(answer)
        return True

    # ------------------------------------------------------------------
    # Avatar lifecycle
    # ------------------------------------------------------------------

    async def _start_avatar(self, state: _SessionState, opts: dict) -> None:
        """Build the session's ``TeamHarness`` and start its supervisor once."""
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
        try:
            harness = TeamHarness.build(
                agent_spec=spec,
                role=TeamRole.WORKER,
                member_name=state.member_name,
                build_context=build_context,
            )
            # End each schema turn's round as soon as structured_output is
            # captured (added before start so it registers with the harness).
            harness.add_rail(StructuredOutputFinishRail())
            # Cold start: the harness creates and owns its child session, so
            # DeepAgentState / context persist across this session's turns.
            await harness.start()
            await harness.subscribe(
                on_state=self._make_state_cb(state),
                on_round=self._make_round_cb(state),
            )
        except Exception as e:
            team_logger.exception("[swarmflow] session avatar build/start failed for %s", state.member_name)
            raise BackendError(f"session avatar build/start failed for {state.member_name}: {e}") from e
        state.harness = harness

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
        self._raise_on_interrupt_or_fail(state, result)

        if submit is not None:
            if not (submit.called and submit.captured is not None):
                raise BackendError(
                    f"session '{state.member_name}' did not submit a structured result via structured_output"
                )
            return AgentResult(
                structured=submit.captured,
                tokens=_estimate_tokens(prompt, submit.captured),
            )
        text = _output_text(result)
        return AgentResult(text=text, tokens=_estimate_tokens(prompt, text))

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
            raise BackendError(
                f"session '{state.member_name}' interrupted (avatar HITL not supported)"
            )

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
        format_prompt = (
            f"You put this question to the person:\n{prompt}\n\n"
            f"The person replied:\n{raw}\n\n"
            "Render their reply faithfully into the answer for this turn; do not "
            "add anything they did not express."
        )
        # The avatar (human persona) formats the raw reply; reuse the agent turn
        # path so schema capture / round settling work identically.
        return await self._agent_turn(state, format_prompt, schema_json)

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
                self._on_human_replied(state.member_name, corr)
            except Exception:
                team_logger.debug("[swarmflow] human-replied notify failed for %s", state.member_name)
        return raw

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _session_system_prompt(state: _SessionState) -> str:
        """Compose the avatar's system prompt: role persona + caller instructions."""
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
        return str(result.get("output", ""))
    return str(result or "")


def _estimate_tokens(prompt: str, result: Any) -> int:
    """Rough token estimate for budget accounting (cf. the worker backend)."""
    import json

    try:
        payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        payload = str(result)
    return len(prompt) // 4 + len(payload) // 4


__all__ = ["AvatarSessionManager"]
