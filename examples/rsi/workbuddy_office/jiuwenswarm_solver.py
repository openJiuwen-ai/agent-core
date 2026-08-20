#!/usr/bin/env python3
# coding: utf-8
"""Standalone JiuwenSwarm solver runtime for WorkBuddy workspaces.

The runtime drives JiuwenSwarm through the task86 direct AgentServer ``tui``
contract. It does not create or replace the benchmark workspace. Model
credentials are staged by the caller in the isolated case container and are
never returned in the structured result.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence
import uuid

logger = logging.getLogger(__name__)

_OUTPUT_START = "===JIUWENSWARM_SOLVER_OUTPUT_START==="
_OUTPUT_END = "===JIUWENSWARM_SOLVER_OUTPUT_END==="
_AUTOMATIC_PLAN_CONTINUATION = (
    "Proceed now with the plan you just described. This is an unattended benchmark: "
    "do not ask for confirmation or further clarification. Make reasonable conservative "
    "decisions, complete the task, create every required artifact at the exact requested "
    "path, and validate the artifact before finishing."
)


@dataclass(frozen=True, slots=True)
class JiuwenSwarmSolverConfig:
    """Process and protocol configuration for one JiuwenSwarm installation."""

    python_executable: str = sys.executable
    expected_version: str = ""
    distribution_name: str = "jiuwenswarm"
    agent_server_module: str = "jiuwenswarm.server.app_agentserver"
    gateway_module: str = "jiuwenswarm.gateway.app_gateway"
    agent_server_command: tuple[str, ...] = ()
    gateway_command: tuple[str, ...] = ()
    agent_host: str = "127.0.0.1"
    agent_port: int = 18092
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 19000
    gateway_internal_port: int = 19001
    websocket_path: str = "/ws"
    startup_timeout_sec: float = 60.0
    request_timeout_sec: float = 3600.0
    shutdown_timeout_sec: float = 5.0
    version_timeout_sec: float = 15.0
    max_websocket_message_bytes: int = 8 * 2**20
    runtime_profile: str = "task86"
    harness_packages_file: str = ""
    log_dir: str = ""
    log_tail_chars: int = 6000
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.python_executable.strip():
            raise ValueError("python_executable must not be empty")
        if not self.distribution_name.strip():
            raise ValueError("distribution_name must not be empty")
        if not self.runtime_profile.strip():
            raise ValueError("runtime_profile must not be empty")
        for name, port in (
            ("agent_port", self.agent_port),
            ("gateway_port", self.gateway_port),
            ("gateway_internal_port", self.gateway_internal_port),
        ):
            if not 1 <= int(port) <= 65535:
                raise ValueError(f"{name} must be between 1 and 65535")
        for name, value in (
            ("startup_timeout_sec", self.startup_timeout_sec),
            ("request_timeout_sec", self.request_timeout_sec),
            ("shutdown_timeout_sec", self.shutdown_timeout_sec),
            ("version_timeout_sec", self.version_timeout_sec),
        ):
            if float(value) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_evaluator_config(
        cls,
        evaluator_config: Any,
        *,
        environment: dict[str, str] | None = None,
        log_dir: str = "",
    ) -> JiuwenSwarmSolverConfig:
        """Map the fixed RSI evaluator fields to the in-process runtime.

        ``jiuwenswarm_executable`` identifies this standalone bridge when a
        backend launches it. Once inside the bridge, ``jiuwenswarm_python`` is
        the interpreter that owns the installed JiuwenSwarm distribution and
        starts both ``-m`` services.
        """
        return cls(
            python_executable=str(_config_value(evaluator_config, "jiuwenswarm_python", "") or sys.executable),
            expected_version=str(_config_value(evaluator_config, "jiuwenswarm_expected_version", "")),
            startup_timeout_sec=float(
                _config_value(
                    evaluator_config,
                    "jiuwenswarm_startup_timeout_sec",
                    120,
                )
            ),
            request_timeout_sec=float(
                _config_value(
                    evaluator_config,
                    "jiuwenswarm_runtime_timeout_sec",
                    3600,
                )
            ),
            runtime_profile=str(
                _config_value(
                    evaluator_config,
                    "jiuwenswarm_runtime_profile",
                    "task86",
                )
            ),
            log_dir=log_dir,
            environment=dict(environment or {}),
        )


@dataclass(frozen=True, slots=True)
class JiuwenSwarmSolverResult:
    """Normalized result consumable by a WorkBuddy case backend."""

    final_response: str
    trajectory: list[dict[str, Any]]
    metadata: dict[str, Any]


class JiuwenSwarmSolverError(RuntimeError):
    """Runtime failure with bounded external-process diagnostics."""

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        self.diagnostics = dict(diagnostics or {})
        detail = _format_diagnostic_message(message, self.diagnostics)
        super().__init__(detail)


class JiuwenSwarmSolverRuntime:
    """Run JiuwenSwarm against an already prepared WorkBuddy workspace."""

    def __init__(self, config: JiuwenSwarmSolverConfig) -> None:
        self.config = config

    async def solve(
        self,
        *,
        workspace: str | Path,
        instruction: str,
        system_overlay: str = "",
        session_id: str = "",
        harness_config_path: str | Path = "",
        package_id: str = "",
        required_artifacts: Sequence[str] = (),
    ) -> JiuwenSwarmSolverResult:
        """Execute one ``agent`` request without taking ownership of workspace."""
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise JiuwenSwarmSolverError(f"workspace does not exist: {workspace_path}")
        instruction = str(instruction or "").strip()
        if not instruction:
            raise JiuwenSwarmSolverError("instruction must not be empty")

        activation = _prepare_harness_activation(
            harness_config_path=harness_config_path,
            package_id=package_id,
            packages_file=self.config.harness_packages_file,
        )

        executable = _resolve_executable(self.config.python_executable)
        installed_version = await _installed_distribution_version(
            executable,
            self.config.distribution_name,
            timeout_sec=self.config.version_timeout_sec,
        )
        expected_version = self.config.expected_version.strip()
        if expected_version and installed_version != expected_version:
            raise JiuwenSwarmSolverError(
                f"JiuwenSwarm version mismatch: expected {expected_version!r}, installed {installed_version!r}"
            )

        log_dir = _allocate_log_dir(self.config.log_dir)
        agent_log_path = log_dir / "agent_server.log"
        gateway_log_path = log_dir / "gateway.log"
        agent_log = agent_log_path.open("ab", buffering=0)
        processes: list[tuple[str, Any]] = []
        started_at = time.monotonic()

        environment = _build_environment(
            self.config,
            workspace=workspace_path,
        )
        agent_command = self.config.agent_server_command or (
            executable,
            "-m",
            self.config.agent_server_module,
        )
        try:
            agent_process = await _spawn_process(
                tuple(agent_command),
                cwd=workspace_path,
                environment=environment,
                log_file=agent_log,
            )
            processes.append(("agent_server", agent_process))
            if not await _wait_for_process_port(
                agent_process,
                host=self.config.agent_host,
                port=self.config.agent_port,
                timeout_sec=self.config.startup_timeout_sec,
            ):
                raise self._runtime_error(
                    f"AgentServer failed to become ready at {self.config.agent_host}:{self.config.agent_port}",
                    agent_log_path=agent_log_path,
                    gateway_log_path=gateway_log_path,
                    processes=processes,
                )

            protocol_session_id = session_id.strip() or f"workbuddy_{uuid.uuid4().hex[:12]}"
            agent_server_url = f"ws://{self.config.agent_host}:{self.config.agent_port}"
            harness_activation: dict[str, Any] = {}
            if activation:
                try:
                    harness_activation = await asyncio.wait_for(
                        _activate_harness_package(
                            agent_server_url,
                            session_id=protocol_session_id,
                            workspace=str(workspace_path),
                            package_id=activation["package_id"],
                            max_size=self.config.max_websocket_message_bytes,
                            shutdown_timeout_sec=self.config.shutdown_timeout_sec,
                        ),
                        timeout=self.config.request_timeout_sec,
                    )
                except asyncio.TimeoutError as exc:
                    raise self._runtime_error(
                        f"JiuwenSwarm Harness activation timed out after {self.config.request_timeout_sec:.1f}s",
                        agent_log_path=agent_log_path,
                        gateway_log_path=gateway_log_path,
                        processes=processes,
                    ) from exc

            full_instruction = _compose_instruction(instruction, system_overlay)
            try:
                protocol_result = await asyncio.wait_for(
                    _run_task86_agent_server_protocol(
                        agent_server_url,
                        session_id=protocol_session_id,
                        content=full_instruction,
                        workspace=str(workspace_path),
                        required_artifacts=tuple(required_artifacts),
                    ),
                    timeout=self.config.request_timeout_sec,
                )
            except asyncio.TimeoutError as exc:
                raise self._runtime_error(
                    f"JiuwenSwarm agent timed out after {self.config.request_timeout_sec:.1f}s",
                    agent_log_path=agent_log_path,
                    gateway_log_path=gateway_log_path,
                    processes=processes,
                ) from exc

            elapsed_sec = time.monotonic() - started_at
            metadata = {
                **protocol_result["metadata"],
                "harness_activation": harness_activation,
                "runtime": "jiuwenswarm_agentserver_task86",
                "protocol_mode": "agent.plan",
                "protocol_channel": "tui",
                "workspace": str(workspace_path),
                "python_executable": executable,
                "jiuwenswarm_distribution": self.config.distribution_name,
                "jiuwenswarm_version": installed_version,
                "expected_version": expected_version,
                "runtime_profile": self.config.runtime_profile,
                "harness_config_path": (activation["config_path"] if activation else ""),
                "harness_package_id": (activation["package_id"] if activation else ""),
                "system_overlay_applied": bool(system_overlay.strip()),
                "elapsed_sec": elapsed_sec,
                "agent_server_endpoint": f"{self.config.agent_host}:{self.config.agent_port}",
                "processes": _process_metadata(processes),
                "logs": self._log_diagnostics(agent_log_path, gateway_log_path),
            }
            return JiuwenSwarmSolverResult(
                final_response=protocol_result["final_response"],
                trajectory=protocol_result["trajectory"],
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except JiuwenSwarmSolverError:
            raise
        except Exception as exc:
            raise self._runtime_error(
                f"JiuwenSwarm solver failed: {exc}",
                agent_log_path=agent_log_path,
                gateway_log_path=gateway_log_path,
                processes=processes,
            ) from exc
        finally:
            try:
                await asyncio.shield(
                    _stop_processes(
                        processes,
                        timeout_sec=self.config.shutdown_timeout_sec,
                    )
                )
            except Exception as exc:  # Cleanup must not mask the primary result.
                logger.warning("JiuwenSwarm process cleanup failed: %s", exc)
            agent_log.close()

    def _runtime_error(
        self,
        message: str,
        *,
        agent_log_path: Path,
        gateway_log_path: Path,
        processes: list[tuple[str, Any]],
    ) -> JiuwenSwarmSolverError:
        diagnostics = {
            "processes": _process_metadata(processes),
            "logs": self._log_diagnostics(agent_log_path, gateway_log_path),
        }
        return JiuwenSwarmSolverError(message, diagnostics=diagnostics)

    def _log_diagnostics(self, agent_log_path: Path, gateway_log_path: Path) -> dict[str, Any]:
        return {
            "agent_server": {
                "path": str(agent_log_path),
                "tail": _read_log_tail(agent_log_path, self.config.log_tail_chars),
            },
            "gateway": {
                "path": str(gateway_log_path),
                "tail": _read_log_tail(gateway_log_path, self.config.log_tail_chars),
            },
        }


async def _run_task86_agent_server_protocol(
    agent_server_url: str,
    *,
    session_id: str,
    content: str,
    workspace: str,
    required_artifacts: Sequence[str] = (),
) -> dict[str, Any]:
    """Use the same direct AgentServer ``tui`` contract as task 86."""
    try:
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod
        from jiuwenswarm.gateway.routing.agent_client import (
            WebSocketAgentServerClient,
        )
    except ImportError as exc:
        raise JiuwenSwarmSolverError("JiuwenSwarm task86 AgentServer client is unavailable") from exc

    client = WebSocketAgentServerClient(
        ping_interval=30.0,
        ping_timeout=300.0,
    )
    await client.connect(agent_server_url)
    try:
        create_envelope = e2a_from_agent_fields(
            request_id=f"session_{uuid.uuid4().hex[:10]}",
            channel_id="tui",
            session_id=session_id,
            req_method=ReqMethod.SESSION_CREATE,
            params={"session_id": session_id},
            is_stream=False,
        )
        create_response = await client.send_request(create_envelope)
        _require_task86_response_ok(create_response, "session.create")

        async def consume_round(round_content: str) -> dict[str, Any]:
            chat_envelope = e2a_from_agent_fields(
                request_id=f"chat_{uuid.uuid4().hex[:10]}",
                channel_id="tui",
                session_id=session_id,
                req_method=ReqMethod.CHAT_SEND,
                params=_task86_chat_params(content=round_content, workspace=workspace),
                is_stream=True,
            )
            chunks = client.send_request_stream(chat_envelope)
            final_response = ""
            messages: list[dict[str, Any]] = []
            assistant: dict[str, Any] = {"role": "assistant", "content": ""}
            tool_calls: list[dict[str, Any]] = []
            tool_results: dict[str, str] = {}
            observed_events: list[str] = []
            ask_user_count = 0

            def flush_assistant() -> None:
                nonlocal assistant, tool_calls
                if not assistant.get("content") and not tool_calls:
                    return
                row = dict(assistant)
                if tool_calls:
                    row["tool_calls"] = list(tool_calls)
                messages.append(row)
                for tool_call in tool_calls:
                    tool_id = str(tool_call.get("id", "") or "")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_results.get(tool_id, ""),
                        }
                    )
                assistant = {"role": "assistant", "content": ""}
                tool_calls = []

            async for chunk in chunks:
                payload = getattr(chunk, "payload", None)
                payload = payload if isinstance(payload, dict) else {}
                event_name = str(payload.get("event_type", "") or "")
                observed_events.append(event_name)
                if event_name == "chat.delta":
                    if tool_calls and assistant.get("content"):
                        flush_assistant()
                    delta = str(payload.get("content", "") or "")
                    final_response += delta
                    assistant["content"] = str(assistant.get("content", "")) + delta
                elif event_name == "chat.tool_call":
                    raw_tool_call = payload.get("tool_call")
                    raw_tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
                    tool_id = str(
                        raw_tool_call.get("tool_call_id") or raw_tool_call.get("id") or f"tool_{len(tool_calls)}"
                    )
                    arguments = raw_tool_call.get("arguments", {})
                    tool_calls.append(
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": str(raw_tool_call.get("name", "unknown") or "unknown"),
                                "arguments": (
                                    json.dumps(arguments, ensure_ascii=False)
                                    if isinstance(arguments, (dict, list))
                                    else str(arguments)
                                ),
                            },
                        }
                    )
                elif event_name == "chat.tool_result":
                    tool_id = str(payload.get("tool_call_id", "") or "")
                    if tool_id:
                        tool_results[tool_id] = _stringify_tool_result(payload.get("result", ""))
                elif event_name == "chat.error":
                    error_type = payload.get("error_type") or payload.get("code") or "AGENT_ERROR"
                    error = payload.get("error") or payload.get("content") or payload
                    raise JiuwenSwarmSolverError(f"agent reported error: {error_type}: {error}")
                elif event_name == "chat.ask_user_question":
                    ask_user_count += 1
                    answer_envelope = e2a_from_agent_fields(
                        request_id=f"answer_{uuid.uuid4().hex[:10]}",
                        channel_id="tui",
                        session_id=session_id,
                        req_method=ReqMethod.CHAT_ANSWER,
                        params={"answer": "continue"},
                        is_stream=False,
                    )
                    answer_response = await client.send_request(answer_envelope)
                    _require_task86_response_ok(answer_response, "chat.answer")

                if bool(getattr(chunk, "is_complete", False)):
                    final_payload = str(payload.get("final_response") or payload.get("content") or "")
                    if final_payload and not final_response:
                        final_response = final_payload
                        assistant["content"] = final_payload
                    flush_assistant()
                    break
            return {
                "final_response": final_response,
                "messages": messages,
                "observed_events": observed_events,
                "ask_user_count": ask_user_count,
            }

        artifact_before = _required_artifact_state(workspace, required_artifacts)
        first_round = await consume_round(content)
        rounds = [first_round]
        trajectory = [{"role": "user", "content": content}, *first_round["messages"]]
        continuation_reason = _automatic_continuation_reason(
            final_response=first_round["final_response"],
            artifact_state=artifact_before,
        )
        if continuation_reason:
            continuation = _AUTOMATIC_PLAN_CONTINUATION
            trajectory.append(
                {
                    "role": "user",
                    "content": continuation,
                    "automatic_continuation": True,
                    "reason": continuation_reason,
                }
            )
            second_round = await consume_round(continuation)
            rounds.append(second_round)
            trajectory.extend(second_round["messages"])

        final_response = str(rounds[-1]["final_response"] or first_round["final_response"])
        observed_events = [event for round_result in rounds for event in round_result["observed_events"]]
        artifact_after = _required_artifact_state(workspace, required_artifacts)
        return {
            "final_response": final_response,
            "trajectory": trajectory,
            "metadata": {
                "session_id": session_id,
                "chat_acknowledged": True,
                "event_count": len(observed_events),
                "observed_events": observed_events,
                "request_channel": "tui",
                "request_workspace_dir": workspace,
                "round_count": len(rounds),
                "continuation_count": len(rounds) - 1,
                "continuation_reason": continuation_reason,
                "ask_user_count": sum(int(round_result["ask_user_count"]) for round_result in rounds),
                "required_artifacts_before": artifact_before,
                "required_artifacts_after": artifact_after,
            },
        }
    finally:
        await client.disconnect()


def _task86_chat_params(*, content: str, workspace: str) -> dict[str, Any]:
    return {
        "query": content,
        "mode": "agent.plan",
        "workspace_dir": workspace,
        "cwd": workspace,
        "trusted_dirs": [workspace],
    }


def _required_artifact_state(workspace: str, required_artifacts: Sequence[str]) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    artifacts: list[dict[str, Any]] = []
    for raw in required_artifacts:
        value = str(raw or "").strip().replace("\\", "/")
        relative = Path(value)
        if not value or relative.is_absolute() or ".." in relative.parts:
            raise JiuwenSwarmSolverError(f"invalid required artifact path: {value!r}")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise JiuwenSwarmSolverError(f"required artifact escapes workspace: {value!r}") from exc
        exists = target.is_file()
        size = target.stat().st_size if exists else 0
        artifacts.append(
            {
                "path": relative.as_posix(),
                "exists": exists,
                "nonempty": bool(exists and size > 0),
                "size": size,
            }
        )
    return {
        "declared": bool(artifacts),
        "complete": bool(artifacts) and all(item["nonempty"] for item in artifacts),
        "artifacts": artifacts,
    }


def _automatic_continuation_reason(*, final_response: str, artifact_state: dict[str, Any]) -> str:
    if not bool(artifact_state.get("declared")) or bool(artifact_state.get("complete")):
        return ""
    text = " ".join(str(final_response or "")[-5000:].lower().split())
    plan_markers = (
        "execution plan",
        "here's my plan",
        "here is my plan",
        "prepared a plan",
        "plan before i start",
        "execution approach",
        "执行计划",
        "处理计划",
        "方案",
    )
    confirmation_markers = (
        "shall i proceed",
        "shall i proceed with generating",
        "would you like me to proceed",
        "please confirm",
        "decision i'd like your input",
        "decision i would like your input",
        "before i start executing",
        "是否继续",
        "请确认",
        "需要您确认",
        "是否开始",
    )
    if any(marker in text for marker in plan_markers) and any(marker in text for marker in confirmation_markers):
        return "plan_confirmation_with_required_artifact_missing"
    return ""


def _require_task86_response_ok(response: Any, operation: str) -> None:
    if bool(getattr(response, "ok", False)):
        return
    payload = getattr(response, "payload", None)
    payload = payload if isinstance(payload, dict) else {}
    code = payload.get("code", "UNKNOWN")
    error = payload.get("error") or payload.get("message") or payload
    raise JiuwenSwarmSolverError(f"{operation} failed: {code}: {error}")


async def _activate_harness_package(
    agent_server_url: str,
    *,
    session_id: str,
    workspace: str,
    package_id: str,
    max_size: int,
    shutdown_timeout_sec: float,
) -> dict[str, Any]:
    """Activate one package through AgentServer's E2A-only control method."""
    websocket = await _open_websocket(agent_server_url, max_size=max_size)
    try:
        ready = await _receive_frame(websocket, peer_name="AgentServer")
        if ready.get("type") != "event" or ready.get("event") != "connection.ack":
            raise JiuwenSwarmSolverError("AgentServer did not send connection.ack before Harness activation")

        request_id = f"harness_{uuid.uuid4().hex[:10]}"
        await _send_frame(
            websocket,
            {
                "protocol_version": "1.0",
                "request_id": request_id,
                "session_id": session_id,
                "identity_origin": "user",
                "channel": "tui",
                "method": "harness.packages.activate",
                "params": {
                    "package_id": package_id,
                    "mode": "agent",
                    "workspace_dir": workspace,
                    "project_dir": workspace,
                    "cwd": workspace,
                    "trusted_dirs": [workspace],
                },
                "is_stream": False,
            },
        )
        return await _wait_for_agent_server_response(
            websocket,
            request_id=request_id,
        )
    finally:
        await _close_websocket(websocket, timeout_sec=shutdown_timeout_sec)


