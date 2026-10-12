# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Drive an external SDK member (Claude Code / Codex) one swarmflow turn at a time.

The member runtime speaks in turn lifecycle events; swarmflow wants
"one prompt in, one result out" for both single-shot workers and stateful
sessions. This driver is that adapter, plus the two things an external
provider cannot do through harness rails: report usage in the native ledger's
per-turn shape, and capture a structured result without a submit tool.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness_protocol import HarnessState, MessageRole, TurnResult, TurnStatus, TurnUsage

# Providers whose terminal ``TurnResult.usage`` is cumulative over the session
# (their mapping emits ``UsageUpdateMode.CUMULATIVE``); everything else reports
# the turn's own total.
_CUMULATIVE_USAGE_PROVIDERS = frozenset({"claude-code"})

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class ExternalTurnOutcome:
    """One completed external turn in the shape the backends return."""

    text: str
    structured: Any = None
    tokens: int = 0
    marker: str | None = None
    """Provider-side identifier of this turn — the cut point a later fork may reference."""
    model: str | None = None
    """The model the CLI reported for this member (display-only, never journaled)."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    """Prompt / completion / prompt-cache split of ``tokens``; None when the
    CLI reported no split (the backend then keeps only the total)."""


def schema_instruction(schema_json: dict) -> str:
    """Prompt tail asking the CLI for a bare JSON object matching ``schema_json``."""
    schema = json.dumps(schema_json, ensure_ascii=False)
    return (
        "When you have the answer, finish your reply with ONE JSON object that conforms to this "
        f"JSON Schema and nothing else after it:\n{schema}"
    )


def parse_structured_text(text: str) -> Any:
    """Extract the JSON object a CLI wrote in its final answer.

    Tries, in order: the whole text, fenced ```json blocks (last first), the last
    balanced ``{...}`` span. Raises ``ValueError`` when none parses to an object.
    """
    candidates: list[str] = [text.strip()]
    fenced = _FENCED_JSON.findall(text)
    candidates.extend(reversed(fenced))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found in the reply")


def turn_marker(provider_name: str, result: TurnResult) -> str | None:
    """The provider-side id that names this turn for a later ``before`` fork.

    Codex forks by turn id; Claude forks by transcript uuid — the offline
    ``fork_session`` addresses JSONL entries and rejects the API message id, so
    a turn whose last assistant message carries no uuid has no usable cut point.
    """
    if provider_name == "codex":
        value = result.provider_data.get("native_turn_id")
        return str(value) if value else None
    if provider_name in _CUMULATIVE_USAGE_PROVIDERS:
        for message in reversed(result.messages):
            if message.role is MessageRole.ASSISTANT:
                value = message.data.get("uuid")
                return str(value) if value else None
    return None


def _opt_int(value: int | None) -> int | None:
    return int(value) if value is not None else None


class ExternalMemberTurns:
    """Turn-level façade over an ``ExternalHarnessMemberRuntime``.

    Args:
        runtime: The started-or-not member runtime to drive.
        member_name: Minted swarmflow identity, for error messages.
        auth_hint_models: Team model pool names compatible with this member's
            CLI kind, for the two-audience copy on an auth-classified failure.
            ``None``/empty means the copy degrades to "fix the CLI login".
    """

    def __init__(
        self,
        runtime: Any,
        *,
        member_name: str,
        auth_hint_models: Sequence[str] | None = None,
    ) -> None:
        self._runtime = runtime
        self._member_name = member_name
        self._auth_hint_models = auth_hint_models
        self._turn_future: asyncio.Future | None = None
        self._active_turn_id: str | None = None
        self._cumulative = runtime.provider_name in _CUMULATIVE_USAGE_PROVIDERS
        self._last_seen: dict[str, int] = {}

    @property
    def provider_name(self) -> str:
        return self._runtime.provider_name

    @property
    def reported_model(self) -> str | None:
        """The model the CLI reported for this member, if the runtime has seen one."""
        return getattr(self._runtime, "reported_model", None)

    @property
    def session_id(self) -> str | None:
        """The provider's own session / thread pointer (available once started)."""
        return self._runtime.session_id

    @property
    def member_session(self) -> Any:
        """The runtime's own child session — the only safe place to persist host state."""
        return self._runtime.member_session

    def bind_activity_sink(
        self,
        sink: Callable[[Any], None] | None,
        *,
        agent_id: str | None = None,
        phase: str | None = None,
        label: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Bind the runtime's activity sink with the swarmflow node identity.

        Passthrough to the member runtime; the backend rebinds per turn so the
        correlation id tracks the current turn.
        """
        self._runtime.bind_activity_sink(
            sink,
            agent_id=agent_id,
            phase=phase,
            label=label,
            correlation_id=correlation_id,
        )

    async def start(self, team_session: Any) -> None:
        """Start the runtime (the member session comes from ``team_session``) and listen for turn ends."""
        await self._runtime.start(team_session=team_session)
        await self._runtime.subscribe(on_state=self._on_state, on_round=self._on_round)

    async def run_turn(self, prompt: str, *, schema_json: dict | None = None) -> ExternalTurnOutcome:
        """Send one prompt and wait for its terminal turn event.

        Cancellation (engine timeout or a workflow pause) aborts the CLI turn
        before unwinding so no orphan process keeps working and billing.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._turn_future = fut
        text = prompt if schema_json is None else f"{prompt}\n\n{schema_instruction(schema_json)}"
        try:
            receipt = await self._runtime.send(text, immediate=False)
            self._active_turn_id = getattr(receipt, "turn_id", None)
            result = await fut
        except asyncio.CancelledError:
            await self.abort()
            raise
        finally:
            self._turn_future = None
            self._active_turn_id = None
        return self._outcome(result, schema_json)

    async def abort(self) -> None:
        try:
            await self._runtime.abort(immediate=True)
        except Exception:  # noqa: BLE001 - best effort while unwinding
            team_logger.debug("[swarmflow] external abort failed for %s", self._member_name, exc_info=True)

    async def stop(self) -> None:
        await self._runtime.stop()

    # ------------------------------------------------------------------

    async def _on_state(self, *, old: Any, new: Any, session_id: Any) -> None:
        if new is not HarnessState.TERMINATED:
            return
        fut = self._turn_future
        if fut is not None and not fut.done():
            fut.set_exception(BackendError(f"external member '{self._member_name}' runtime terminated mid-turn"))

    async def _on_round(self, *, kind: str, round_id: Any, result: Any) -> None:
        if kind not in ("finished", "failed", "aborted"):
            return
        if self._active_turn_id and round_id and round_id != self._active_turn_id:
            return
        fut = self._turn_future
        if fut is not None and not fut.done():
            fut.set_result(result)

    def _auth_failure_message(self, detail: str) -> str:
        """Rebuild an auth-classified failure as two-audience copy.

        The CLI's own error text (status, reason, url) stays verbatim inside the
        parenthetical so the frontend shows the real root cause; the workaround
        and the compatible pool models come last so a Leader reading the message
        still sees the fix. No compatible pool models -> the workaround degrades
        to fixing the machine's CLI login.
        """
        head = f"{self.provider_name} CLI login failed ({detail}) — environment problem, not a script error"
        names = self._auth_hint_models or ()
        if names:
            return f"{head}; workaround: add model=<pool model> to this node (compatible: {', '.join(names)})"
        return f"{head}; fix the CLI login on this machine and re-run"

    def _outcome(self, result: Any, schema_json: dict | None) -> ExternalTurnOutcome:
        if not isinstance(result, TurnResult):
            raise BackendError(f"external member '{self._member_name}' ended its turn without a result")
        tokens, in_n, out_n, cache_n = self._turn_usage(result.usage)
        if result.status is TurnStatus.FAILED:
            error = result.error
            message = error.message if error is not None else "turn failed"
            # The same classification the harness's auth fallback keys on: the
            # request never reached the model, so this is an environment problem
            # (CLI login / credentials), not a script error. Rebuild the copy for
            # two audiences — root cause first so a truncated message still names
            # the endpoint, workaround + compatible pool names last for the Leader.
            if error is not None and error.category == "auth_required":
                raise BackendError(self._auth_failure_message(message), tokens=tokens or None)
            raise BackendError(f"external member '{self._member_name}' failed: {message}", tokens=tokens or None)
        if result.status is TurnStatus.INTERRUPTED:
            raise BackendError(f"external member '{self._member_name}' turn was interrupted", tokens=tokens or None)
        text = result.final_output if isinstance(result.final_output, str) else str(result.final_output or "")
        structured = None
        if schema_json is not None:
            try:
                structured = parse_structured_text(text)
            except ValueError as e:
                raise BackendError(
                    f"external member '{self._member_name}' did not return a JSON object for the schema: {e}",
                    tokens=tokens or None,
                ) from e
        return ExternalTurnOutcome(
            text=text,
            structured=structured,
            tokens=tokens,
            marker=turn_marker(self.provider_name, result),
            input_tokens=in_n,
            output_tokens=out_n,
            cache_tokens=cache_n,
        )

    def _turn_usage(
        self, usage: TurnUsage | None
    ) -> tuple[int, int | None, int | None, int | None]:
        """This turn's total tokens plus the input / output / cache split.

        Cumulative providers report the CLI session's running totals, so every
        counter is diffed against the last turn's value; a counter that went
        backwards means the provider reset its conversation, so the whole new
        value belongs to this turn. A ``None`` split counter (provider reported
        no split) stays ``None`` so callers can tell "unreported" from zero.
        """
        if usage is None:
            return 0, None, None, None
        if not self._cumulative:
            return (
                int(usage.total_tokens or 0),
                _opt_int(usage.input_tokens),
                _opt_int(usage.output_tokens),
                _opt_int(usage.cached_input_tokens),
            )
        return (
            self._delta("total", usage.total_tokens or 0),
            self._delta("input", usage.input_tokens),
            self._delta("output", usage.output_tokens),
            self._delta("cache", usage.cached_input_tokens),
        )

    def _delta(self, key: str, value: int | None) -> int | None:
        """Per-turn share of one cumulative usage counter (see ``_turn_usage``)."""
        if value is None:
            return None
        last = self._last_seen.get(key, 0)
        share = value - last if value >= last else value
        self._last_seen[key] = value
        return max(share, 0)


__all__ = ["ExternalMemberTurns", "ExternalTurnOutcome", "parse_structured_text", "schema_instruction", "turn_marker"]
