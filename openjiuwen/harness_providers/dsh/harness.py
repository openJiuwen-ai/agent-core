# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HarnessProtocol implementation backed by the DSH Python SDK."""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from dataclasses import replace
from typing import Any, cast

from openjiuwen.harness_protocol import (
    PROTOCOL_VERSION,
    HarnessCard,
    HarnessCapability,
    HostCapability,
    HarnessContext,
    HarnessError,
    HarnessInput,
    HarnessProtocolError,
    ResumePolicy,
    TurnEventKind,
    TurnResult,
    UnsupportedHarnessCapabilityError,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.skills import install_skills
from openjiuwen.harness_providers.base import PendingTurn, SerializedTurnHarness, TurnTiming, logger
from openjiuwen.harness_providers.dsh.composition import mcp_configs, write_overlay
from openjiuwen.harness_providers.dsh.config import DshHarnessConfig
from openjiuwen.harness_providers.dsh.mapping import DshTurnAccumulator, MappedDshEvent

ADAPTER_VERSION = "0.3.0"


class DshHarness(SerializedTurnHarness):
    """Adapt one reusable DeepSeek Harness session to protocol v1.

    Each OpenJiuwen Turn is one serialized DSH activity interval: input
    acceptance through the next whole-agent idle.  DSH's own ``turn`` and
    ``step`` records remain internal/provider observations and do not redefine
    this public Turn boundary.
    """

    card = HarnessCard(
        name="deepseek-harness",
        implementation_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        compatible_protocol_versions=frozenset({PROTOCOL_VERSION}),
        capabilities=frozenset({HarnessCapability.MCP_TOOLS}),
        optional_host_capabilities=frozenset({HostCapability.MCP_SERVERS}),
    )

    def __init__(self, config: DshHarnessConfig | None = None) -> None:
        self._config = config or DshHarnessConfig()
        super().__init__(event_buffer_capacity=self._config.event_buffer_capacity)
        self._sdk_harness: Any = None
        self._sdk_session: Any = None
        self._overlay: Any = None

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    def _needs_host_overlay(self, context: HarnessContext) -> bool:
        """Report whether this context can only be served through a host overlay.

        Args:
            context: The harness context about to open a session.

        Returns:
            True when skills, MCP servers or an injected system prompt require
            the overlay that a custom launcher would bypass.
        """
        if self._config.skills and self._config.profile == "sdk-minimal":
            return True
        if context.mcp_servers:
            return True
        return bool(context.system_prompt) and self._config.system_prompt_env_var is None

    def _validate_context(self, context: HarnessContext) -> None:
        super()._validate_context(context)
        if context.resume_policy is ResumePolicy.REQUIRE_RESUME or context.checkpoint is not None:
            raise UnsupportedHarnessCapabilityError("the DSH SDK server cannot restore protocol checkpoints")
        mcp_configs(context)
        if self._config.launch_args_override is not None and self._needs_host_overlay(context):
            raise UnsupportedHarnessCapabilityError("DSH host overlays require the standard profile launcher")

    async def _open_session(self, context: HarnessContext) -> str | None:
        """Start a fresh DSH subprocess/session cycle."""
        await asyncio.to_thread(install_skills, self._config.skills, provider="dsh",
                                cwd=context.cwd or self._config.cwd, conflict=self._config.skill_conflict)

        # Resolve the optional dependency and pure SDK options before opening
        # an observable protocol cycle.  A missing SDK must not leave a
        # half-started session behind.
        sdk = _load_dsh_sdk()
        options = self._sdk_options(context)
        session_id = f"dsh-{uuid.uuid4().hex}"
        overlay_context = replace(context, cwd=context.cwd or self._config.cwd)
        overlay = write_overlay(
            overlay_context,
            include_prompt=self._config.system_prompt_env_var is None,
            prompt_mode=self._config.system_prompt_mode,
            enable_skill_plugins=bool(self._config.skills) and self._config.profile == "sdk-minimal",
        )
        if overlay is not None:
            self._overlay, path, env = overlay
            options["env"].update(env)
            options["patches"] = (*options.get("patches", ()), path)
        sdk_harness = None
        try:
            launch_args = self._config.launch_args_override
            if launch_args is not None:
                sdk_harness = sdk.DeepSeekHarness(_launch_args=launch_args, **options)
            else:
                sdk_harness = sdk.DeepSeekHarness(**options)
            sdk_session = await asyncio.to_thread(sdk_harness.start_session, session_id)
        except Exception:
            if sdk_harness is not None:
                await _close_sdk_quietly(sdk_harness)
            # SDK transport errors may include a subprocess stderr tail; do
            # not retain it as an exception cause because context.env and
            # provider credentials are explicitly sensitive.
            raise HarnessError("failed to start the DeepSeek Harness SDK runtime") from None
        except BaseException:
            if sdk_harness is not None:
                await _close_sdk_quietly(sdk_harness)
            raise
        self._sdk_harness = sdk_harness
        self._sdk_session = sdk_session
        return session_id

    async def _close_session(self) -> None:
        sdk_harness = self._sdk_harness
        self._sdk_harness = None
        self._sdk_session = None
        try:
            if sdk_harness is not None:
                await _close_sdk_quietly(sdk_harness)
        finally:
            if self._overlay is not None:
                self._overlay.cleanup()
                self._overlay = None

    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        timing = TurnTiming()
        session = self._sdk_session
        session_id = self._session_id
        if session is None or session_id is None:
            raise HarnessProtocolError("DSH session disappeared during an active cycle")
        accumulator = DshTurnAccumulator(turn_id=turn.turn_id, root_session_id=session_id)
        loop = asyncio.get_running_loop()

        def on_notification(notification: object) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._handle_notification(turn, accumulator, notification),
                loop,
            )
            future.result()

        try:
            run_result = await asyncio.to_thread(
                session.run,
                _to_dsh_input(turn.content),
                on_notification=on_notification,
            )
        except Exception as exc:
            if turn.stop_requested:
                return TurnEventKind.ABORTED, accumulator.build_stopped_result(
                    started_at=timing.started_at,
                    started_monotonic=timing.started_monotonic,
                )
            return TurnEventKind.FAILED, accumulator.build_failed_result(
                exc,
                started_at=timing.started_at,
                started_monotonic=timing.started_monotonic,
            )
        return accumulator.build_terminal_result(
            run_result,
            started_at=timing.started_at,
            started_monotonic=timing.started_monotonic,
        )

    async def _handle_notification(
        self,
        turn: PendingTurn,
        accumulator: DshTurnAccumulator,
        notification: object,
    ) -> None:
        for mapped in accumulator.consume(notification):
            await self._emit_mapped(turn, mapped)

    async def _emit_mapped(self, turn: PendingTurn, mapped: MappedDshEvent) -> None:
        await self._emit(
            mapped.payload,
            turn=turn,
            item_id=mapped.item_id,
            provider_session_id=mapped.provider_session_id,
        )

    def _sdk_options(self, context: HarnessContext) -> dict[str, object]:
        env = dict(self._config.env)
        env.update(context.env)
        if context.system_prompt and self._config.system_prompt_env_var is not None:
            env[self._config.system_prompt_env_var] = context.system_prompt
        values: dict[str, object | None] = {
            "provider": self._config.provider,
            "model": self._config.model,
            "reasoning_effort": self._config.reasoning_effort,
            "max_tokens": self._config.max_tokens,
            "cwd": context.cwd or self._config.cwd,
            "runtime_cwd": self._config.runtime_cwd,
            "dsh_bin": self._config.dsh_bin,
            "dsh_home": self._config.dsh_home,
            "profile": self._config.profile,
            "patches": self._config.patches or None,
            "env": env,
            "initialize_timeout_seconds": self._config.initialize_timeout_seconds,
            "request_timeout_seconds": self._config.request_timeout_seconds,
            "shutdown_timeout_seconds": self._config.shutdown_timeout_seconds,
            "base_url": self._config.base_url,
            "api_key": self._config.api_key,
        }
        return {key: value for key, value in values.items() if value is not None}


def _load_dsh_sdk() -> Any:
    try:
        return importlib.import_module("deepseek_harness")
    except ImportError as exc:
        raise HarnessError(
            "deepseek-harness-sdk is required for the DSH adapter; install the optional SDK before start()"
        ) from exc


async def _close_sdk_quietly(sdk_harness: Any) -> None:
    try:
        await asyncio.to_thread(sdk_harness.close)
    except Exception as exc:
        logger.debug("DSH SDK close failed during teardown: %s", type(exc).__name__)


def _to_dsh_input(content: HarnessInput) -> str | list[dict[str, object]]:
    value = json_value_to_builtin(content.content)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(block, dict) for block in value):
        return cast(list[dict[str, object]], value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = ["ADAPTER_VERSION", "DshHarness"]