async def _wait_for_agent_server_response(
    websocket: Any,
    *,
    request_id: str,
) -> dict[str, Any]:
    while True:
        frame = await _receive_frame(websocket, peer_name="AgentServer")
        if str(frame.get("request_id", "")) != request_id:
            continue

        if "ok" in frame:
            if not frame.get("ok", False):
                raise JiuwenSwarmSolverError(f"Harness activation failed: {_agent_server_response_error(frame)}")
            payload = frame.get("payload")
            return dict(payload) if isinstance(payload, dict) else {}

        status = str(frame.get("status", "") or "")
        response_kind = str(frame.get("response_kind", "") or "")
        body = frame.get("body")
        body = body if isinstance(body, dict) else {}
        if status == "failed" or response_kind == "e2a.error":
            raise JiuwenSwarmSolverError(f"Harness activation failed: {_agent_server_response_error(frame)}")
        if status != "succeeded" or response_kind != "e2a.complete":
            raise JiuwenSwarmSolverError(
                "AgentServer returned an unsupported Harness activation response: "
                f"status={status!r}, response_kind={response_kind!r}"
            )
        result = body.get("result")
        return dict(result) if isinstance(result, dict) else {}


def _agent_server_response_error(frame: dict[str, Any]) -> str:
    payload = frame.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("message") or payload)
    body = frame.get("body")
    if isinstance(body, dict):
        details = body.get("details")
        if isinstance(details, dict):
            return str(details.get("error") or details.get("message") or details)
        return str(body.get("message") or body)
    return "unknown AgentServer error"


