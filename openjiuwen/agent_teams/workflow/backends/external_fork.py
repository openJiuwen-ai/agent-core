# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fork an external (Claude Code / Codex) swarmflow session through the provider's own primitive.

A fork has two moments the engine already separates: *capture* at ``fork()``
time and *materialise* when the child first runs. For an external member the
capture is a set of credentials — which parent conversation, up to which turn
— never a copy of the conversation: only the provider can branch its own
session with tool context intact, so the host records what to ask for and
asks at the last possible moment.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.harness_providers.claudecode.options import load_claude_sdk
from openjiuwen.harness_providers.codex.options import build_codex_config, build_process_env, load_codex_sdk

_FORK_DATA_KEY = "external_fork"


@dataclass(frozen=True)
class ForkCredentials:
    """What the child needs to ask its provider for a branch of the parent."""

    provider: str
    parent_member: str
    parent_pointer: str
    mode: str
    cut_marker: str | None
    keep_rounds: int | None

    def to_fork_data(self) -> dict:
        return {_FORK_DATA_KEY: asdict(self)}

    @classmethod
    def from_fork_data(cls, data: Any) -> "ForkCredentials | None":
        if not isinstance(data, dict):
            return None
        raw = data.get(_FORK_DATA_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return cls(**raw)
        except TypeError:
            return None


def supported_fork_modes(provider: str) -> frozenset[str]:
    """Fork modes the provider card declares (empty for unknown / adapter kinds)."""
    if provider == "claude":
        from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness

        return ClaudeCodeHarness.card.fork_modes
    if provider == "codex":
        from openjiuwen.harness_providers.codex import CodexHarness

        return CodexHarness.card.fork_modes
    return frozenset()


def check_fork_mode(provider: str, mode: str) -> None:
    """Reject anything the provider cannot branch natively — no host emulation."""
    modes = supported_fork_modes(provider)
    if mode in modes:
        return
    supported = ", ".join(sorted(modes)) or "(none)"
    raise BackendError(
        f"fork_mode {mode!r} is not supported for external {provider} sessions; "
        f"supported: [{supported}]. Use fork_mode='full' or 'before' with keep_rounds."
    )


def cut_marker_for(markers: Sequence[str | None], mode: str, keep_rounds: int | None) -> str | None:
    """Pick the parent turn the fork keeps up to (``None`` = whole conversation)."""
    if mode == "full":
        return None
    if keep_rounds is None or keep_rounds < 1 or keep_rounds > len(markers):
        raise BackendError(
            f"keep_rounds={keep_rounds!r} is outside the parent's {len(markers)} recorded external turn(s)"
        )
    marker = markers[keep_rounds - 1]
    if not marker:
        raise BackendError(f"parent turn {keep_rounds} recorded no provider marker; cannot fork before it")
    return marker


def reconcile(credentials: ForkCredentials, parent_markers: Sequence[str | None]) -> None:
    """The cut point must still be a turn the parent actually has."""
    if credentials.cut_marker is None:
        return
    if credentials.cut_marker not in parent_markers:
        raise BackendError(
            f"fork credentials no longer match the parent session {credentials.parent_member!r}: "
            f"turn marker {credentials.cut_marker!r} is not in its history"
        )


async def materialise_external_fork(credentials: ForkCredentials, *, entry: Any, cwd: str | None) -> str:
    """Ask the provider for the branch; return the child's session / thread pointer."""
    if credentials.provider == "claude":
        return await _fork_claude(credentials, entry=entry, cwd=cwd)
    if credentials.provider == "codex":
        return await _fork_codex(credentials, entry=entry, cwd=cwd)
    raise BackendError(f"no fork materialiser for provider {credentials.provider!r}")


async def _fork_claude(credentials: ForkCredentials, *, entry: Any, cwd: str | None) -> str:
    """Offline transcript fork: the SDK copies the JSONL up to the cut message under a new id."""
    sdk = load_claude_sdk()
    directory = cwd or getattr(entry, "cwd", None)

    async def _call(where: str | None) -> Any:
        return await asyncio.to_thread(
            sdk.fork_session,
            credentials.parent_pointer,
            directory=where,
            up_to_message_id=credentials.cut_marker,
        )

    try:
        try:
            result = await _call(directory)
        except FileNotFoundError:
            if directory is None:
                raise
            # The member's cwd is not always the project dir the transcript was
            # written under; without a directory the SDK searches all of them.
            result = await _call(None)
    except Exception as e:  # noqa: BLE001 - surfaced as a backend error with provider text
        raise BackendError(f"Claude fork failed for {credentials.parent_member!r}: {e}") from e
    return str(result.session_id)


def _codex_config_for(sdk: Any, entry: Any, cwd: str | None) -> Any:
    """The same app-server config the member runtime would use, so the thread is addressable."""
    from openjiuwen.agent_teams.external.cli_agent.spawn import _codex_process_env
    from openjiuwen.harness_providers.codex import CodexHarnessConfig

    config = CodexHarnessConfig(
        cwd=cwd,
        codex_bin=getattr(entry, "codex_bin", None) or getattr(entry, "cli_path", None),
        # A fork client that does not see the member's env (CODEX_HOME above all)
        # talks to a different app server, where the parent thread does not exist.
        env=_codex_process_env(extra_env=getattr(entry, "env", None) or None, join_env={}),
        inherit_process_env=False,
    )
    return build_codex_config(
        sdk=sdk, config=config, model=None, cwd=cwd,
        env=build_process_env(config, {}), mcp_servers=(), enable_user_input=False,
    )


async def _fork_codex(credentials: ForkCredentials, *, entry: Any, cwd: str | None) -> str:
    """Branch the parent thread on the app server; a short-lived client is enough."""
    sdk = load_codex_sdk()
    # The AsyncCodex facade's thread_fork() has no last_turn_id parameter; the
    # raw app-server client accepts the wire-level params directly.
    client = sdk.async_client.AsyncCodexClient(config=_codex_config_for(sdk, entry, cwd))
    params: dict[str, Any] = {}
    if credentials.cut_marker is not None:
        params["lastTurnId"] = credentials.cut_marker
    if cwd:
        params["cwd"] = cwd
    try:
        await client.start()
        await client.initialize()
        forked = await client.thread_fork(credentials.parent_pointer, params)
    except Exception as e:  # noqa: BLE001
        raise BackendError(f"Codex fork failed for {credentials.parent_member!r}: {e}") from e
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
    return str(forked.thread.id)


__all__ = [
    "ForkCredentials",
    "check_fork_mode",
    "cut_marker_for",
    "materialise_external_fork",
    "reconcile",
    "supported_fork_modes",
]
