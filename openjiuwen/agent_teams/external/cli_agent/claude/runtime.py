# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime implementation backed by Claude Agent SDK."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import aclosing, nullcontext
from dataclasses import dataclass
import json
from typing import Any, AsyncIterator, Awaitable, Callable, ContextManager, Optional

from openjiuwen.agent_teams.external.cli_agent.claude.failure_classifier import (
    classify_assistant_error,
    classify_claude_exception,
    classify_result_message,
)
from openjiuwen.agent_teams.external.cli_agent.claude.options import build_claude_options, load_claude_sdk
from openjiuwen.agent_teams.external.cli_agent.claude.sdk_mcp import (
    ClaudeSdkMcpToolSet,
    build_claude_sdk_mcp_tool_set,
)
from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.stream.base import OutputSchema


@dataclass(frozen=True, slots=True)
class _ClaudeToolMetadata:
    """Metadata retained until the matching Claude tool result arrives."""

    tool_name: str = ""
    is_team_tool: bool = False


class _ClaudeStderrTail:
    """Capture a bounded tail of Claude CLI stderr for crash diagnostics."""

    def __init__(self, *, max_lines: int = 40, max_line_chars: int = 2000, max_chars: int = 8000) -> None:
        """Initialize the bounded stderr tail collector."""
        self._max_line_chars = max_line_chars
        self._max_chars = max_chars
        self._lines: deque[str] = deque(maxlen=max_lines)

    def append(self, line: str) -> None:
        """Append one stderr line after trimming excessive content."""
        text = str(line).strip()
        if not text:
            return
        if len(text) > self._max_line_chars:
            text = text[: self._max_line_chars] + "...[truncated]"
        self._lines.append(text)
        while len(self.render()) > self._max_chars and self._lines:
            self._lines.popleft()

    def render(self) -> str:
        """Return the currently captured stderr tail."""
        return "\n".join(self._lines)


class _ClaudeAuthFallbackRequested(RuntimeError):
    """Signal that the outer driver should retry once with fallback options."""