async def _wait_for_response(
    websocket: Any,
    *,
    request_id: str,
    accepted_event: str = "",
) -> dict[str, Any]:
    while True:
        frame = await _receive_frame(websocket)
        if accepted_event and frame.get("type") == "event" and frame.get("event") == accepted_event:
            return frame
        if frame.get("type") != "res" or frame.get("id") != request_id:
            continue
        if not frame.get("ok", False):
            raise JiuwenSwarmSolverError(f"request {request_id} failed: {_response_error(frame)}")
        return frame


async def _send_frame(websocket: Any, frame: dict[str, Any]) -> None:
    await websocket.send(json.dumps(frame, ensure_ascii=False))


async def _receive_frame(
    websocket: Any,
    *,
    peer_name: str = "Gateway",
) -> dict[str, Any]:
    raw = await websocket.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        frame = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise JiuwenSwarmSolverError(f"{peer_name} returned invalid JSON: {raw!r}") from exc
    if not isinstance(frame, dict):
        raise JiuwenSwarmSolverError(f"{peer_name} frame must be a JSON object")
    return frame


async def _open_websocket(url: str, *, max_size: int) -> Any:
    try:
        from websockets.legacy.client import connect
    except ImportError:
        from websockets import connect
    return await connect(url, max_size=max_size)


