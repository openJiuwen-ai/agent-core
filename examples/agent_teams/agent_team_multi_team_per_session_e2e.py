# coding: utf-8
"""Interactive E2E example: multiple teams sharing a single session.

Runs two TeamAgentSpec instances ("team_alpha" / "team_beta") under the
same session_id so they share the session checkpoint namespace but each
holds an independent ActiveTeam in the runtime pool. Demonstrates:

* parallel ``run_agent_team_streaming`` calls keyed by team_name;
* per-team interact_team routing through the three payload shapes
  (god view / operator / human-agent) and DeliverResult feedback;
* per-team pause / stop / release_session with the busy precondition;
* the chunk emitted with action_kind so callers can tell create from
  cold/warm recover.

Configuration reuses ``config.yaml`` (same fields as agent_team_e2e.py);
``runtime.session_id`` defaults to ``multi_team_session`` and the second
team's name is taken from ``runtime.alt_team_name`` (defaults to
``<base_team_name>_beta``).

Run:
    python examples/agent_teams/agent_team_multi_team_per_session_e2e.py

CLI commands:
    :start <team> [<query>]       launch a stream for <team> on the shared session
    :god <team> <text>            send GodViewMessage to <team>'s leader
    :op <team> [@target] <text>   send OperatorMessage; @target=direct, no target=broadcast
    :ha <team> <sender> <text>    send HumanAgentMessage as <sender> (HITT must be on)
    :pause <team>                 pause <team>'s runtime
    :stop <team>                  stop <team>'s runtime (preserves data)
    :status                       print pool snapshot for the shared session
    :release                      release_session (requires every team stopped)
    :quit                         clean up and exit
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Optional,
)

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.interaction.payload import (
    DeliverResult,
    GodViewMessage,
    HumanAgentMessage,
    OperatorMessage,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import (
    ainput,
    load_team_config,
)

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


@dataclass(slots=True)
class TeamStream:
    """Per-team stream task on the shared session."""

    team_name: str
    spec: TeamAgentSpec
    task: asyncio.Task
    runtime_ready: asyncio.Future
    last_action_kind: Optional[str] = None
    chunks_seen: int = field(default=0)


class MultiTeamSessionShell:
    """Manage two TeamAgentSpec runtimes that share one session_id."""

    def __init__(self, specs: dict[str, TeamAgentSpec], session_id: str) -> None:
        self._specs = specs
        self._session_id = session_id
        self._streams: dict[str, TeamStream] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def teams(self) -> list[str]:
        return list(self._specs.keys())

    def is_running(self, team_name: str) -> bool:
        stream = self._streams.get(team_name)
        return stream is not None and not stream.task.done()

    async def start(self, team_name: str, query: str) -> Optional[dict[str, Any]]:
        if team_name not in self._specs:
            print(f"[system] unknown team: {team_name}")
            return None
        if self.is_running(team_name):
            print(f"[system] team {team_name} already running")
            return None

        spec = self._specs[team_name]
        runtime_ready: asyncio.Future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            self._consume(spec, query, runtime_ready),
            name=f"stream::{team_name}",
        )
        stream = TeamStream(
            team_name=team_name,
            spec=spec,
            task=task,
            runtime_ready=runtime_ready,
        )
        self._streams[team_name] = stream
        try:
            ack = await asyncio.wait_for(runtime_ready, timeout=30)
        except Exception as exc:
            print(f"[system] {team_name} failed to reach runtime_ready: {exc}")
            await self._stop_task(stream)
            self._streams.pop(team_name, None)
            return None
        stream.last_action_kind = ack.get("activation_kind")
        print(
            f"[system] {team_name} ready on session={self._session_id} "
            f"action_kind={stream.last_action_kind} ack={ack}"
        )
        return ack

    async def interact(self, team_name: str, payload) -> DeliverResult:
        if team_name not in self._specs:
            return DeliverResult.failure("unknown_team")
        return await Runner.interact_agent_team(
            payload,
            team_name=team_name,
            session_id=self._session_id,
        )

    async def pause(self, team_name: str) -> bool:
        if team_name not in self._specs:
            print(f"[system] unknown team: {team_name}")
            return False
        return await Runner.pause_agent_team(
            team_name=team_name,
            session_id=self._session_id,
        )

    async def stop(self, team_name: str) -> bool:
        if team_name not in self._specs:
            print(f"[system] unknown team: {team_name}")
            return False
        ok = await Runner.stop_agent_team(
            team_name=team_name,
            session_id=self._session_id,
        )
        stream = self._streams.pop(team_name, None)
        if stream is not None:
            await self._stop_task(stream)
        return ok

    async def release(self) -> None:
        # release_session requires every team to be stopped first.
        for team_name in list(self._streams.keys()):
            await self.stop(team_name)
        await Runner.release(self._session_id)
        print(f"[system] released session={self._session_id}")

    async def shutdown(self) -> None:
        for team_name in list(self._streams.keys()):
            await self.stop(team_name)

    async def status(self) -> None:
        # Demo-only: the public Runner facade does not yet expose the team
        # pool; reach through the runner singleton for read-only inspection.
        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        manager = GLOBAL_RUNNER._get_team_runtime_manager()
        active_on_session = await manager.pool.teams_for_session(self._session_id)
        print(f"[status] session={self._session_id}")
        for team_name in self.teams:
            entry = await manager.pool.get(team_name)
            stream = self._streams.get(team_name)
            running = "yes" if stream and not stream.task.done() else "no"
            entry_state = entry.state.value if entry else "absent"
            print(
                f"  {team_name:<24} pool_state={entry_state:<8} "
                f"stream={running:<3} chunks={stream.chunks_seen if stream else 0}"
            )
        print(f"  -> pool entries on this session: {[t.team_name for t in active_on_session]}")

    async def _consume(
        self,
        spec: TeamAgentSpec,
        query: str,
        runtime_ready: asyncio.Future,
    ) -> None:
        team_name = spec.team_name
        try:
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": query},
                session=self._session_id,
            ):
                payload = getattr(chunk, "payload", None)
                if (
                    isinstance(payload, dict)
                    and payload.get("event_type") == "team.runtime_ready"
                    and not runtime_ready.done()
                ):
                    runtime_ready.set_result(payload)
                    continue
                stream = self._streams.get(team_name)
                if stream is not None:
                    stream.chunks_seen += 1
                preview = (
                    payload.get("content")
                    or payload.get("output")
                    or payload
                ) if isinstance(payload, dict) else payload
                print(f"[{team_name}] chunk={getattr(chunk, 'type', '')} payload={preview}")
        except asyncio.CancelledError:
            print(f"[{team_name}] stream cancelled")
            raise
        except Exception as exc:
            if not runtime_ready.done():
                runtime_ready.set_exception(exc)
            print(f"[{team_name}] stream error: {exc}")

    @staticmethod
    async def _stop_task(stream: TeamStream) -> None:
        stream.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stream.task


def _print_help() -> None:
    print("Commands:")
    print("  :start <team> [<query>]              launch <team>'s stream on the shared session")
    print("  :god <team> <text>                   GodViewMessage -> leader")
    print("  :op <team> [@target] <text>          OperatorMessage; @target = direct, else broadcast")
    print("  :ha <team> <sender> <text>           HumanAgentMessage (HITT must be on)")
    print("  :pause <team>                        pause <team>'s runtime")
    print("  :stop <team>                         stop <team>'s runtime")
    print("  :status                              print pool snapshot for this session")
    print("  :release                             release_session (every team must be stopped first)")
    print("  :quit                                clean up and exit")


def _build_specs(base: TeamAgentSpec, runtime_cfg: dict[str, Any]) -> dict[str, TeamAgentSpec]:
    """Derive an alpha/beta pair from the base config so they share infra but differ in team_name."""
    alpha_name = base.team_name
    beta_name = runtime_cfg.get("alt_team_name", f"{alpha_name}_beta")
    if beta_name == alpha_name:
        beta_name = f"{alpha_name}_beta"
    beta_spec = base.model_copy(update={"team_name": beta_name})
    return {alpha_name: base, beta_name: beta_spec}


def _parse_op_payload(args: str) -> Optional[OperatorMessage]:
    """``@target body`` => direct; ``body`` => broadcast."""
    args = args.strip()
    if not args:
        return None
    if args.startswith("@"):
        head, _, body = args.partition(" ")
        target = head[1:].strip()
        if not target or not body.strip():
            return None
        return OperatorMessage(body=body.strip(), target=target)
    return OperatorMessage(body=args)


async def _handle_command(shell: MultiTeamSessionShell, raw: str) -> bool:
    """Return False to signal main loop to exit."""
    if raw == ":quit":
        return False
    if raw == ":status":
        await shell.status()
        return True
    if raw == ":release":
        await shell.release()
        return True

    head, _, rest = raw.partition(" ")
    rest = rest.strip()

    if head == ":start":
        team_name, _, query = rest.partition(" ")
        team_name = team_name.strip()
        if not team_name:
            print("[system] usage: :start <team> [<query>]")
            return True
        await shell.start(team_name, query.strip() or "hello")
        return True
    if head == ":pause":
        if not rest:
            print("[system] usage: :pause <team>")
            return True
        ok = await shell.pause(rest)
        print(f"[system] pause {rest}: ok={ok}")
        return True
    if head == ":stop":
        if not rest:
            print("[system] usage: :stop <team>")
            return True
        ok = await shell.stop(rest)
        print(f"[system] stop {rest}: ok={ok}")
        return True
    if head == ":god":
        team_name, _, body = rest.partition(" ")
        team_name = team_name.strip()
        body = body.strip()
        if not team_name or not body:
            print("[system] usage: :god <team> <text>")
            return True
        result = await shell.interact(team_name, GodViewMessage(body=body))
        print(f"[system] god {team_name}: ok={result.ok} reason={result.reason!r}")
        return True
    if head == ":op":
        team_name, _, args = rest.partition(" ")
        team_name = team_name.strip()
        if not team_name or not args.strip():
            print("[system] usage: :op <team> [@target] <text>")
            return True
        payload = _parse_op_payload(args)
        if payload is None:
            print("[system] could not parse :op payload")
            return True
        result = await shell.interact(team_name, payload)
        print(
            f"[system] op {team_name} target={payload.target}: "
            f"ok={result.ok} message_id={result.message_id} reason={result.reason!r}"
        )
        return True
    if head == ":ha":
        team_name, _, after = rest.partition(" ")
        sender, _, body = after.partition(" ")
        team_name = team_name.strip()
        sender = sender.strip()
        body = body.strip()
        if not team_name or not sender or not body:
            print("[system] usage: :ha <team> <sender> <text>")
            return True
        result = await shell.interact(
            team_name,
            HumanAgentMessage(body=body, sender=sender),
        )
        print(
            f"[system] ha {team_name} sender={sender}: "
            f"ok={result.ok} message_id={result.message_id} reason={result.reason!r}"
        )
        return True

    print(f"[system] unknown command: {raw}; type :quit to exit")
    return True


async def main() -> None:
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg = cfg.pop("runtime", {})
    base_spec = TeamAgentSpec.model_validate(cfg)

    specs = _build_specs(base_spec, runtime_cfg)
    session_id = runtime_cfg.get("session_id", "multi_team_session")

    print("=" * 60)
    print("Multi-team-per-session E2E")
    print(f"session_id = {session_id}")
    print(f"teams      = {', '.join(specs.keys())}")
    print("=" * 60)
    _print_help()

    await Runner.start()
    shell = MultiTeamSessionShell(specs, session_id)
    try:
        # Bootstrap: kick off both teams on the shared session so the user
        # can immediately see two ActiveTeam entries cohabit one session.
        for team_name in specs:
            await shell.start(team_name, runtime_cfg.get("initial_query", "hello"))
        await shell.status()

        while True:
            try:
                raw = (await ainput("\n[you] > ")).strip()
            except EOFError:
                break
            if not raw:
                continue
            if not await _handle_command(shell, raw):
                break
    finally:
        await shell.shutdown()
        await Runner.stop()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