class ClaudeSdkRuntime(CliRuntimeBase):
    """Drive a Claude Code member through Claude Agent SDK."""

    def __init__(
        self,
        *,
        member_name: str,
        options: Any,
        fallback_options: Any | None = None,
        promote_fallback_model: Callable[[], Awaitable[bool]] | None = None,
        transport: Any | None = None,
        inject_mcp: bool = True,
        mcp_server_name: str = "openjiuwen-team",
        member_agent_id: str | None = None,
        team_context_tracker: Any = None,
        span_bridge: Any | None = None,
    ):
        """Bind SDK options; the SDK client is connected on start."""
        super().__init__(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
        )
        self._options = options
        self._fallback_options = fallback_options
        self._promote_fallback_model = promote_fallback_model
        self._fallback_activated = False
        self._transport = transport
        self._inject_mcp = inject_mcp
        self._mcp_server_name = mcp_server_name
        self._sdk_mcp_tool_set: ClaudeSdkMcpToolSet | None = None
        self._client: Any | None = None
        self._abort_requested = False
        self._tool_metadata_by_id: dict[str, _ClaudeToolMetadata] = {}
        self._span_bridge = span_bridge or _NoopClaudeSpanBridge()
        self._stderr_tail = _ClaudeStderrTail()
        self._install_stderr_callback()
        # Reliability context; injected via bind_reliability_context.
        self._reliability_ctx: Any = None

    def bind_reliability_context(
        self,
        *,
        session_id: str,
        team_backend: Any,
        leader_name: str,
        update_status_cb: Any,
        messager: Any,
    ) -> None:
        """Bind the reliability failure/retry delivery surface."""
        from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext

        self._reliability_ctx = RuntimeReliabilityContext(
            member_name=self._member_name,
            team_name=team_backend.team_name if team_backend is not None else "",
            session_id=session_id,
            agent_kind="claude",
            message_manager=team_backend.message_manager if team_backend is not None else None,
            messager=messager,
            leader_name=leader_name,
            update_status_cb=update_status_cb,
            span_bridge=self._span_bridge,
        )

    def _install_stderr_callback(self) -> None:
        """Attach a Claude SDK stderr callback without dropping a caller callback."""
        original = getattr(self._options, "stderr", None)

        def _capture_stderr(line: str) -> None:
            self._stderr_tail.append(line)
            if callable(original):
                original(line)

        self._options.stderr = _capture_stderr

    async def _connect_client(self, client: Any) -> None:
        """Connect a Claude SDK client and log stderr tail on failure."""
        try:
            await client.connect()
        except Exception:
            stderr_tail = self._stderr_tail.render()
            if stderr_tail:
                team_logger.error(
                    "[{}] Claude CLI stderr before connect failure:\n{}",
                    self._member_name,
                    stderr_tail,
                )
            raise

    def bind_team_tools(
        self,
        *,
        team_backend: Any,
        role: str,
        teammate_mode: str,
        dispatch_mode: str,
        lifecycle: str,
        language: str,
        workspace_manager: Any = None,
        on_teammate_created: Any = None,
        model_config_allocator: Any = None,
        parent_agent: Any = None,
        messager: Any = None,
        team_name: str = "default",
        swarmflow_model_resolver: Any = None,
        swarmflow_worker_base_spec: Any = None,
        swarmflow_human_base_spec: Any = None,
        concurrency_governor: Any = None,
        swarmflow_budget: Any = None,
        team_permissions_enabled: bool = False,
    ) -> None:
        """Bind team tools to the owning TeamAgent shell."""
        if not self._inject_mcp:
            return
        if self._sdk_mcp_tool_set is not None:
            return
        tool_set = build_claude_sdk_mcp_tool_set(
            server_name=self._mcp_server_name,
            team_backend=team_backend,
            role=role,
            teammate_mode=teammate_mode,
            dispatch_mode=dispatch_mode,
            lifecycle=lifecycle,
            language=language,
            workspace_manager=workspace_manager,
            on_teammate_created=on_teammate_created,
            model_config_allocator=model_config_allocator,
            parent_agent=parent_agent,
            messager=messager,
            team_name=team_name,
            swarmflow_model_resolver=swarmflow_model_resolver,
            swarmflow_worker_base_spec=swarmflow_worker_base_spec,
            swarmflow_human_base_spec=swarmflow_human_base_spec,
            concurrency_governor=concurrency_governor,
            swarmflow_budget=swarmflow_budget,
            team_permissions_enabled=team_permissions_enabled,
            span_bridge=self._span_bridge,
        )
        self._sdk_mcp_tool_set = tool_set
        self._options.mcp_servers = {self._mcp_server_name: tool_set.server}
        if self._fallback_options is not None:
            self._fallback_options.mcp_servers = {self._mcp_server_name: tool_set.server}

    async def _activate_auth_fallback(self) -> bool:
        """Switch an unauthenticated native client to its persisted fallback model."""
        if self._fallback_activated or self._fallback_options is None or self._promote_fallback_model is None:
            return False
        fallback_client = None
        original_options = self._options
        fallback_session_id = self._fallback_options.resume or self._fallback_options.session_id
        session_mode = "resume" if self._fallback_options.resume else "new"
        team_logger.info(
            "[external-cli] member {} activating Claude authentication fallback session_mode={} session_id={}",
            self._member_name,
            session_mode,
            fallback_session_id,
        )
        try:
            old_client = self._client
            if old_client is not None:
                await old_client.disconnect()
                team_logger.info(
                    "[external-cli] member {} disconnected native Claude client before authentication fallback",
                    self._member_name,
                )
            self._client = None
            self._options = self._fallback_options
            self._install_stderr_callback()
            sdk = load_claude_sdk()
            fallback_client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
            await self._connect_client(fallback_client)
            team_logger.info(
                "[external-cli] member {} connected Claude authentication fallback client",
                self._member_name,
            )
            promoted = await self._promote_fallback_model()
        except Exception:
            self._options = original_options
            self._client = None
            self._install_stderr_callback()
            team_logger.exception(
                "[external-cli] member {} failed to activate Claude authentication fallback",
                self._member_name,
            )
            return False
        if not promoted:
            team_logger.warning(
                "[external-cli] member {} connected Claude authentication fallback but failed to persist it",
                self._member_name,
            )
            await fallback_client.disconnect()
            self._options = original_options
            self._install_stderr_callback()
            return False
        self._client = fallback_client
        self._fallback_activated = True
        team_logger.info("[external-cli] member {} activated Claude authentication fallback", self._member_name)
        return True

    async def start(self, *, team_session: Any | None = None) -> None:
        """Start the SDK client and initialize Claude's streaming protocol."""
        await super().start(team_session=team_session)
        if self._inject_mcp and self._sdk_mcp_tool_set is None:
            team_logger.warning("[{}] Claude SDK MCP is enabled but no team tools were bound", self._member_name)
        if self._reliability_ctx is not None:
            self._reliability_ctx.begin_attempt(phase="startup", round_id=None)
        sdk = load_claude_sdk()
        self._client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
        try:
            await self._connect_client(self._client)
        except BaseException as exc:
            await self._finalize_startup_failure(exc)
            raise

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        query = inputs.get("query")
        text = query if isinstance(query, str) else str(query)
        self._abort_requested = False
        self._tool_metadata_by_id.clear()
        for _ in range(2):
            try:
                async with aclosing(self._drive_once(text)) as attempt_stream:
                    async for chunk in attempt_stream:
                        yield chunk
            except _ClaudeAuthFallbackRequested:
                continue
            return

    async def _drive_once(self, text: str) -> AsyncIterator[Any]:
        """Run one Claude SDK turn attempt and request at most one outer retry."""
        client = self._client
        if client is None:
            sdk = load_claude_sdk()
            client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
            self._client = client
            await self._connect_client(client)
        self._span_bridge.start_turn(prompt=text)
        # One reliability attempt per turn.
        if self._reliability_ctx is not None:
            self._reliability_ctx.begin_attempt(phase="turn", round_id=self._current_round_id)
        status = "ok"
        error: BaseException | None = None
        retry_with_fallback = False
        deferred_auth_chunks: list[OutputSchema] = []
        try:
            await client.query(text)
            chunk_index = 0
            async for message in client.receive_response():
                if self._abort_requested:
                    team_logger.debug("[{}] claude sdk turn aborted", self._member_name)
                    status = "cancelled"
                    return
                # Classify structured failure signals before chunk conversion.
                # AssistantMessage.error records a pending candidate;
                # ResultMessage.is_error finalizes it.
                auth_diagnostic = self._reliability_ctx is not None and self._is_auth_diagnostic_message(message)
                finalize_payload = self._classify_sdk_message(message)
                if finalize_payload is not None:
                    category, reason, summary = finalize_payload
                    if category == "auth_required" and chunk_index == 0 and await self._activate_auth_fallback():
                        retry_with_fallback = True
                        status = "cancelled"
                        break
                    for chunk in deferred_auth_chunks:
                        team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                        self._span_bridge.record_chunk(chunk)
                        yield chunk
                        chunk_index = chunk.index + 1
                    deferred_auth_chunks.clear()
                    await self._reliability_ctx.finalize_failure(
                        category=category,
                        reason=reason,
                        summary=summary,
                    )
                    # Turn terminal failure delivered; end the generator cleanly
                    # so _drive_turn maps it onto a failed round.
                    return
                if deferred_auth_chunks:
                    for chunk in deferred_auth_chunks:
                        team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                        self._span_bridge.record_chunk(chunk)
                        yield chunk
                        chunk_index = chunk.index + 1
                    deferred_auth_chunks.clear()
                chunks = _iter_sdk_chunks(
                    message,
                    chunk_index,
                    self._tool_metadata_by_id,
                    mcp_server_name=self._mcp_server_name,
                    team_tool_names=set(self._sdk_mcp_tool_set.tools) if self._sdk_mcp_tool_set is not None else set(),
                )
                if auth_diagnostic:
                    deferred_auth_chunks.extend(chunks)
                    continue
                for chunk in chunks:
                    team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                    self._span_bridge.record_chunk(chunk)
                    yield chunk
                    chunk_index = chunk.index + 1
            if self._abort_requested:
                status = "cancelled"
        except GeneratorExit:
            status = "cancelled"
            raise
        except BaseException as exc:
            error = exc
            if self._abort_requested or isinstance(exc, asyncio.CancelledError):
                status = "cancelled"
            else:
                ctx = self._reliability_ctx
                pending_auth = ctx is not None and ctx.has_pending and ctx.pending_category == "auth_required"
                if pending_auth and chunk_index == 0 and await self._activate_auth_fallback():
                    retry_with_fallback = True
                    status = "cancelled"
                else:
                    for chunk in deferred_auth_chunks:
                        team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                        self._span_bridge.record_chunk(chunk)
                        yield chunk
                        chunk_index = chunk.index + 1
                    deferred_auth_chunks.clear()
                    status = "failed"
                    await self._finalize_turn_failure(exc)
                    raise exc
        finally:
            self._span_bridge.finish_turn(status=status, error=error)
        if retry_with_fallback:
            raise _ClaudeAuthFallbackRequested()

    @staticmethod
    def _is_auth_diagnostic_message(message: Any) -> bool:
        """Return whether an assistant message carries a structured authentication error."""
        sdk = load_claude_sdk()
        if not isinstance(message, sdk.AssistantMessage) or not message.error:
            return False
        category, _ = classify_assistant_error(message.error)
        return category == "auth_required"

    def _classify_sdk_message(
        self,
        message: Any,
    ) -> Optional[tuple[str, Any, str]]:
        """Record a candidate or describe the Claude SDK terminal failure.

        ``AssistantMessage.error`` is a candidate: recorded as pending, returns
        ``None`` (no finalize yet). ``ResultMessage`` with ``is_error=True`` is
        the turn terminal state: returns a ``(category, reason, summary)``
        tuple for the caller to finalize.
        """
        ctx = self._reliability_ctx
        if ctx is None:
            return None
        sdk = load_claude_sdk()
        if isinstance(message, sdk.AssistantMessage):
            if message.error:
                category, reason = classify_assistant_error(message.error)
                ctx.record_pending(category=category, reason=reason)
            return None
        if isinstance(message, sdk.ResultMessage) and message.is_error:
            category, reason = classify_result_message(message)
            # Merge the terminal state's structured fields with the pending
            # candidate; keep the candidate category when the terminal state
            # carries no mappable api_error_status.
            if ctx.has_pending and (reason.http_status is None or category == "sdk_error"):
                if ctx.pending_category is not None and ctx.pending_category != "sdk_error":
                    category = ctx.pending_category
            ctx.record_pending(category=category, reason=reason)
            summary = _claude_failure_summary(message, ctx.pending_reason)
            return category, ctx.pending_reason or reason, summary
        return None

    async def _finalize_turn_failure(self, exc: BaseException) -> None:
        """Finalize a Claude SDK/CLI exception, merging any pending candidate."""
        ctx = self._reliability_ctx
        if ctx is None or ctx.has_finalized:
            return
        category, reason = classify_claude_exception(exc, phase="turn")
        if ctx.has_pending:
            # Keep the pending structured signal; enrich reason with exc text.
            category = ctx.pending_category if ctx.pending_category is not None else category
            reason = ctx.pending_reason or reason
        await ctx.finalize_failure(
            category=category,
            reason=reason,
            summary=f"{self._member_name} Claude SDK turn failed: {type(exc).__name__}",
        )

    async def _finalize_startup_failure(self, exc: BaseException) -> None:
        """Finalize and surface a Claude startup failure (member → ERROR)."""
        from openjiuwen.agent_teams.schema.external_runtime_reliability import (
            ExternalRuntimeFailureReason,
        )

        ctx = self._reliability_ctx
        if ctx is None or ctx.has_finalized:
            return
        category, reason = classify_claude_exception(exc, phase="startup")
        stderr_tail = self._stderr_tail.render()
        if stderr_tail:
            reason = ExternalRuntimeFailureReason(
                message=f"{reason.message}\n{stderr_tail}" if reason.message else stderr_tail,
                sdk_error_type=reason.sdk_error_type,
                sdk_error_code=reason.sdk_error_code,
                http_status=reason.http_status,
            )
        await ctx.finalize_failure(
            category=category,
            reason=reason,
            summary=f"{self._member_name} Claude SDK startup failed: {type(exc).__name__}",
        )
        await ctx.mark_member_error()

    async def steer(self, content: str) -> None:
        """Send content into the active Claude SDK conversation."""
        if self._client is None:
            return
        await self._client.query(content)

    async def follow_up(self, content: str) -> None:
        """Send follow-up content into the active Claude SDK conversation."""
        await self.steer(content)

    async def _abort_turn(self) -> None:
        """Interrupt the in-flight Claude turn if the SDK client is connected."""
        self._abort_requested = True
        if self._client is not None:
            await self._client.interrupt()

    async def aclose(self) -> None:
        """Disconnect the SDK client. Idempotent."""
        if self._client is None:
            self._sdk_mcp_tool_set = None
            return
        client = self._client
        self._client = None
        try:
            await client.disconnect()
        finally:
            self._sdk_mcp_tool_set = None