async def _close_websocket(websocket: Any, *, timeout_sec: float) -> None:
    try:
        result = websocket.close()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning("JiuwenSwarm WebSocket cleanup timed out")
    except Exception as exc:
        logger.warning("JiuwenSwarm WebSocket cleanup failed: %s", exc)


async def _spawn_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_file: Any,
) -> Any:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        **kwargs,
    )


async def _wait_for_process_port(
    process: Any,
    *,
    host: str,
    port: int,
    timeout_sec: float,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if process.returncode is not None:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=min(0.5, max(0.01, deadline - time.monotonic())),
            )
            del reader
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.1)
    return False


async def _stop_processes(
    processes: list[tuple[str, Any]],
    *,
    timeout_sec: float,
) -> None:
    for name, process in reversed(processes):
        if process.returncode is not None:
            continue
        _terminate_process(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning("JiuwenSwarm %s did not terminate; killing it", name)
            _kill_process(process)
            await asyncio.wait_for(process.wait(), timeout=timeout_sec)


def _terminate_process(process: Any) -> None:
    try:
        if os.name != "nt" and process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass


def _kill_process(process: Any) -> None:
    try:
        if os.name != "nt" and process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


async def _installed_distribution_version(
    executable: str,
    distribution_name: str,
    *,
    timeout_sec: float,
) -> str:
    script = f"from importlib.metadata import version; print(version({distribution_name!r}))"
    process = await asyncio.create_subprocess_exec(
        executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise JiuwenSwarmSolverError(f"timed out checking {distribution_name} version") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise JiuwenSwarmSolverError(f"failed to query {distribution_name} version with {executable}: {detail}")
    installed = stdout.decode("utf-8", errors="replace").strip()
    if not installed:
        raise JiuwenSwarmSolverError(f"{distribution_name} returned an empty installed version")
    return installed


def _resolve_executable(configured: str) -> str:
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    resolved = shutil.which(configured)
    if resolved:
        return str(Path(resolved).resolve())
    raise JiuwenSwarmSolverError(f"configured Python executable not found: {configured}")


def _build_environment(
    config: JiuwenSwarmSolverConfig,
    *,
    workspace: Path,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in config.environment.items()})
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "AGENT_SERVER_HOST": config.agent_host,
            "AGENT_SERVER_PORT": str(config.agent_port),
            "GATEWAY_HOST": config.gateway_host,
            "GATEWAY_PORT": str(config.gateway_internal_port),
            "WEB_HOST": config.gateway_host,
            "WEB_PORT": str(config.gateway_port),
            "WEB_PATH": config.websocket_path,
            "JIUWENSWARM_WORKSPACE": str(workspace),
            "JIUWENSWARM_RUNTIME_PROFILE": config.runtime_profile,
            "WORKSPACE_DIR": str(workspace),
        }
    )
    return environment


