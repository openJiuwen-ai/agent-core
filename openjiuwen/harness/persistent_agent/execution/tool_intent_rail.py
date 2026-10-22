# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Record Persistent tool intent before side effects and outcomes after dispatch."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent
from openjiuwen.harness.rails.base import DeepAgentRail

from ..schema.events import EventType
from ..tools.effect_policy import is_read_only_tool_name
from .tool_outcome import record_tool_completion

if TYPE_CHECKING:
    from ..schema.build_context import ConstructionBinding

_CALL_STATE_KEY = "_persistent_tool_intent_state"
_SUMMARY_LIMIT = 500
_AFTER_TOOL_READER_OFFSET = 1_000_000


class PersistentToolIntentRail(DeepAgentRail):
    """Gate side-effecting Persistent tools on their durable intent event."""

    # Run after confirmation/security guards and after the model rail's stop check.
    priority = 5

    def __init__(self, binding: "ConstructionBinding"):
        super().__init__()
        self._binding = binding

    def callback_priority(self, event: AgentCallbackEvent) -> int:
        """Gate after permission callbacks, but capture completion before result rewrites."""
        if event == AgentCallbackEvent.AFTER_TOOL_CALL:
            return self.priority + _AFTER_TOOL_READER_OFFSET
        return self.priority

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record a write intent before allowing the native ability manager to dispatch."""
        if self._must_not_dispatch(ctx):
            return

        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = getattr(tool_call, "id", None)
        tool_name = getattr(ctx.inputs, "tool_name", None)
        if not self._is_identifier(tool_call_id) or not self._is_identifier(tool_name):
            self._fail_closed(ctx, "tool call identity is missing or invalid")

        tool_args = getattr(ctx.inputs, "tool_args", None)
        try:
            params_digest = self._params_digest(tool_args)
            args_summary = self._args_summary(tool_args)
        except Exception as exc:
            self._fail_closed(ctx, "tool arguments could not be canonicalized", exc)

        read_only = is_read_only_tool_name(tool_name)
        target_ref = self._target_ref(tool_args)
        prepared_target_ref = ctx.extra.get("_persistent_tool_target_ref")
        if self._is_identifier(prepared_target_ref):
            target_ref = prepared_target_ref
        prepared_summary = ctx.extra.get("_persistent_tool_args_summary")
        if isinstance(prepared_summary, str) and prepared_summary:
            args_summary = prepared_summary[:_SUMMARY_LIMIT]
        state = self._state(ctx)
        if state is not None:
            if (
                state["tool_call_id"] != tool_call_id
                or state["tool_name"] != tool_name
                or state["params_digest"] != params_digest
            ):
                self._fail_closed(ctx, "tool call identity changed during retry")
            # A retry of an already-intended write reuses its stable operation ID.
            state["dispatched"] = True
            return

        if read_only:
            ctx.extra[_CALL_STATE_KEY] = self._new_state(
                tool_call_id, tool_name, params_digest, args_summary, None, True
            )
            return

        control = self._binding.control
        emitter = control.emitter
        if emitter is None:
            self._fail_closed(ctx, "write tool has no Persistent event emitter")

        operation_id = self._operation_id(tool_call_id)
        try:
            await emitter.emit(
                EventType.TOOL_INTENT,
                {
                    "operation_id": operation_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "target_ref": target_ref,
                    "params_digest": params_digest,
                    "args_summary": args_summary,
                },
            )
        except Exception:
            # EventEmitter marks critical failures fatal. Preserve that error and
            # never turn it into an ordinary tool failure or a retryable result.
            if control.fatal is None:
                control.mark_fatal(
                    build_error(
                        StatusCode.DEEPAGENT_RUNTIME_ERROR,
                        error_msg="critical tool intent recording failed",
                    )
                )
            # BEFORE_TOOL_CALL callback exceptions are logged and swallowed by
            # the callback framework. The force-finish signal is what prevents
            # the rail-wrapped method (and therefore the tool side effect) from
            # running after that callback returns.
            ctx.request_force_finish(
                {
                    "result_type": "persistent_pause",
                    "reason": "error",
                    "detail": "tool intent persistence failed",
                    "output": "",
                }
            )
            raise

        # Tool calls may be dispatched concurrently. A sibling can fail its
        # critical intent write (or accept a stop) while this call waits on the
        # emitter's ordering lock, so re-check execution control at the actual
        # dispatch boundary as well.
        if control.fatal is not None or control.abort_reason is not None:
            ctx.request_force_finish(
                {
                    "result_type": "persistent_pause",
                    "reason": "error",
                    "detail": "execution stopped before tool dispatch",
                    "output": "",
                }
            )
            return
        if ctx.has_force_finish_request:
            return

        # This marker is the permission to let the wrapped ability method run.
        ctx.extra[_CALL_STATE_KEY] = self._new_state(
            tool_call_id, tool_name, params_digest, args_summary, operation_id, True
        )

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        """Allow retries only for explicit idempotent cards, after shared retry rails run."""
        if self._binding.control.fatal is not None or not self._is_explicitly_idempotent(ctx):
            # ToolCallResilienceRail (priority 70) runs before this Persistent-only
            # callback (priority 5), so its pending request is visible here.
            ctx.consume_retry_request()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record one final outcome for a call that passed the dispatch barrier."""
        state = self._state(ctx)
        emitter = self._binding.control.emitter
        if state is None or not state["dispatched"] or state["completed"] or emitter is None:
            return
        state["completed"] = True

        result = getattr(ctx.inputs, "tool_result", None)
        exception = ctx.exception
        if exception is not None:
            status = "unknown" if state["operation_id"] is not None else "failed"
            reason = self._exception_reason(exception, status)
        elif self._is_explicit_failure(result):
            status = "failed"
            reason = "tool returned an explicit failure result"
        else:
            status = "succeeded"
            reason = None

        # Retention uses the actual final dispatch result, never success prose in a transcript.
        # Bind the receipt to the result before later rails may replace its model-visible text.
        call = getattr(ctx.inputs, "tool_call", None)
        if (
            state["operation_id"] is not None
            and call is not None
            and call.id == state["tool_call_id"]
            and call.name == state["tool_name"]
            and self._params_digest(call.arguments) == state["params_digest"]
        ):
            record_tool_completion(
                getattr(ctx.inputs, "tool_msg", None), call, result, exception, state["args_summary"]
            )

        try:
            result_summary = self._result_summary(result, exception)
        except Exception as exc:
            logger.warning(
                "[PersistentAgent] tool result summary unavailable: tool=%s error=%s",
                state["tool_name"],
                type(exc).__name__,
            )
            result_summary = "Tool result summary unavailable"

        try:
            await emitter.emit(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": state["tool_call_id"],
                    "tool_name": state["tool_name"],
                    "args_summary": state["args_summary"],
                    "status": status,
                    "result_summary": result_summary,
                    "operation_id": state["operation_id"],
                    "reason": reason,
                },
            )
        except Exception as exc:
            # Completion is an ordinary event: its failure must not replace the
            # already determined native tool result.
            logger.warning(
                "[PersistentAgent] tool completion event could not be built: tool=%s error=%s",
                state["tool_name"],
                type(exc).__name__,
            )

    def _must_not_dispatch(self, ctx: AgentCallbackContext) -> bool:
        """Honor earlier interrupt, skip, fatal, and abort decisions."""
        control = self._binding.control
        if control.fatal is not None or control.abort_reason is not None or ctx.has_force_finish_request:
            return True
        if ctx.extra.get("_skip_tool"):
            return True
        tool_call = getattr(ctx.inputs, "tool_call", None)
        call_id = getattr(tool_call, "id", None)
        skip_calls = ctx.extra.get("_skip_tool_calls")
        return isinstance(skip_calls, dict) and bool(skip_calls.get(call_id))

    def _fail_closed(
        self,
        ctx: AgentCallbackContext,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        """Preserve a fatal guard error and force the current native loop to stop."""
        error = build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, cause=cause, error_msg=message)
        self._binding.control.mark_fatal(error)
        ctx.request_force_finish(
            {
                "result_type": "persistent_pause",
                "reason": "error",
                "detail": "tool call blocked by Persistent execution policy",
                "output": "",
            }
        )
        raise error

    def _operation_id(self, tool_call_id: str) -> str:
        execution = self._binding.execution
        identity = {
            "namespace_id": execution.namespace_id,
            "agent_id": execution.agent_id,
            "session_id": execution.session_id,
            "request_id": execution.request_id,
            "tool_call_id": tool_call_id,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "op_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _params_digest(arguments: Any) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                # Keep malformed raw strings distinct without echoing them into events.
                pass
        canonical = json.dumps(
            arguments if arguments is not None else {},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _args_summary(cls, arguments: Any) -> str:
        from .entries import redact_arguments

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return "Arguments omitted because they are not valid JSON"
        summary = redact_arguments(arguments)
        return summary[:_SUMMARY_LIMIT] or "{}"

    @staticmethod
    def _target_ref(arguments: Any) -> str | None:
        """Use only an explicit target_ref argument; never infer a target from prose."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return None
        candidate = arguments.get("target_ref") if isinstance(arguments, dict) else None
        if not isinstance(candidate, str) or not candidate or any(char.isspace() for char in candidate):
            return None
        return candidate if len(candidate) <= 512 else None

    @staticmethod
    def _new_state(tool_call_id, tool_name, params_digest, args_summary, operation_id, dispatched):
        return {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "params_digest": params_digest,
            "args_summary": args_summary,
            "operation_id": operation_id,
            "dispatched": dispatched,
            "completed": False,
        }

    @staticmethod
    def _state(ctx) -> dict | None:
        state = ctx.extra.get(_CALL_STATE_KEY)
        return state if isinstance(state, dict) else None

    @staticmethod
    def _is_explicitly_idempotent(ctx: AgentCallbackContext) -> bool:
        """Only a registered ToolCard with the literal idempotent=True is retry-safe."""
        tool_name = getattr(ctx.inputs, "tool_name", None)
        ability_manager = getattr(ctx.agent, "ability_manager", None)
        if not tool_name or ability_manager is None:
            return False
        ability = ability_manager.get(tool_name)
        from openjiuwen.core.foundation.tool import ToolCard

        return isinstance(ability, ToolCard) and ability.idempotent is True

    @staticmethod
    def _is_identifier(value: Any) -> bool:
        return isinstance(value, str) and bool(value) and not any(char.isspace() for char in value)

    @staticmethod
    def _is_explicit_failure(result: Any) -> bool:
        if isinstance(result, dict):
            return result.get("success") is False
        return getattr(result, "success", None) is False

    @staticmethod
    def _exception_reason(exception: BaseException, status: str) -> str:
        if status == "unknown":
            return f"{type(exception).__name__}: external side-effect outcome is unknown"
        return f"{type(exception).__name__}: read-only tool call failed"

    @classmethod
    def _result_summary(cls, result: Any, exception: BaseException | None) -> str:
        from .entries import redact_arguments

        if exception is not None:
            # Tool results on exception paths may contain arbitrary transport
            # text (including echoed credentials); never persist that text.
            summary = f"Tool raised {type(exception).__name__}; result content omitted"
        elif result is None:
            summary = "Tool returned no result"
        elif isinstance(result, str):
            try:
                decoded = json.loads(result)
            except json.JSONDecodeError:
                summary = f"Text result omitted ({len(result)} characters)"
            else:
                summary = redact_arguments(decoded)
        elif isinstance(result, (dict, list, tuple)):
            summary = redact_arguments(result)
        elif hasattr(result, "model_dump"):
            summary = redact_arguments(result.model_dump())
        elif isinstance(result, (bool, int, float)):
            summary = str(result)
        else:
            summary = f"Tool returned {type(result).__name__}"
        return summary[:_SUMMARY_LIMIT] or "Tool returned an empty result"


__all__ = ["PersistentToolIntentRail"]
