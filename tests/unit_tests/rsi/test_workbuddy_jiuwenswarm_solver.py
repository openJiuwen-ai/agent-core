# coding: utf-8
"""Tests for the standalone WorkBuddy JiuwenSwarm solver runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from examples.rsi.workbuddy_office import jiuwenswarm_solver as solver_module
from examples.rsi.workbuddy_office.jiuwenswarm_solver import (
    JiuwenSwarmSolverConfig,
    JiuwenSwarmSolverError,
    JiuwenSwarmSolverRuntime,
)


class _FakeProcess:
    def __init__(self, name: str) -> None:
        self.name = name
        self.pid = None
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = -9 if self.killed else 0
        return self.returncode


class _FakeWebSocket:
    def __init__(self, *, complete_chat: bool = True) -> None:
        self.complete_chat = complete_chat
        self.frames: list[str] = []
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        method = frame["method"]
        if method == "initialize":
            self.frames.append(json.dumps({"type": "event", "event": "connection.ack", "payload": {}}))
        elif method == "session.create":
            self.frames.append(json.dumps({"type": "res", "id": frame["id"], "ok": True}))
        elif method == "chat.send":
            self.frames.append(json.dumps({"type": "res", "id": frame["id"], "ok": True}))
            if self.complete_chat:
                self.frames.extend(
                    [
                        json.dumps(
                            {
                                "type": "event",
                                "event": "chat.delta",
                                "payload": {"content": "Inspected. "},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event",
                                "event": "chat.tool_call",
                                "payload": {
                                    "tool_call": {
                                        "tool_call_id": "tool_1",
                                        "name": "shell",
                                        "arguments": {"command": "ls"},
                                    }
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event",
                                "event": "chat.tool_result",
                                "payload": {"tool_call_id": "tool_1", "result": "ok"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event",
                                "event": "chat.delta",
                                "payload": {"content": "Complete."},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event",
                                "event": "chat.final",
                                "payload": {},
                            }
                        ),
                    ]
                )

    async def recv(self) -> str:
        if self.frames:
            return self.frames.pop(0)
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class _FakeAgentServerWebSocket:
    def __init__(self) -> None:
        self.frames = [json.dumps({"type": "event", "event": "connection.ack", "payload": {}})]
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        assert frame["method"] == "harness.packages.activate"
        self.frames.append(
            json.dumps(
                {
                    "protocol_version": "1.0",
                    "response_id": frame["request_id"],
                    "request_id": frame["request_id"],
                    "sequence": 0,
                    "is_final": True,
                    "status": "succeeded",
                    "response_kind": "e2a.complete",
                    "body": {
                        "result": {
                            "activated_package_id": frame["params"]["package_id"],
                            "loaded_resources": ["prompt", "tool", "rail"],
                        }
                    },
                }
            )
        )

    async def recv(self) -> str:
        return self.frames.pop(0)

    async def close(self) -> None:
        self.closed = True


def _install_task86_protocol_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_type: type,
) -> None:
    modules = {
        "jiuwenswarm": ModuleType("jiuwenswarm"),
        "jiuwenswarm.common": ModuleType("jiuwenswarm.common"),
        "jiuwenswarm.common.e2a": ModuleType("jiuwenswarm.common.e2a"),
        "jiuwenswarm.common.e2a.gateway_normalize": ModuleType("jiuwenswarm.common.e2a.gateway_normalize"),
        "jiuwenswarm.common.schema": ModuleType("jiuwenswarm.common.schema"),
        "jiuwenswarm.common.schema.message": ModuleType("jiuwenswarm.common.schema.message"),
        "jiuwenswarm.gateway": ModuleType("jiuwenswarm.gateway"),
        "jiuwenswarm.gateway.routing": ModuleType("jiuwenswarm.gateway.routing"),
        "jiuwenswarm.gateway.routing.agent_client": ModuleType("jiuwenswarm.gateway.routing.agent_client"),
    }

    def envelope(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    class ReqMethod:
        SESSION_CREATE = "session.create"
        CHAT_SEND = "chat.send"
        CHAT_ANSWER = "chat.answer"

    modules["jiuwenswarm.common.e2a.gateway_normalize"].e2a_from_agent_fields = envelope
    modules["jiuwenswarm.common.schema.message"].ReqMethod = ReqMethod
    modules["jiuwenswarm.gateway.routing.agent_client"].WebSocketAgentServerClient = client_type
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_merge_config_files_preserves_jiuwenswarm_defaults(tmp_path: Path) -> None:
    base_path = tmp_path / "config.yaml"
    overlay_path = tmp_path / "task86-overlay.yaml"
    base_path.write_text(
        "channels:\n  web:\n    enabled: true\nreact:\n  enable_task_loop: false\n  max_iterations: 20\n",
        encoding="utf-8",
    )
    overlay_path.write_text(
        "react:\n  enable_task_loop: true\nmodels:\n  defaults: []\n",
        encoding="utf-8",
    )

    solver_module._merge_config_files(
        base_path=str(base_path),
        overlay_path=str(overlay_path),
    )

    import yaml

    merged = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    assert merged["channels"]["web"]["enabled"] is True
    assert merged["react"] == {
        "enable_task_loop": True,
        "max_iterations": 20,
    }
    assert merged["models"] == {"defaults": []}


@pytest.mark.asyncio
async def test_task86_protocol_continues_plan_once_when_required_artifact_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FakeClient:
        instance: "FakeClient | None" = None

        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["ping_timeout"] > 0
            self.requests: list[dict[str, Any]] = []
            self.chat_round = 0
            FakeClient.instance = self

        async def connect(self, url: str) -> None:
            assert url == "ws://agent-server"

        async def disconnect(self) -> None:
            return None

        async def send_request(self, envelope: dict[str, Any]) -> Any:
            self.requests.append(envelope)
            return SimpleNamespace(ok=True, payload={})

        def send_request_stream(self, envelope: dict[str, Any]) -> Any:
            self.requests.append(envelope)
            self.chat_round += 1
            round_index = self.chat_round

            async def chunks() -> Any:
                if round_index == 1:
                    content = "Here's my plan before I start executing. Shall I proceed with this plan?"
                else:
                    artifact = workspace / "output" / "result.xlsx"
                    artifact.parent.mkdir(parents=True)
                    artifact.write_bytes(b"xlsx")
                    content = "Completed and validated the requested workbook."
                yield SimpleNamespace(
                    payload={"event_type": "chat.delta", "content": content},
                    is_complete=False,
                )
                yield SimpleNamespace(payload={"event_type": "chat.final"}, is_complete=True)

            return chunks()

    _install_task86_protocol_modules(monkeypatch, client_type=FakeClient)

    result = await solver_module._run_task86_agent_server_protocol(
        "ws://agent-server",
        session_id="case-001",
        content="Create the workbook.",
        workspace=str(workspace),
        required_artifacts=("output/result.xlsx",),
    )

    assert FakeClient.instance is not None
    chat_requests = [request for request in FakeClient.instance.requests if request["req_method"] == "chat.send"]
    assert len(chat_requests) == 2
    assert {request["session_id"] for request in chat_requests} == {"case-001"}
    assert chat_requests[0]["params"]["query"] == "Create the workbook."
    assert "do not ask for confirmation" in chat_requests[1]["params"]["query"]
    assert result["metadata"]["round_count"] == 2
    assert result["metadata"]["continuation_count"] == 1
    assert result["metadata"]["required_artifacts_after"]["complete"] is True
    assert [row["role"] for row in result["trajectory"]] == ["user", "assistant", "user", "assistant"]
    assert result["trajectory"][2]["automatic_continuation"] is True


def test_automatic_continuation_requires_plan_confirmation_and_missing_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing = solver_module._required_artifact_state(str(workspace), ("output/result.xlsx",))
    assert (
        solver_module._automatic_continuation_reason(
            final_response="Execution plan ready. Please confirm before I start executing.",
            artifact_state=missing,
        )
        == "plan_confirmation_with_required_artifact_missing"
    )
    assert (
        solver_module._automatic_continuation_reason(
            final_response=(
                "I've inspected the source data and prepared a plan. "
                "Shall I proceed with generating the file?"
            ),
            artifact_state=missing,
        )
        == "plan_confirmation_with_required_artifact_missing"
    )
    assert (
        solver_module._automatic_continuation_reason(
            final_response="I could not complete the task because the input is corrupt.",
            artifact_state=missing,
        )
        == ""
    )

    artifact = workspace / "output" / "result.xlsx"
    artifact.parent.mkdir()
    artifact.write_bytes(b"xlsx")
    complete = solver_module._required_artifact_state(str(workspace), ("output/result.xlsx",))
    assert (
        solver_module._automatic_continuation_reason(
            final_response="Execution plan ready. Please confirm before I start executing.",
            artifact_state=complete,
        )
        == ""
    )


def _install_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    websocket: _FakeWebSocket,
    agent_websocket: _FakeAgentServerWebSocket | None = None,
    ready_results: list[bool] | None = None,
    installed_version: str = "1.4.2",
) -> tuple[list[_FakeProcess], list[dict[str, Any]]]:
    processes: list[_FakeProcess] = []
    launches: list[dict[str, Any]] = []
    readiness = list(ready_results or [True])

    monkeypatch.setattr(solver_module, "_resolve_executable", lambda value: "C:/tools/python.exe")

    async def fake_version(executable: str, distribution_name: str, *, timeout_sec: float) -> str:
        assert executable == "C:/tools/python.exe"
        assert distribution_name == "jiuwenswarm"
        assert timeout_sec > 0
        return installed_version

    async def fake_spawn(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        log_file: Any,
    ) -> _FakeProcess:
        process = _FakeProcess(command[0])
        processes.append(process)
        launches.append(
            {
                "command": command,
                "cwd": cwd,
                "environment": environment,
            }
        )
        log_file.write((" ".join(command) + " started\n").encode())
        return process

    async def fake_ready(process: _FakeProcess, **kwargs: Any) -> bool:
        del process, kwargs
        return readiness.pop(0)

    async def fake_websocket(url: str, *, max_size: int) -> Any:
        assert max_size > 0
        assert url == "ws://127.0.0.1:18092"
        assert agent_websocket is not None
        return agent_websocket

    async def fake_task86_protocol(
        agent_server_url: str,
        *,
        session_id: str,
        content: str,
        workspace: str,
        required_artifacts: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        assert agent_server_url == "ws://127.0.0.1:18092"
        assert required_artifacts == ()
        websocket.sent.extend(
            [
                {
                    "method": "session.create",
                    "channel": "tui",
                    "params": {"session_id": session_id},
                },
                {
                    "method": "chat.send",
                    "channel": "tui",
                    "params": solver_module._task86_chat_params(
                        content=content,
                        workspace=workspace,
                    ),
                },
            ]
        )
        try:
            if not websocket.complete_chat:
                await asyncio.Future()
            return {
                "final_response": "Inspected. Complete.",
                "trajectory": [
                    {"role": "user", "content": content},
                    {
                        "role": "assistant",
                        "content": "Inspected. ",
                        "tool_calls": [
                            {
                                "id": "tool_1",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": '{"command": "ls"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "tool_1", "content": "ok"},
                    {"role": "assistant", "content": "Complete."},
                ],
                "metadata": {
                    "session_id": session_id,
                    "chat_acknowledged": True,
                    "request_channel": "tui",
                    "request_workspace_dir": workspace,
                },
            }
        finally:
            websocket.closed = True

    monkeypatch.setattr(solver_module, "_installed_distribution_version", fake_version)
    monkeypatch.setattr(solver_module, "_spawn_process", fake_spawn)
    monkeypatch.setattr(solver_module, "_wait_for_process_port", fake_ready)
    monkeypatch.setattr(solver_module, "_open_websocket", fake_websocket)
    monkeypatch.setattr(
        solver_module,
        "_run_task86_agent_server_protocol",
        fake_task86_protocol,
    )
    return processes, launches


@pytest.mark.asyncio
async def test_solver_runs_agent_plan_in_existing_workspace_and_returns_trajectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "existing.txt"
    marker.write_text("keep", encoding="utf-8")
    websocket = _FakeWebSocket()
    processes, launches = _install_runtime_fakes(monkeypatch, websocket=websocket)
    config = JiuwenSwarmSolverConfig(
        python_executable="C:/configured/python.exe",
        expected_version="1.4.2",
        agent_server_command=("agent-server", "--exact"),
        gateway_command=("gateway", "--exact"),
        log_dir=str(tmp_path / "logs"),
        environment={"API_KEY": "top-secret"},
    )

    result = await JiuwenSwarmSolverRuntime(config).solve(
        workspace=workspace,
        instruction="Create the requested office artifact.",
        system_overlay="Use only the mounted workspace.",
        session_id="case_001",
    )

    assert marker.read_text(encoding="utf-8") == "keep"
    assert [launch["command"] for launch in launches] == [
        ("agent-server", "--exact"),
    ]
    assert all(launch["cwd"] == workspace.resolve() for launch in launches)
    assert launches[0]["environment"]["JIUWENSWARM_WORKSPACE"] == str(workspace.resolve())
    assert launches[0]["environment"]["JIUWENSWARM_RUNTIME_PROFILE"] == "task86"
    assert launches[0]["environment"]["GATEWAY_PORT"] == "19001"
    assert launches[0]["environment"]["WEB_PORT"] == "19000"
    assert launches[0]["environment"]["WEB_PATH"] == "/ws"
    chat = next(frame for frame in websocket.sent if frame["method"] == "chat.send")
    assert chat["channel"] == "tui"
    assert chat["params"] == {
        "query": ("Use only the mounted workspace.\n\n---\n\nCreate the requested office artifact."),
        "mode": "agent.plan",
        "workspace_dir": str(workspace.resolve()),
        "cwd": str(workspace.resolve()),
        "trusted_dirs": [str(workspace.resolve())],
    }
    assert result.final_response == "Inspected. Complete."
    assert [row["role"] for row in result.trajectory] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.trajectory[1]["tool_calls"][0]["function"]["name"] == "shell"
    assert result.trajectory[2]["content"] == "ok"
    assert result.metadata["jiuwenswarm_version"] == "1.4.2"
    assert result.metadata["protocol_mode"] == "agent.plan"
    assert "started" in result.metadata["logs"]["agent_server"]["tail"]
    assert "top-secret" not in json.dumps(result.metadata)
    assert websocket.closed is True
    assert len(processes) == 1
    assert all(process.terminated for process in processes)


@pytest.mark.asyncio
async def test_solver_registers_and_activates_candidate_harness_after_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    candidate = tmp_path / "candidate_harness"
    candidate.mkdir()
    manifest = candidate / "harness_config.yaml"
    manifest.write_text(
        "schema_version: harness_config.v0.1\nname: office_candidate\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / ".jiuwenswarm" / "auto-harness" / "harness-packages.json"
    websocket = _FakeWebSocket()
    agent_websocket = _FakeAgentServerWebSocket()
    _install_runtime_fakes(
        monkeypatch,
        websocket=websocket,
        agent_websocket=agent_websocket,
    )
    runtime = JiuwenSwarmSolverRuntime(
        JiuwenSwarmSolverConfig(
            agent_server_command=("agent-server",),
            gateway_command=("gateway",),
            harness_packages_file=str(registry_path),
            log_dir=str(tmp_path / "logs"),
        )
    )

    result = await runtime.solve(
        workspace=workspace,
        instruction="Do the task",
        harness_config_path=manifest,
        package_id="pkg_rsi_office_candidate",
    )

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["active_package_ids"] == []
    assert registry["native_version"]["is_active"] is True
    package = registry["packages"][0]
    assert package["id"] == "pkg_rsi_office_candidate"
    assert package["runtime_path"] == str(candidate.resolve())
    assert package["config_path"] == str(manifest.resolve())
    assert package["is_active"] is False

    methods = [frame["method"] for frame in websocket.sent]
    assert methods == [
        "session.create",
        "chat.send",
    ]
    assert len(agent_websocket.sent) == 1
    activation = agent_websocket.sent[0]
    assert activation["protocol_version"] == "1.0"
    assert activation["channel"] == "tui"
    assert activation["session_id"]
    assert activation["params"] == {
        "package_id": "pkg_rsi_office_candidate",
        "mode": "agent",
        "workspace_dir": str(workspace.resolve()),
        "project_dir": str(workspace.resolve()),
        "cwd": str(workspace.resolve()),
        "trusted_dirs": [str(workspace.resolve())],
    }
    assert result.metadata["harness_package_id"] == "pkg_rsi_office_candidate"
    assert result.metadata["harness_config_path"] == str(manifest.resolve())
    assert result.metadata["harness_activation"]["loaded_resources"] == [
        "prompt",
        "tool",
        "rail",
    ]
    assert agent_websocket.closed is True


@pytest.mark.asyncio
async def test_solver_startup_failure_includes_log_tail_and_cleans_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    websocket = _FakeWebSocket()
    processes, _ = _install_runtime_fakes(
        monkeypatch,
        websocket=websocket,
        ready_results=[False],
    )
    runtime = JiuwenSwarmSolverRuntime(
        JiuwenSwarmSolverConfig(
            agent_server_command=("agent-server",),
            gateway_command=("gateway",),
            log_dir=str(tmp_path / "logs"),
        )
    )

    with pytest.raises(JiuwenSwarmSolverError) as exc_info:
        await runtime.solve(workspace=workspace, instruction="Do the task")

    assert "AgentServer failed to become ready" in str(exc_info.value)
    assert exc_info.value.diagnostics["logs"]["agent_server"]["path"].endswith("agent_server.log")
    assert len(processes) == 1
    assert all(process.terminated for process in processes)
    assert websocket.closed is False


@pytest.mark.asyncio
async def test_solver_request_timeout_closes_websocket_and_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    websocket = _FakeWebSocket(complete_chat=False)
    processes, _ = _install_runtime_fakes(monkeypatch, websocket=websocket)
    runtime = JiuwenSwarmSolverRuntime(
        JiuwenSwarmSolverConfig(
            agent_server_command=("agent-server",),
            gateway_command=("gateway",),
            request_timeout_sec=0.02,
            log_dir=str(tmp_path / "logs"),
        )
    )

    with pytest.raises(JiuwenSwarmSolverError, match="agent timed out"):
        await runtime.solve(workspace=workspace, instruction="Do the task")

    assert websocket.closed is True
    assert all(process.terminated for process in processes)


@pytest.mark.asyncio
async def test_solver_exact_version_mismatch_fails_before_process_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    websocket = _FakeWebSocket()
    processes, launches = _install_runtime_fakes(
        monkeypatch,
        websocket=websocket,
        installed_version="2.0.0",
    )
    runtime = JiuwenSwarmSolverRuntime(JiuwenSwarmSolverConfig(expected_version="1.4.2"))

    with pytest.raises(JiuwenSwarmSolverError, match="version mismatch"):
        await runtime.solve(workspace=workspace, instruction="Do the task")

    assert processes == []
    assert launches == []


def test_evaluator_config_mapping_uses_fixed_jiuwenswarm_fields() -> None:
    config = JiuwenSwarmSolverConfig.from_evaluator_config(
        {
            "jiuwenswarm_executable": "/opt/rsi/jiuwenswarm_solver.py",
            "jiuwenswarm_python": "/opt/jiuwenswarm/bin/python",
            "jiuwenswarm_expected_version": "1.4.2",
            "jiuwenswarm_startup_timeout_sec": 75,
            "jiuwenswarm_runtime_timeout_sec": 2100,
            "jiuwenswarm_runtime_profile": "task86-office",
        },
        environment={"MODEL_PROVIDER": "configured-outside-runtime"},
        log_dir="/tmp/jiuwenswarm-logs",
    )

    assert config.python_executable == "/opt/jiuwenswarm/bin/python"
    assert config.expected_version == "1.4.2"
    assert config.startup_timeout_sec == 75
    assert config.request_timeout_sec == 2100
    assert config.runtime_profile == "task86-office"
    assert config.environment == {"MODEL_PROVIDER": "configured-outside-runtime"}
    assert config.log_dir == "/tmp/jiuwenswarm-logs"


def test_cli_reads_request_json_and_emits_stable_result_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "instruction": "Edit the office files.",
                "system_overlay": "Operate only in /workspace.",
                "session_id": "case_cli_001",
                "required_artifacts": ["output/result.xlsx"],
            }
        ),
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    async def fake_solve(
        self: JiuwenSwarmSolverRuntime,
        **kwargs: Any,
    ) -> solver_module.JiuwenSwarmSolverResult:
        observed["config"] = self.config
        observed["request"] = kwargs
        return solver_module.JiuwenSwarmSolverResult(
            final_response="Done.",
            trajectory=[{"role": "assistant", "content": "Done."}],
            metadata={"runtime": "fake"},
        )

    monkeypatch.setattr(JiuwenSwarmSolverRuntime, "solve", fake_solve)

    exit_code = solver_module.main(
        [
            "--workspace",
            str(workspace),
            "--request-file",
            str(request_path),
            "--jiuwenswarm-python",
            "/opt/jiuwenswarm/bin/python",
            "--jiuwenswarm-expected-version",
            "1.4.2",
            "--jiuwenswarm-startup-timeout-sec",
            "75",
            "--jiuwenswarm-runtime-timeout-sec",
            "2100",
            "--jiuwenswarm-runtime-profile",
            "task86-office",
        ]
    )

    assert exit_code == 0
    assert observed["request"] == {
        "workspace": str(workspace),
        "instruction": "Edit the office files.",
        "system_overlay": "Operate only in /workspace.",
        "session_id": "case_cli_001",
        "harness_config_path": "",
        "package_id": "",
        "required_artifacts": ["output/result.xlsx"],
    }
    config = observed["config"]
    assert config.python_executable == "/opt/jiuwenswarm/bin/python"
    assert config.expected_version == "1.4.2"
    assert config.startup_timeout_sec == 75
    assert config.request_timeout_sec == 2100
    assert config.runtime_profile == "task86-office"

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == solver_module._OUTPUT_START
    assert lines[-1] == solver_module._OUTPUT_END
    payload = json.loads(lines[1])
    assert payload["ok"] is True
    assert payload["final_response"] == "Done."
    assert payload["trajectory"][0]["role"] == "assistant"
