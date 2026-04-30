# coding: utf-8
"""Interactive E2E example for a Runner-owned TeamAgentSpec runtime."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

_HERE = Path(__file__).resolve().parent
_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config.yaml"
_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda match: os.environ.get(match.group(1), match.group(0)), value)
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _load_team_spec(path: Path) -> tuple[TeamAgentSpec, dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as file:
        raw = yaml.safe_load(file)
    expanded = _expand_env_vars(raw)
    runtime = expanded.pop("runtime", {})
    return TeamAgentSpec.model_validate(expanded), runtime


async def ainput(prompt: str = "> ") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


class TeamStreamCli:
    """Small interactive shell that mirrors the future claw usage model."""

    def __init__(self, base_spec: TeamAgentSpec, specs: dict[str, TeamAgentSpec]):
        self._base_spec = base_spec
        self._specs = specs
        self._stream_handle: StreamHandle | None = None
        self._active_team_name: str | None = None
        self._active_session_id: str | None = None
        self._pending_team_name: str | None = None
        self._pending_session_id: str | None = None

    @property
    def current_session_id(self) -> str | None:
        return self._active_session_id

    @property
    def current_team_name(self) -> str | None:
        return self._active_team_name

    async def start_session(self, team_name: str, session_id: str, query: str) -> dict[str, Any]:
        self._pending_team_name = team_name
        self._pending_session_id = session_id
        handle = await self._restart_stream(team_name, session_id, query)
        ack = await self._await_runtime_ready(handle)
        self._active_team_name = team_name
        self._active_session_id = session_id
        self._pending_team_name = None
        self._pending_session_id = None
        return ack

    async def switch_session(
        self,
        team_name: str,
        session_id: str,
        query: str,
    ) -> tuple[bool, dict[str, Any] | str]:
        """Switch by stopping the old stream first, then commit on runtime_ready."""
        previous_team = self._active_team_name
        previous_active = self._active_session_id
        self._pending_team_name = team_name
        self._pending_session_id = session_id
        try:
            handle = await self._restart_stream(team_name, session_id, query)
            ack = await self._await_runtime_ready(handle)
        except Exception as exc:
            self._pending_team_name = None
            self._pending_session_id = None
            rollback_to = previous_active or "none"
            self._active_team_name = previous_team
            self._active_session_id = previous_active
            return False, (
                f"switch failed before ack: {exc}; "
                f"rollback active_team={previous_team or 'none'} active_session={rollback_to}"
            )

        self._active_team_name = team_name
        self._active_session_id = session_id
        self._pending_team_name = None
        self._pending_session_id = None
        return True, ack

    async def route_user_request(
        self,
        *,
        team_name: str,
        session_id: str,
        query: str,
    ) -> tuple[str, dict[str, Any] | str]:
        """Route same-session input to interact, and changed session to switch."""
        if self._active_team_name == team_name and self._active_session_id == session_id:
            delivered = await self.interact(query)
            return (
                "interact",
                f"delivered={delivered} active_team={self._active_team_name} active_session={self._active_session_id}",
            )

        switched, result = await self.switch_session(team_name, session_id, query)
        return ("switch_committed" if switched else "switch_rolled_back"), result

    async def interact(self, user_input: str) -> bool:
        if self._active_team_name is None or self._active_session_id is None:
            return False
        return await Runner.interact_agent_team(
            user_input,
            team_name=self._active_team_name,
            session_id=self._active_session_id,
        )

    async def pause(self) -> bool:
        if self._active_team_name is None or self._active_session_id is None:
            return False
        return await Runner.pause_agent_team(
            team_name=self._active_team_name,
            session_id=self._active_session_id,
        )

    async def stop_stream(self) -> None:
        handle = self._stream_handle
        self._stream_handle = None
        self._active_team_name = None
        self._active_session_id = None
        self._pending_team_name = None
        self._pending_session_id = None
        if handle is not None:
            await self._stop_handle(handle)

    async def _await_runtime_ready(self, handle: "StreamHandle") -> dict[str, Any]:
        return await asyncio.wait_for(handle.runtime_ready, timeout=30)

    async def _restart_stream(self, team_name: str, session_id: str, query: str) -> "StreamHandle":
        old_handle = self._stream_handle
        if old_handle is not None:
            paused = await Runner.pause_agent_team(
                team_name=old_handle.team_name,
                session_id=old_handle.session_id,
            )
            print(
                f"[system] pause old runtime before switch: "
                f"active_team={old_handle.team_name} active_session={old_handle.session_id} "
                f"paused={paused}"
            )
            print(
                f"[system] stopping old stream before switch: "
                f"active_team={old_handle.team_name} active_session={old_handle.session_id} "
                f"-> pending_team={team_name} pending_session={session_id}"
            )
            await self._stop_handle(old_handle)

        spec = self._get_or_create_spec(team_name)
        runtime_ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._consume_stream(spec, session_id, query, runtime_ready))
        handle = StreamHandle(
            team_name=team_name,
            session_id=session_id,
            query=query,
            runtime_ready=runtime_ready,
            task=task,
        )
        self._stream_handle = handle
        print(
            f"[system] started new stream: active_team={self._active_team_name} "
            f"active_session={self._active_session_id} pending_team={self._pending_team_name} "
            f"pending_session={self._pending_session_id}"
        )
        return handle

    async def _stop_handle(self, handle: "StreamHandle") -> None:
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await handle.task

    def _get_or_create_spec(self, team_name: str) -> TeamAgentSpec:
        spec = self._specs.get(team_name)
        if spec is not None:
            return spec

        spec = self._base_spec.model_copy(update={"team_name": team_name})
        self._specs[team_name] = spec
        return spec

    async def _consume_stream(
        self,
        spec: TeamAgentSpec,
        session_id: str,
        query: str,
        runtime_ready: asyncio.Future[dict[str, Any]],
    ) -> None:
        try:
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": query},
                session=session_id,
            ):
                payload = getattr(chunk, "payload", {})
                if (
                    isinstance(payload, dict)
                    and payload.get("event_type") == "team.runtime_ready"
                    and not runtime_ready.done()
                ):
                    print(f"[ack] session={session_id} payload={payload}")
                    runtime_ready.set_result(payload)
                    continue
                print(f"[stream] session={session_id} payload={payload}")
        except asyncio.CancelledError:
            print(f"[system] stream cancelled for session={session_id}")
            raise
        except Exception as exc:
            if not runtime_ready.done():
                runtime_ready.set_exception(exc)
            print(f"[error] session={session_id} error={exc}")
            raise


def _print_help() -> None:
    print("Commands:")
    print("  <text>                    send user input to the active team runtime")
    print("  :switch <sid> <query>     same session -> interact; new session -> switch with runtime_ready")
    print("  :switch-team <team> <sid> <query>  switch across team_name and wait for runtime_ready")
    print("  :pause                    pause the active team runtime")
    print("  :quit                     stop the stream and exit")


@dataclass(slots=True)
class StreamHandle:
    team_name: str
    session_id: str
    query: str
    runtime_ready: asyncio.Future[dict[str, Any]]
    task: asyncio.Task


async def main() -> None:
    base_spec, runtime_cfg = _load_team_spec(_TEAM_CONFIG_PATH)
    alt_team_name = runtime_cfg.get("alt_team_name", f"{base_spec.team_name}_alt")
    specs = {
        base_spec.team_name: base_spec,
        alt_team_name: base_spec.model_copy(update={"team_name": alt_team_name}),
    }
    initial_session = runtime_cfg.get("session_id", "agent_team_owner_session")
    initial_query = runtime_cfg.get("initial_query", "hello")
    initial_team_name = runtime_cfg.get("team_name", base_spec.team_name)

    cli = TeamStreamCli(base_spec, specs)

    await Runner.start()
    try:
        _print_help()
        print(f"[system] available teams: {', '.join(specs)}")
        first_ack = await cli.start_session(initial_team_name, initial_session, initial_query)
        print(f"[system] active team={initial_team_name} session={initial_session} ack={first_ack}")

        while True:
            try:
                raw = (await ainput("\n[you] > ")).strip()
            except EOFError:
                break

            if not raw:
                continue
            if raw == ":quit":
                break
            if raw == ":pause":
                paused = await cli.pause()
                print(
                    f"[system] pause requested: ok={paused} "
                    f"active_team={cli.current_team_name} active_session={cli.current_session_id}"
                )
                continue
            if raw.startswith(":switch-team "):
                parts = raw.split(" ", 3)
                if len(parts) < 4:
                    print("[system] usage: :switch-team <team_name> <session_id> <query>")
                    continue
                team_name = parts[1].strip()
                session_id = parts[2].strip()
                query = parts[3].strip()
                action, result = await cli.route_user_request(
                    team_name=team_name,
                    session_id=session_id,
                    query=query,
                )
                if action == "interact":
                    print(f"[system] same team+session follow-up routed to interact: {result}")
                elif action == "switch_committed":
                    print(f"[system] team switch committed: active team={team_name} session={session_id} ack={result}")
                else:
                    print(f"[system] team switch rolled back: {result}")
                continue
            if raw.startswith(":switch "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[system] usage: :switch <session_id> <query>")
                    continue
                session_id = parts[1].strip()
                query = parts[2].strip()
                if cli.current_team_name is None:
                    print("[system] no active team")
                    continue
                action, result = await cli.route_user_request(
                    team_name=cli.current_team_name,
                    session_id=session_id,
                    query=query,
                )
                if action == "interact":
                    print(f"[system] same-session follow-up routed to interact: {result}")
                elif action == "switch_committed":
                    print(
                        f"[system] switch committed: active team={cli.current_team_name} "
                        f"session={session_id} ack={result}"
                    )
                else:
                    print(f"[system] switch rolled back: {result}")
                continue

            delivered = await cli.interact(raw)
            print(
                f"[system] input delivered={delivered} "
                f"team={cli.current_team_name} session={cli.current_session_id}"
            )
    finally:
        await cli.stop_stream()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