def _prepare_harness_activation(
    *,
    harness_config_path: str | Path,
    package_id: str,
    packages_file: str,
) -> dict[str, str] | None:
    raw_config_path = str(harness_config_path or "").strip()
    requested_package_id = str(package_id or "").strip()
    if not raw_config_path:
        if requested_package_id:
            return {"package_id": requested_package_id, "config_path": ""}
        return None

    candidate = Path(raw_config_path).expanduser().resolve()
    config_path = candidate / "harness_config.yaml" if candidate.is_dir() else candidate
    if not config_path.is_file():
        raise JiuwenSwarmSolverError(f"harness config does not exist: {config_path}")
    if config_path.name != "harness_config.yaml":
        raise JiuwenSwarmSolverError("harness_config_path must name harness_config.yaml or its parent directory")

    runtime_path = config_path.parent.resolve()
    resolved_package_id = requested_package_id or _derived_package_id(runtime_path)
    registry_path = (
        Path(packages_file).expanduser().resolve()
        if packages_file.strip()
        else Path.home() / ".jiuwenswarm" / "auto-harness" / "harness-packages.json"
    )
    _upsert_harness_package_registry(
        registry_path=registry_path,
        package_id=resolved_package_id,
        runtime_path=runtime_path,
        config_path=config_path.resolve(),
    )
    return {
        "package_id": resolved_package_id,
        "config_path": str(config_path.resolve()),
        "registry_path": str(registry_path),
    }