def build_claude_runtime(
    *,
    member_name: str,
    cwd: str | None,
    add_dirs: tuple[str, ...],
    env: dict[str, str],
    cli_path: str | None = None,
    external_model_config: ExternalCliModelConfig | None = None,
    fallback_external_model_config: ExternalCliModelConfig | None = None,
    promote_fallback_model: Callable[[], Awaitable[bool]] | None = None,
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    system_prompt: str | None,
    ssh_transport: SshTransportConfig | None,
    team_session_id: str | None,
    resume_external_backend: bool,
    member_agent_id: str | None = None,
    team_context_tracker: Any = None,
    team_name: str | None = None,
    role: str | None = None,
) -> ClaudeSdkRuntime:
    """Build a Claude SDK runtime, using an SSH SDK transport when configured."""
    _ = mcp_server_command
    options = build_claude_options(
        cwd=cwd,
        add_dirs=add_dirs,
        env=env,
        cli_path=cli_path,
        external_model_config=external_model_config,
        system_prompt=system_prompt,
        team_session_id=team_session_id,
        member_name=member_name,
        resume_external_backend=resume_external_backend,
    )
    fallback_options = None
    if external_model_config is None and fallback_external_model_config is not None:
        fallback_options = build_claude_options(
            cwd=cwd,
            add_dirs=add_dirs,
            env=env,
            cli_path=cli_path,
            external_model_config=fallback_external_model_config,
            system_prompt=system_prompt,
            team_session_id=team_session_id,
            member_name=member_name,
            resume_external_backend=True,
        )
    transport = None
    if ssh_transport is not None:
        team_logger.info("[external-cli] using claude sdk ssh transport for member {}", member_name)
        transport = build_claude_sdk_ssh_transport(prompt=_empty_prompt(), options=options, config=ssh_transport)
    span_bridge = _build_claude_span_bridge(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=team_session_id,
        role=role,
    )
    return ClaudeSdkRuntime(
        member_name=member_name,
        options=options,
        fallback_options=fallback_options,
        promote_fallback_model=promote_fallback_model,
        transport=transport,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
        member_agent_id=member_agent_id,
        team_context_tracker=team_context_tracker,
        span_bridge=span_bridge,
    )


