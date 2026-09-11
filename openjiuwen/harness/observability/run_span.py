# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Root span around one single-agent run.

``OtelCallbackHandler`` skips LLM/tool span creation when no parent span exists
(see ``callback_handler._get_parent_context_for_llm_tool``). A single-agent run
sets neither a team span nor a current agent span, so without a root span zero
spans are produced even after a clean ``init_observability``. These helpers open
a root span and register it as the run's root — the same mechanism Team mode
uses internally via ``get_or_create_team_span`` — so LLM/tool spans nest under
it and are exported.

Usage (must be paired, in the same coroutine so the ContextVar propagates into
the runner's LLM calls)::

    handle = open_agent_run_span(session_id=sid, mode=mode)
    try:
        ...  # Runner.run_agent_streaming / Runner.run_agent
    finally:
        close_agent_run_span(handle, session_id=sid, output=answer)
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.execution_subject import ExecutionSubject
from openjiuwen.harness.observability.span_context import (
    register_run_root_span,
    unregister_run_root_span,
)

# Tracer name the single-agent root spans are emitted under.
_RUN_TRACER_NAME = "openjiuwen.harness.observability"

_OUTPUT_UNSET = object()


def build_run_span_name(*, mode: str, session_id: str) -> str:
    """Build a hierarchical OTel span name: ``agent.<mode>.<session_id>``.

    *mode* is the host's request mode, typically shaped ``<category>.<submode>``
    (e.g. ``agent.plan`` / ``agent.fast`` / ``code.normal``), so it yields the
    hierarchy directly::

        agent.plan  -> agent.agent.plan.<session_id>
        code.normal -> agent.code.normal.<session_id>

    Falls back gracefully when either component is empty.

    Args:
        mode: Request mode of the run; empty is allowed.
        session_id: Session the run belongs to; empty is allowed.

    Returns:
        The span name.
    """
    normalized_mode = (mode or "").strip()
    normalized_session = (session_id or "").strip()
    if not normalized_mode:
        return f"agent.run.{normalized_session}" if normalized_session else "agent.run"
    if not normalized_session:
        return f"agent.{normalized_mode}.run"
    return f"agent.{normalized_mode}.{normalized_session}"


def open_agent_run_span(
    *,
    session_id: str = "",
    mode: str = "",
    request_id: str = "",
    run_id: str = "",
    turn_id: str = "",
    turn_number: int | None = None,
    execution_subject: ExecutionSubject | None = None,
) -> Any:
    """Open the root span of a single-agent run.

    Args:
        session_id: Session the run belongs to; also keys the fallback registry.
        mode: Request mode of the run, stamped on the span and used in its name.
        request_id: Optional host Web/RPC request identity.
        run_id: Optional stable identity for this root run.
        turn_id: Optional identity for the user turn.
        turn_number: Optional known 1-based turn number.
        execution_subject: Optional concrete owner for work that runs outside
            the normal agent invoke/stream execution scope.

    Returns:
        An opaque handle to pass to :func:`close_agent_run_span`, or ``None``
        when single-agent tracing is off (in which case closing is a no-op).
    """
    try:
        from opentelemetry.trace import SpanKind

        from openjiuwen.extensions.observability.semconv import (
            GEN_AI_CONVERSATION_ID,
            GEN_AI_OPERATION_NAME,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
            OJ_AGENT_MODE,
            OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
            OJ_EXECUTION_SUBJECT_ID,
            OJ_EXECUTION_SUBJECT_KIND,
            OJ_EXECUTION_SUBJECT_PARENT_ID,
            OJ_EXECUTION_SUBJECT_SESSION_ID,
            OJ_REQUEST_ID,
            OJ_RUN_ID,
            OJ_SESSION_ID,
            OJ_TRACE_ROOT,
            OJ_TRACE_SCHEMA_VERSION,
            OJ_TRAJECTORY_RECORD_KIND,
            OJ_TURN_ID,
            OJ_TURN_NUMBER,
        )
        from openjiuwen.extensions.observability.setup import get_tracer, is_initialized
        from openjiuwen.extensions.observability.span_context import (
            set_current_session_id,
            set_root_span,
        )
        from openjiuwen.harness.observability.setup import is_tracing_enabled

        if not is_initialized():
            return None
        if not is_tracing_enabled():
            return None

        tracer = get_tracer(_RUN_TRACER_NAME)
        name = build_run_span_name(mode=mode, session_id=session_id)
        subject = execution_subject
        base_attributes: dict[str, Any] = {
            LANGFUSE_SESSION_ID: session_id or "",
            OJ_AGENT_MODE: mode or "",
            OJ_TRACE_ROOT: True,
            OJ_TRACE_SCHEMA_VERSION: "1",
            GEN_AI_OPERATION_NAME: "invoke_agent",
            OJ_TRAJECTORY_RECORD_KIND: "turn",
            LANGFUSE_OBSERVATION_TYPE: "agent",
            OJ_EXECUTION_SUBJECT_ID: subject.subject_id if subject is not None else "main",
            OJ_EXECUTION_SUBJECT_DISPLAY_NAME: (
                subject.display_name if subject is not None else "Main Agent"
            ),
            OJ_EXECUTION_SUBJECT_KIND: subject.kind if subject is not None else "main_agent",
            OJ_EXECUTION_SUBJECT_SESSION_ID: (
                subject.session_id if subject is not None and subject.session_id else session_id or ""
            ),
        }
        if subject is not None and subject.parent_subject_id:
            base_attributes[OJ_EXECUTION_SUBJECT_PARENT_ID] = subject.parent_subject_id
        if session_id:
            base_attributes[GEN_AI_CONVERSATION_ID] = session_id
            base_attributes[OJ_SESSION_ID] = session_id
        if request_id:
            base_attributes[OJ_REQUEST_ID] = request_id
        if run_id:
            base_attributes[OJ_RUN_ID] = run_id
        if turn_id:
            base_attributes[OJ_TURN_ID] = turn_id
        if turn_number is not None:
            base_attributes[OJ_TURN_NUMBER] = int(turn_number)
        span = tracer.start_span(
            name=name,
            kind=SpanKind.SERVER,
            attributes=base_attributes,
        )
        # Register as the run root so parent lookup finds it for LLM/tool span
        # creation. The session id goes into the shared registry as well as the
        # local fallback table — supervisor tasks may not inherit ContextVars.
        sid = session_id or ""
        set_root_span(span, session_id=sid)
        set_current_session_id(sid)
        register_run_root_span(span, session_id=sid)
        logger.info("[AgentObservability] root span opened: name=%s", name)
        return span
    except Exception as exc:
        logger.warning("[AgentObservability] open root span failed: %s", exc)
        return None


def stamp_run_output(handle: Any, output: Any, *, allow_empty: bool = False) -> None:
    """Write the run's final answer onto the root span as the trace output.

    Team mode fills the equivalent attribute on its ``team.<name>`` span from
    the leader's iteration result (``TeamObservabilityRail.after_task_iteration``),
    which keys off ``TeamRole.LEADER`` and therefore never fires for a single
    agent — leaving the trace with an empty top-level output. The single-agent
    counterpart is the run's final answer, stamped here.

    Redaction follows the active ``ObservabilityConfig`` so ``redact_completions``
    covers this attribute exactly as it covers llm/agent span outputs.

    Args:
        handle: The still-recording root span.
        output: Final answer text; empty means nothing to stamp.
    """
    if not output and not allow_empty:
        return
    from openjiuwen.extensions.observability.redaction import redact_completion
    from openjiuwen.extensions.observability.semconv import LANGFUSE_OBSERVATION_OUTPUT
    from openjiuwen.extensions.observability.setup import get_config

    config = get_config()
    output_text = str(output)
    text = redact_completion(output_text, config) if config else output_text
    handle.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, text)
    from openjiuwen.extensions.observability.demand import publish_span_snapshot

    publish_span_snapshot(handle, "output")