def _derived_package_id(runtime_path: Path) -> str:
    path_digest = hashlib.sha256(str(runtime_path).encode("utf-8")).hexdigest()[:12]
    safe_name = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_" for character in runtime_path.name
    ).strip("_")
    return f"pkg_rsi_{safe_name or 'harness'}_{path_digest}"


def _upsert_harness_package_registry(
    *,
    registry_path: Path,
    package_id: str,
    runtime_path: Path,
    config_path: Path,
) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JiuwenSwarmSolverError(f"failed to read JiuwenSwarm harness registry {registry_path}: {exc}") from exc
        if not isinstance(registry, dict):
            raise JiuwenSwarmSolverError(f"JiuwenSwarm harness registry must be a JSON object: {registry_path}")
    else:
        registry = {}

    raw_packages = registry.get("packages", [])
    if not isinstance(raw_packages, list):
        raise JiuwenSwarmSolverError(f"JiuwenSwarm harness registry packages must be a list: {registry_path}")
    packages = [item for item in raw_packages if isinstance(item, dict)]
    now = datetime.now(timezone.utc).isoformat()
    package = {
        "id": package_id,
        "extension_name": runtime_path.name,
        "runtime_path": str(runtime_path),
        "config_path": str(config_path),
        "created_at": now,
        "is_active": False,
        "version_label": "",
        "description": "RSI evaluation candidate",
    }
    replaced = False
    for index, existing in enumerate(packages):
        if existing.get("id") == package_id:
            package["created_at"] = existing.get("created_at", now)
            packages[index] = package
            replaced = True
            break
    if not replaced:
        packages.append(package)

    registry.update(
        {
            "packages": packages,
            # AgentServer clears activation state during startup. Activation is
            # deliberately sent over WebSocket only after the server is ready.
            "active_package_ids": [],
            "native_version": {
                "id": "native",
                "extension_name": "Native Agent",
                "is_active": True,
            },
            "last_updated": now,
        }
    )
    registry.pop("active_package_id", None)
    registry.pop("active_extension_name", None)
    temporary_path = registry_path.with_name(f".{registry_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, registry_path)
    except OSError as exc:
        raise JiuwenSwarmSolverError(f"failed to write JiuwenSwarm harness registry {registry_path}: {exc}") from exc
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _allocate_log_dir(configured: str) -> Path:
    if configured.strip():
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="rsi_jiuwenswarm_"))