async def _empty_prompt() -> AsyncIterator[dict[str, Any]]:
    """Provide an empty streaming prompt for SDK transport construction."""
    return
    yield {}  # type: ignore[unreachable]


class _NoopClaudeSpanBridge:
    """Runtime-local no-op bridge for optional observability dependencies."""

    @staticmethod
    def start_turn(**_: Any) -> None:
        """Ignore turn start."""

    @staticmethod
    def record_chunk(_: OutputSchema) -> None:
        """Ignore one Claude stream chunk."""

    @staticmethod
    def finish_turn(*, status: str, error: Any | None = None) -> None:
        """Ignore turn completion."""

    @staticmethod
    def tool_execution_context() -> ContextManager[None]:
        """Return a no-op context for local tool execution."""
        return nullcontext()


def _build_claude_span_bridge(
    *,
    member_name: str,
    member_agent_id: str | None,
    team_name: str | None,
    session_id: str | None,
    role: str | None = None,
) -> Any:
    """Build the optional Claude OTel bridge without making runtime import depend on OTel."""
    try:
        from openjiuwen.agent_teams.observability.setup import is_initialized
    except ImportError:
        return _NoopClaudeSpanBridge()
    if not is_initialized():
        return _NoopClaudeSpanBridge()
    try:
        from openjiuwen.agent_teams.observability.claude import ClaudeSpanBridge
    except ImportError as exc:
        team_logger.warning("[{}] Claude observability bridge unavailable: {}", member_name, exc)
        return _NoopClaudeSpanBridge()
    return ClaudeSpanBridge.build(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=session_id,
        role=role,
    )


