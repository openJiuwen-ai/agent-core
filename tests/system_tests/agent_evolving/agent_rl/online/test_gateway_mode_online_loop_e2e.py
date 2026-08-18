"""Opt-in E2E for real JiuwenSwarm processes and gateway trajectory collection.

This test starts a real JiuwenSwarm AgentServer, JiuwenSwarm Gateway, CLI turn,
agent-core Gateway, and (unless an existing URL is supplied) vLLM. It requires
a dedicated Redis endpoint and is skipped unless explicitly enabled:

    RUN_JIUWENSWARM_E2E=1 \
    JIUWENSWARM_E2E_REDIS_URL=redis://127.0.0.1:16379/0 \
    JIUWENSWARM_E2E_MODEL_PATH=/path/to/Qwen3-0.6B \
    JIUWENSWARM_REPO=/path/to/jiuwenswarm \
    pytest -s tests/system_tests/agent_evolving/agent_rl/online/test_gateway_mode_online_loop_e2e.py

Set ``JIUWENSWARM_E2E_INFERENCE_URL`` to reuse a compatible deployed vLLM
instead of launching one. The endpoint must expose token IDs and logprobs.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import IO, Any

import httpx
import pytest
import yaml

pytestmark = [
    pytest.mark.level1,
    pytest.mark.skipif(
        os.getenv("RUN_JIUWENSWARM_E2E") != "1",
        reason="set RUN_JIUWENSWARM_E2E=1 to start the real JiuwenSwarm E2E",
    ),
]

_REPO_ROOT = Path(__file__).resolve().parents[5]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _log_tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return "<log file was not created>"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


class _ProcessGroup:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._processes: list[tuple[str, subprocess.Popen[str], Path]] = []
        self._logs: list[IO[str]] = []

    def start(self, name: str, command: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.Popen[str]:
        log_path = self._log_dir / f"{name}.log"
        log_file = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._logs.append(log_file)
        self._processes.append((name, process, log_path))
        return process

    def wait_http(self, name: str, process: subprocess.Popen[str], url: str, *, timeout: float) -> None:
        self._wait(name, process, timeout=timeout, probe=lambda: httpx.get(url, timeout=1).is_success)

    def wait_tcp(self, name: str, process: subprocess.Popen[str], port: int, *, timeout: float) -> None:
        def probe() -> bool:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True

        self._wait(name, process, timeout=timeout, probe=probe)

    def _wait(
        self,
        name: str,
        process: subprocess.Popen[str],
        *,
        timeout: float,
        probe: Any,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"{name} exited with {process.returncode}\n{_log_tail(self._log_path(name))}")
            try:
                if probe():
                    return
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(0.5)
        pytest.fail(f"timed out waiting for {name}\n{_log_tail(self._log_path(name))}")

    def _log_path(self, name: str) -> Path:
        return next(path for item_name, _, path in self._processes if item_name == name)

    def close(self) -> None:
        for _, process, _ in reversed(self._processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        for _, process, _ in reversed(self._processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for log_file in self._logs:
            log_file.close()


def _require_path(env_name: str, default: Path | None = None) -> Path:
    raw = os.getenv(env_name)
    path = Path(raw).expanduser().resolve() if raw else default
    if path is None or not path.exists():
        pytest.fail(f"{env_name} must point to an existing path")
    return path


def _initialize_swarm_workspace(
    *,
    data_dir: Path,
    env: dict[str, str],
    gateway_url: str,
    model_name: str,
    user_id: str,
    collection_session_id: str,
) -> None:
    initialized = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from jiuwenswarm.common.utils import prepare_workspace; "
                "prepare_workspace(overwrite=False, preferred_language='zh')"
            ),
        ],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr

    config_path = data_dir / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model_client = config["models"]["defaults"][0]["model_client_config"]
    model_client.update(
        {
            "api_base": f"{gateway_url}/v1",
            "api_key": "EMPTY",
            "model_name": model_name,
            "client_provider": "OpenAI",
            "verify_ssl": False,
            "custom_headers": {
                "x-user-id": user_id,
                "x-session-id": collection_session_id,
            },
        }
    )
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (data_dir / "config" / ".env").write_text(
        "\n".join(
            [
                f"API_BASE={gateway_url}/v1",
                "API_KEY=EMPTY",
                f"MODEL_NAME={model_name}",
                "MODEL_PROVIDER=OpenAI",
                "BROWSER_RUNTIME_MCP_ENABLED=0",
                "LOG_LEVEL=INFO",
                "PREFERRED_LANGUAGE=zh",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _post_json(client: httpx.Client, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(url, json=payload)
    response.raise_for_status()
    return response.json()


def test_real_jiuwenswarm_reaches_gateway_collection_and_trainer_queue(tmp_path: Path) -> None:
    swarm_repo = _require_path("JIUWENSWARM_REPO", _REPO_ROOT.parent / "jiuwenswarm")
    redis_url = os.getenv("JIUWENSWARM_E2E_REDIS_URL", "").strip()
    if not redis_url:
        pytest.fail("JIUWENSWARM_E2E_REDIS_URL must point to a dedicated Redis instance")

    inference_url = os.getenv("JIUWENSWARM_E2E_INFERENCE_URL", "").strip().rstrip("/")
    model_path: Path | None = None
    if not inference_url:
        model_path = _require_path("JIUWENSWARM_E2E_MODEL_PATH")
    model_name = os.getenv("JIUWENSWARM_E2E_MODEL", model_path.name if model_path else "").strip()
    if not model_name:
        pytest.fail("JIUWENSWARM_E2E_MODEL is required when reusing an inference endpoint")

    ports = {name: _free_port() for name in ("vllm", "gateway", "agent_server", "swarm_web", "swarm_tui")}
    if not inference_url:
        inference_url = f"http://127.0.0.1:{ports['vllm']}"
    gateway_url = f"http://127.0.0.1:{ports['gateway']}"
    run_id = uuid.uuid4().hex
    collection_session_id = f"jiuwenswarm-e2e-{run_id}"
    conversation_session_id = f"jiuwenswarm-conversation-{run_id}"
    user_id = f"jiuwenswarm-user-{run_id}"
    marker = f"JIUWENSWARM_REAL_E2E_{run_id[:8]}"
    data_dir = tmp_path / "jiuwenswarm-data"
    home_dir = tmp_path / "jiuwenswarm-home"
    workspace = tmp_path / "workspace"
    log_dir = tmp_path / "logs"
    for path in (data_dir, home_dir, workspace, log_dir):
        path.mkdir(parents=True, exist_ok=True)

    pythonpath = os.pathsep.join(
        value for value in (str(swarm_repo), str(_REPO_ROOT), os.getenv("PYTHONPATH", "")) if value
    )
    base_env = dict(os.environ)
    base_env.update(
        {
            "PYTHONPATH": pythonpath,
            "JIUWENSWARM_HOME": str(home_dir),
            "JIUWENSWARM_DATA_DIR": str(data_dir),
            "AGENT_SERVER_HOST": "127.0.0.1",
            "AGENT_SERVER_PORT": str(ports["agent_server"]),
            "AGENT_SERVER_URL": f"ws://127.0.0.1:{ports['agent_server']}",
            "WEB_HOST": "127.0.0.1",
            "WEB_PORT": str(ports["swarm_web"]),
            "GATEWAY_HOST": "127.0.0.1",
            "GATEWAY_PORT": str(ports["swarm_tui"]),
            "BROWSER_RUNTIME_MCP_ENABLED": "0",
        }
    )
    _initialize_swarm_workspace(
        data_dir=data_dir,
        env=base_env,
        gateway_url=gateway_url,
        model_name=model_name,
        user_id=user_id,
        collection_session_id=collection_session_id,
    )

    processes = _ProcessGroup(log_dir)
    try:
        if model_path is not None:
            vllm_env = dict(base_env)
            vllm_env["CUDA_VISIBLE_DEVICES"] = os.getenv("JIUWENSWARM_E2E_VLLM_GPU", "0")
            vllm_command = [
                sys.executable,
                "-m",
                "vllm.entrypoints.openai.api_server",
                "--model",
                str(model_path),
                "--served-model-name",
                model_name,
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["vllm"]),
                "--trust-remote-code",
                "--enable-auto-tool-choice",
                "--tool-call-parser",
                "hermes",
                "--enable-lora",
                "--max-loras",
                "4",
                *shlex.split(
                    os.getenv(
                        "JIUWENSWARM_E2E_VLLM_ARGS",
                        "--max-model-len 40960 --gpu-memory-utilization 0.70",
                    )
                ),
            ]
            vllm = processes.start("vllm", vllm_command, env=vllm_env, cwd=_REPO_ROOT)
            processes.wait_http("vllm", vllm, f"{inference_url}/health", timeout=480)

        gateway_env = dict(base_env)
        gateway_env.update(
            {
                "GATEWAY_HOST": "127.0.0.1",
                "GATEWAY_PORT": str(ports["gateway"]),
                "INFERENCE_URL": inference_url,
                "MODEL_ID": model_name,
                "SERVED_MODEL_NAME": model_name,
                "REDIS_URL": redis_url,
                "TRAJECTORY_STORE_BACKEND": "redis",
                "ENABLE_GATEWAY_TRAJECTORY_COLLECTION": "true",
                "RECORD_DIR": str(tmp_path / "records"),
                "LORA_REPO_ROOT": str(tmp_path / "lora-repo"),
                "TOOL_PARSER_NAME": "hermes",
                "REQUEST_TIMEOUT": "600",
            }
        )
        gateway = processes.start(
            "agent-core-gateway",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["gateway"]),
            ],
            env=gateway_env,
            cwd=_REPO_ROOT,
        )
        processes.wait_http("agent-core-gateway", gateway, f"{gateway_url}/health", timeout=180)

        with httpx.Client(timeout=30) as client:
            _post_json(
                client,
                f"{gateway_url}/v1/gateway/collection/sessions",
                {
                    "session_id": collection_session_id,
                    "collection_mode": "gateway",
                    "model_id": model_name,
                    "tokenizer_revision": str(model_path or model_name),
                    "template_revision": "jiuwenswarm-real-e2e",
                    "reward_mode": "terminal_task",
                },
            )

        agent_server = processes.start(
            "jiuwenswarm-agentserver",
            [
                sys.executable,
                "-m",
                "jiuwenswarm.server.app_agentserver",
                "--port",
                str(ports["agent_server"]),
            ],
            env=base_env,
            cwd=swarm_repo,
        )
        processes.wait_tcp("jiuwenswarm-agentserver", agent_server, ports["agent_server"], timeout=300)

        swarm_gateway = processes.start(
            "jiuwenswarm-gateway",
            [
                sys.executable,
                "-m",
                "jiuwenswarm.gateway.app_gateway",
                "--agent-server-url",
                f"ws://127.0.0.1:{ports['agent_server']}",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["swarm_web"]),
            ],
            env=base_env,
            cwd=swarm_repo,
        )
        processes.wait_tcp("jiuwenswarm-gateway", swarm_gateway, ports["swarm_tui"], timeout=240)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "jiuwenswarm.cli.main",
                "chat",
                f"Do not call tools. Reply with exactly {marker} and nothing else.",
                "--mode",
                "agent",
                "--session",
                conversation_session_id,
                "--cwd",
                str(workspace),
                "--project-dir",
                str(workspace),
                "--trusted-dir",
                str(workspace),
                "--gateway-url",
                f"ws://127.0.0.1:{ports['swarm_tui']}/tui",
                "--json",
                "--timeout",
                "600",
            ],
            cwd=swarm_repo,
            env=base_env,
            text=True,
            capture_output=True,
            timeout=660,
            check=False,
        )
        assert cli.returncode == 0, f"stdout:\n{cli.stdout}\nstderr:\n{cli.stderr}"
        cli_result = json.loads(cli.stdout)
        assert cli_result["ok"] is True
        assert marker in cli_result["content"]

        with httpx.Client(timeout=30) as client:
            stats = client.get(f"{gateway_url}/v1/gateway/stats")
            stats.raise_for_status()
            collection = stats.json()["collection"]
            assert collection["successes"] >= 1
            assert collection["dropped_samples"] == 0

            finalized = client.post(f"{gateway_url}/v1/gateway/collection/sessions/{collection_session_id}/finalize")
            finalized.raise_for_status()
            reward = _post_json(
                client,
                f"{gateway_url}/v1/gateway/collection/sessions/{collection_session_id}/task-reward",
                {
                    "reward_id": f"{collection_session_id}:terminal",
                    "attempt_id": collection_session_id,
                    "task_id": "jiuwenswarm-real-process-e2e",
                    "training_key": user_id,
                    "score": 1.0,
                    "passed": True,
                    "source": "exact-match-verifier",
                    "termination_reason": "success",
                    "details": {"expected": marker},
                },
            )
            assert reward["projected_samples"] >= collection["successes"]

            trajectory_stats = client.get(
                f"{gateway_url}/v1/rl/trajectories/stats",
                params={"user_id": user_id},
            )
            trajectory_stats.raise_for_status()
            assert trajectory_stats.json()["by_status"]["pending"] >= collection["successes"]
    finally:
        processes.close()