def close_agent_run_span(
    handle: Any,
    *,
    session_id: str = "",
    output: Any = _OUTPUT_UNSET,
    exception: BaseException | None = None,
    error_type: str = "",
    error_message: str = "",
) -> None:
    """End the root span opened by :func:`open_agent_run_span` and clear it.

    Args:
        handle: Opaque handle from :func:`open_agent_run_span`; None is a no-op.
        session_id: Session the run belonged to; its registry entry is dropped.
        output: The run's final answer, stamped as the trace-level output.
            Omit this argument to leave the attribute unset; an explicit empty
            string remains a real, observable result.
        exception: Optional run failure/cancellation recorded on the root.
        error_type: Optional structured terminal failure type supplied by a
            host whose protocol reports failure in-band instead of raising.
            This is recorded verbatim and never replaced with a synthetic
            exception class.
        error_message: Optional structured terminal failure description. A
            message without a type still marks the root ERROR but does not
            invent ``error.type``.
    """
    # Drop this run's fallback entry — and only this run's. Sessions overlap,
    # so clearing whatever happens to be registered would blind a run that is
    # still going (its sub-agents would lose their spans mid-run).
    unregister_run_root_span(handle, session_id=session_id)
    if handle is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        from openjiuwen.extensions.observability.semconv import (
            ERROR_TYPE,
            OJ_TRACE_COMPLETE,
            OJ_TRACE_FORCED_CLOSE,
        )
        from openjiuwen.extensions.observability.span_context import (
            cascade_close_children,
            clear_root_span,
            flush_child_spans,
        )

        # End any still-open child LLM/tool spans (e.g. run aborted mid-call).
        # Two nets are needed for the single-agent path:
        #   1. cascade_close_children — closes spans whose state was pushed on
        #      the _llm_span_stack / _tool_span_map ContextVars in THIS context.
        #   2. flush_child_spans — the SpanProcessor-backed safety net Team mode
        #      relies on (finalize_trace -> flush_child_spans via
        #      ActiveSpanTracker). The single-agent runner opens LLM spans inside
        #      its own child context, so their ContextVar state is not visible
        #      here; the tracker closes them by trace_id regardless of context.
        # Both must run BEFORE the root binding is cleared, and the flush is
        # scoped to our trace only (flush_spans_for_trace), so concurrent runs
        # are not affected.
        #
        # The root is explicitly excluded by span id, so it stays recording
        # until every child has ended. Its final record is therefore a reliable
        # completion marker for incremental trajectory consumers.
        trace_id = getattr(getattr(handle, "context", None), "trace_id", None)
        forced_close_count = 0
        try:
            forced_close_count += cascade_close_children() or 0
        except Exception as exc:
            logger.debug("[AgentObservability] cascade_close_children failed: %s", exc)
        try:
            forced_close_count += flush_child_spans(trace_id=trace_id) or 0
        except Exception as exc:
            logger.debug("[AgentObservability] flush_child_spans failed: %s", exc)
        if forced_close_count:
            handle.set_attribute(OJ_TRACE_FORCED_CLOSE, True)

        if output is not _OUTPUT_UNSET:
            try:
                stamp_run_output(handle, output, allow_empty=True)
            except Exception as exc:
                logger.debug("[AgentObservability] stamp run output failed: %s", exc)

        if exception is not None:
            try:
                handle.record_exception(exception)
            except Exception as exc:
                logger.debug("[AgentObservability] record root exception failed: %s", exc)
        if exception is not None:
            # A raised exception is the authoritative failure fact. Structured
            # fields exist for protocols that report terminal failures in-band;
            # they must never relabel or rewrite a real exception.
            resolved_error_type = type(exception).__name__
            resolved_error_message = str(exception)
        else:
            resolved_error_type = (error_type or "").strip()
            resolved_error_message = (error_message or "").strip()
        if resolved_error_type or resolved_error_message or exception is not None:
            if resolved_error_type:
                handle.set_attribute(ERROR_TYPE, resolved_error_type)
            handle.set_status(
                Status(
                    StatusCode.ERROR,
                    resolved_error_message or resolved_error_type,
                )
            )
        else:
            handle.set_status(Status(StatusCode.OK))
        handle.set_attribute(OJ_TRACE_COMPLETE, True)
        try:
            handle.end()
        except Exception as exc:
            logger.debug("[AgentObservability] end root span failed: %s", exc)
        try:
            clear_root_span(session_id=session_id or "", expected_span=handle)
        except Exception as exc:
            logger.debug("[AgentObservability] clear_root_span failed: %s", exc)
        clear_root_span()
    except Exception as exc:
        logger.warning("[AgentObservability] close root span failed: %s", exc)


__all__ = [
    "build_run_span_name",
    "close_agent_run_span",
    "open_agent_run_span",
    "stamp_run_output",
]