def _claude_failure_summary(result: Any, reason: Any) -> str:
    """Build a one-line failure summary from a Claude ResultMessage terminal state.

    Prefers the structured ``errors`` text; falls back to the pending reason
    message; finally to a generic placeholder so the leader always sees a
    non-empty handling cue.
    """
    errors = getattr(result, "errors", None) or []
    if errors:
        return f"Claude SDK turn failed: {' '.join(str(e) for e in errors)}"
    if reason is not None:
        message = getattr(reason, "message", "") or ""
        if message:
            return f"Claude SDK turn failed: {message}"
    return "Claude SDK turn failed"


def _iter_sdk_chunks(
    message: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    *,
    mcp_server_name: str,
    team_tool_names: set[str],
) -> list[OutputSchema]:
    """Convert one Claude SDK message into native team stream chunks."""
    sdk = load_claude_sdk()
    if isinstance(message, sdk.AssistantMessage):
        return _assistant_chunks(
            message.content,
            start_index,
            tool_metadata_by_id,
            mcp_server_name=mcp_server_name,
            team_tool_names=team_tool_names,
        )
    if isinstance(message, sdk.UserMessage):
        return _user_chunks(message, start_index, tool_metadata_by_id)
    if isinstance(message, sdk.SystemMessage) or isinstance(message, sdk.ResultMessage):
        return []
    return []