def _compose_instruction(instruction: str, system_overlay: str) -> str:
    overlay = str(system_overlay or "").strip()
    return instruction if not overlay else f"{overlay}\n\n---\n\n{instruction}"


def _process_metadata(processes: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "pid": getattr(process, "pid", None),
            "returncode": getattr(process, "returncode", None),
        }
        for name, process in processes
    ]


def _read_log_tail(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as file:
            if path.stat().st_size > limit:
                file.seek(-limit, os.SEEK_END)
            return file.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _response_error(frame: dict[str, Any]) -> str:
    error = frame.get("error", "unknown")
    if isinstance(error, (dict, list)):
        return json.dumps(error, ensure_ascii=False)
    return str(error)


def _stringify_tool_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _format_diagnostic_message(message: str, diagnostics: dict[str, Any]) -> str:
    log_lines: list[str] = []
    logs = diagnostics.get("logs")
    if isinstance(logs, dict):
        for name in ("gateway", "agent_server"):
            item = logs.get(name)
            if not isinstance(item, dict):
                continue
            tail = str(item.get("tail", "") or "").strip()
            path = str(item.get("path", "") or "")
            if tail:
                log_lines.append(f"{name} log ({path}):\n{tail}")
    if not log_lines:
        return message
    return f"{message}\n" + "\n".join(log_lines)


def _config_value(config: Any, name: str, default: Any) -> Any:
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def _parse_cli_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run JiuwenSwarm AgentServer/Gateway against an existing WorkBuddy "
            "workspace. Requests and results use JSON; service logs go to files."
        )
    )
    parser.add_argument("--workspace", default="/workspace")
    request_source = parser.add_mutually_exclusive_group()
    request_source.add_argument("--instruction")
    request_source.add_argument(
        "--request-file",
        help="JSON request path, or '-' to read JSON from stdin",
    )
    overlay_source = parser.add_mutually_exclusive_group()
    overlay_source.add_argument("--system-overlay", default="")
    overlay_source.add_argument("--system-overlay-file")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--harness-config-path",
        default="",
        help="Candidate harness_config.yaml path or its runtime-extension directory",
    )
    parser.add_argument("--package-id", default="")
    parser.add_argument("--harness-packages-file", default="")
    parser.add_argument("--jiuwenswarm-python", default=sys.executable)
    parser.add_argument("--jiuwenswarm-expected-version", default="")
    parser.add_argument(
        "--jiuwenswarm-startup-timeout-sec",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--jiuwenswarm-runtime-timeout-sec",
        type=float,
        default=3600.0,
    )
    parser.add_argument("--jiuwenswarm-runtime-profile", default="task86")
    parser.add_argument("--agent-host", default="127.0.0.1")
    parser.add_argument("--agent-port", type=int, default=18092)
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=19000)
    parser.add_argument("--gateway-internal-port", type=int, default=19001)
    parser.add_argument("--websocket-path", default="/ws")
    parser.add_argument("--shutdown-timeout-sec", type=float, default=5.0)
    parser.add_argument("--version-timeout-sec", type=float, default=15.0)
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--merge-config-base", default="")
    parser.add_argument("--merge-config-overlay", default="")
    return parser.parse_args(argv)