def _assistant_chunks(
    content: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    *,
    mcp_server_name: str,
    team_tool_names: set[str],
) -> list[OutputSchema]:
    """Convert assistant content blocks into stream chunks."""
    if not isinstance(content, list):
        return []
    sdk = load_claude_sdk()
    chunks: list[OutputSchema] = []
    index = start_index
    for block in content:
        if isinstance(block, sdk.TextBlock):
            if block.text:
                chunks.append(_text_chunk("llm_output", block.text, index))
                index += 1
        elif isinstance(block, sdk.ThinkingBlock):
            if block.thinking:
                chunks.append(_text_chunk("llm_reasoning", block.thinking, index))
                index += 1
        elif isinstance(block, sdk.ToolUseBlock):
            is_team_tool = _is_team_mcp_tool(
                block.name,
                mcp_server_name=mcp_server_name,
                team_tool_names=team_tool_names,
            )
            if block.id:
                tool_metadata_by_id[block.id] = _ClaudeToolMetadata(
                    tool_name=block.name,
                    is_team_tool=is_team_tool,
                )
            chunks.append(
                OutputSchema(
                    type="tool_call",
                    index=index,
                    payload={
                        "name": block.name,
                        "arguments": _json_arguments(block.input),
                        "tool_call_id": block.id,
                        "is_team_tool": is_team_tool,
                    },
                ),
            )
            index += 1
    return chunks


def _user_chunks(
    message: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
) -> list[OutputSchema]:
    """Convert user-side tool results into stream chunks without replaying text."""
    chunks: list[OutputSchema] = []
    index = start_index
    content_chunks = _tool_result_content_chunks(message.content, index, tool_metadata_by_id)
    chunks.extend(content_chunks)
    index += len(content_chunks)
    if not content_chunks and message.tool_use_result is not None:
        tool_call_id = message.parent_tool_use_id or ""
        tool_metadata = _pop_tool_metadata(tool_metadata_by_id, tool_call_id)
        chunks.append(
            OutputSchema(
                type="tool_result",
                index=index,
                payload={
                    "tool_name": tool_metadata.tool_name,
                    "result": _normalize_tool_result(message.tool_use_result),
                    "tool_call_id": tool_call_id,
                    "is_team_tool": tool_metadata.is_team_tool,
                },
            ),
        )
    return chunks


def _tool_result_content_chunks(
    content: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
) -> list[OutputSchema]:
    """Convert tool result content blocks into stream chunks."""
    if not isinstance(content, list):
        return []
    sdk = load_claude_sdk()
    chunks: list[OutputSchema] = []
    index = start_index
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            tool_metadata = _pop_tool_metadata(tool_metadata_by_id, block.tool_use_id)
            chunks.append(
                OutputSchema(
                    type="tool_result",
                    index=index,
                    payload={
                        "tool_name": tool_metadata.tool_name,
                        "result": _normalize_tool_result(block.content),
                        "tool_call_id": block.tool_use_id,
                        "is_team_tool": tool_metadata.is_team_tool,
                    },
                ),
            )
            index += 1
    return chunks


def _pop_tool_metadata(
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    tool_call_id: str,
) -> _ClaudeToolMetadata:
    """Return and forget metadata matched by a Claude tool-use id."""
    if not tool_call_id:
        return _ClaudeToolMetadata()
    return tool_metadata_by_id.pop(tool_call_id, _ClaudeToolMetadata())


def _is_team_mcp_tool(tool_name: str, *, mcp_server_name: str, team_tool_names: set[str]) -> bool:
    """Return whether the SDK tool label belongs to the local team MCP server."""
    prefix = f"mcp__{mcp_server_name}__"
    if not tool_name.startswith(prefix):
        return False
    real_tool_name = tool_name[len(prefix):]
    return real_tool_name in team_tool_names


def _text_chunk(chunk_type: str, content: str, index: int) -> OutputSchema:
    """Build a text-like stream chunk."""
    return OutputSchema(
        type=chunk_type,
        index=index,
        payload={"content": content, "result_type": "answer"},
    )


def _json_arguments(value: Any) -> str:
    """Serialize external tool arguments into the native tool-call shape."""
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _normalize_tool_result(value: Any) -> Any:
    """Convert SDK text content blocks into the native string result shape."""
    if not isinstance(value, list):
        return value
    text_parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            return value
        if item.get("type") != "text":
            return value
        text = item.get("text")
        if not isinstance(text, str):
            return value
        text_parts.append(text)
    return "\n".join(text_parts)


__all__ = ["ClaudeSdkRuntime", "build_claude_runtime"]