def _deep_merge_config(
    base: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_config(existing, value)
        else:
            merged[key] = value
    return merged


def _merge_config_files(*, base_path: str, overlay_path: str) -> None:
    import yaml

    base_file = Path(base_path).expanduser().resolve()
    overlay_file = Path(overlay_path).expanduser().resolve()
    base = yaml.safe_load(base_file.read_text(encoding="utf-8")) or {}
    overlay = yaml.safe_load(overlay_file.read_text(encoding="utf-8")) or {}
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        raise ValueError("JiuwenSwarm base config and overlay must be mappings")
    merged = _deep_merge_config(base, overlay)
    temp_file = base_file.with_suffix(base_file.suffix + ".tmp")
    temp_file.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temp_file.replace(base_file)


def _read_cli_request(args: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {}
    if args.request_file:
        if args.request_file == "-":
            raw_request = sys.stdin.read()
        else:
            raw_request = Path(args.request_file).read_text(encoding="utf-8")
        parsed = json.loads(raw_request)
        if not isinstance(parsed, dict):
            raise ValueError("request JSON must be an object")
        request = parsed

    instruction = str(request.get("instruction", args.instruction or "")).strip()
    if not instruction:
        raise ValueError("instruction is required")
    if args.system_overlay_file:
        system_overlay = Path(args.system_overlay_file).read_text(encoding="utf-8")
    else:
        system_overlay = str(request.get("system_overlay", args.system_overlay or ""))
    session_id = str(request.get("session_id", args.session_id or ""))
    required_artifacts = request.get("required_artifacts", [])
    if not isinstance(required_artifacts, list) or not all(isinstance(item, str) for item in required_artifacts):
        raise ValueError("required_artifacts must be a list of relative paths")
    return {
        "instruction": instruction,
        "system_overlay": system_overlay,
        "session_id": session_id,
        "harness_config_path": str(request.get("harness_config_path", args.harness_config_path or "")),
        "package_id": str(request.get("package_id", args.package_id or "")),
        "required_artifacts": required_artifacts,
    }


def _emit_cli_output(payload: dict[str, Any]) -> None:
    print(_OUTPUT_START, flush=True)
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    print(_OUTPUT_END, flush=True)


def main(argv: list[str] | None = None) -> int:
    """Container-friendly CLI entry point.

    A backend can copy this single file into the WorkBuddy image and invoke it
    with ``--workspace /workspace --request-file -``. The request arrives on
    stdin, while the bounded JSON result is emitted between stable markers.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        args = _parse_cli_args(argv)
        if args.merge_config_base or args.merge_config_overlay:
            if not args.merge_config_base or not args.merge_config_overlay:
                raise ValueError("--merge-config-base and --merge-config-overlay must be used together")
            _merge_config_files(
                base_path=args.merge_config_base,
                overlay_path=args.merge_config_overlay,
            )
            return 0
        request = _read_cli_request(args)
        runtime = JiuwenSwarmSolverRuntime(
            JiuwenSwarmSolverConfig(
                python_executable=args.jiuwenswarm_python,
                expected_version=args.jiuwenswarm_expected_version,
                agent_host=args.agent_host,
                agent_port=args.agent_port,
                gateway_host=args.gateway_host,
                gateway_port=args.gateway_port,
                gateway_internal_port=args.gateway_internal_port,
                websocket_path=args.websocket_path,
                startup_timeout_sec=args.jiuwenswarm_startup_timeout_sec,
                request_timeout_sec=args.jiuwenswarm_runtime_timeout_sec,
                shutdown_timeout_sec=args.shutdown_timeout_sec,
                version_timeout_sec=args.version_timeout_sec,
                runtime_profile=args.jiuwenswarm_runtime_profile,
                harness_packages_file=args.harness_packages_file,
                log_dir=args.log_dir,
            )
        )
        result = asyncio.run(
            runtime.solve(
                workspace=args.workspace,
                instruction=request["instruction"],
                system_overlay=request["system_overlay"],
                session_id=request["session_id"],
                harness_config_path=request["harness_config_path"],
                package_id=request["package_id"],
                required_artifacts=request["required_artifacts"],
            )
        )
        _emit_cli_output(
            {
                "ok": True,
                "final_response": result.final_response,
                "trajectory": result.trajectory,
                "metadata": result.metadata,
            }
        )
        return 0
    except (JiuwenSwarmSolverError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics = exc.diagnostics if isinstance(exc, JiuwenSwarmSolverError) else {}
        _emit_cli_output(
            {
                "ok": False,
                "error": str(exc),
                "diagnostics": diagnostics,
            }
        )
        return 1


__all__ = [
    "JiuwenSwarmSolverConfig",
    "JiuwenSwarmSolverError",
    "JiuwenSwarmSolverResult",
    "JiuwenSwarmSolverRuntime",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
