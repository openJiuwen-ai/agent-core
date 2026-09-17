from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any, Literal

import requests
import yaml

AgentKind = Literal["jiuwenswarm", "claude_code"]

_HMAC_KEY = b"real-training-system-test-key-00"
_POSITIVE_MARKER = "TRAINING_GOOD"
_NEGATIVE_MARKER = "TRAINING_BAD"


@dataclass(frozen=True, slots=True)
class TrainingEffect:
    training_run_status: str
    base_policy: str
    trained_policy: str
    lora_tensor_count: int
    lora_abs_max: float
    rewarded_samples: int
    unrewarded_samples: int
    preference_margin_gain: float
    post_training_task_passed: bool


@dataclass(frozen=True, slots=True)
class _AgentRun:
    output: str
    passed: bool


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tail(path: Path, lines: int = 100) -> str:
    if not path.exists():
        return "<log file was not created>"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


class _ProcessGroup:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._processes: list[tuple[str, subprocess.Popen[str], Path]] = []
        self._logs: list[IO[str]] = []

    def start(self, name: str, command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
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
        self._wait(name, process, timeout=timeout, probe=lambda: requests.get(url, timeout=1).status_code < 500)

    def wait_tcp(self, name: str, process: subprocess.Popen[str], port: int, *, timeout: float) -> None:
        def probe() -> bool:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True

        self._wait(name, process, timeout=timeout, probe=probe)

    def _wait(self, name: str, process: subprocess.Popen[str], *, timeout: float, probe: Any) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with {process.returncode}\n{_tail(self._log_path(name))}")
            try:
                if probe():
                    return
            except (OSError, requests.RequestException):
                pass
            time.sleep(0.5)
        raise TimeoutError(f"timed out waiting for {name}\n{_tail(self._log_path(name))}")

    def _log_path(self, name: str) -> Path:
        return next(path for process_name, _, path in self._processes if process_name == name)

    def close(self) -> None:
        failures: list[Exception] = []
        for _, process, _ in reversed(self._processes):
            try:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                failures.append(exc)
        deadline = time.monotonic() + 20
        for _, process, _ in reversed(self._processes):
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except Exception as exc:  # pragma: no cover - cleanup diagnostics
                    failures.append(exc)
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                failures.append(exc)
        for log_file in self._logs:
            try:
                log_file.close()
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                failures.append(exc)
        if failures:
            raise ExceptionGroup("failed to close real-training child processes", failures)


class _AgentAdapter:
    def run(self, marker: str) -> _AgentRun:
        raise NotImplementedError

    def close(self) -> None:
        return


class _SessionHeaderProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in {"connection", "content-length", "host", "transfer-encoding"}
        }
        headers["X-Agent-Session-Id"] = self.server.session_id  # type: ignore[attr-defined]
        response = requests.post(
            f"{self.server.target_url}{self.path}",  # type: ignore[attr-defined]
            headers=headers,
            data=body,
            timeout=900,
        )
        response_body = response.content
        self.send_response(response.status_code)
        for name, value in response.headers.items():
            if name.lower() not in {"connection", "content-encoding", "content-length", "transfer-encoding"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format_: str, *args: Any) -> None:
        del format_, args


class _SessionHeaderProxy:
    def __init__(self, *, target_url: str, session_id: str) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _SessionHeaderProxyHandler)
        self._server.target_url = target_url  # type: ignore[attr-defined]
        self._server.session_id = session_id  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _ClaudeCodeAdapter(_AgentAdapter):
    def __init__(self, *, gateway_url: str, model_id: str, session_id: str, workspace: Path) -> None:
        self._gateway_url = gateway_url
        self._model_id = model_id
        self._session_id = session_id
        self._workspace = workspace
        self._cli = os.getenv("CLAUDE_CODE_CLI", "claude")

    def run(self, marker: str) -> _AgentRun:
        cli_session_id = str(uuid.uuid4())
        prompt = f"Do not call tools. Reply with exactly {marker} and nothing else."
        env = dict(os.environ)
        env.update(
            {
                "ANTHROPIC_API_KEY": "online-rl-system-test",
                "ANTHROPIC_AUTH_TOKEN": "online-rl-system-test",
                "ANTHROPIC_BASE_URL": self._gateway_url,
                "ANTHROPIC_CUSTOM_HEADERS": f"X-Agent-Session-Id: {self._session_id}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                "CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL": "1",
                "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
                "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1",
                "DISABLE_BUG_COMMAND": "1",
                "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
            }
        )
        completed = subprocess.run(
            [
                self._cli,
                "-p",
                "--output-format",
                "stream-json",
                "--verbose",
                "--setting-sources=",
                "--no-session-persistence",
                "--session-id",
                cli_session_id,
                "--model",
                self._model_id,
                "--bare",
                "--permission-mode",
                "dontAsk",
                prompt,
            ],
            cwd=self._workspace,
            env=env,
            text=True,
            capture_output=True,
            timeout=float(os.getenv("ONLINE_RL_TRAINING_AGENT_TIMEOUT", "900")),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Claude Code failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
        output = self._result_text(completed.stdout)
        return _AgentRun(output=output, passed=output.strip() == marker)

    @staticmethod
    def _result_text(stdout: str) -> str:
        for line in reversed(stdout.splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                return str(event.get("result") or "")
        raise RuntimeError(f"Claude Code did not emit a result event\n{stdout[-4000:]}")


class _JiuwenSwarmAdapter(_AgentAdapter):
    def __init__(
        self,
        *,
        gateway_url: str,
        model_id: str,
        session_id: str,
        workspace: Path,
        data_dir: Path,
        processes: _ProcessGroup,
        repo_root: Path,
    ) -> None:
        self._model_id = model_id
        self._session_id = session_id
        self._workspace = workspace
        self._data_dir = data_dir
        self._repo_root = repo_root
        self._swarm_repo = (
            Path(os.getenv("JIUWENSWARM_REPO", str(repo_root.parent / "jiuwenswarm"))).expanduser().resolve()
        )
        self._swarm_sdk_root = self._prepare_locked_sdk()
        self._session_proxy = _SessionHeaderProxy(target_url=gateway_url, session_id=session_id)
        try:
            self._ports = {name: _free_port() for name in ("agent_server", "web", "tui")}
            self._env = self._build_env()
            self._initialize_workspace(self._session_proxy.url)
            agent_server = processes.start(
                "jiuwenswarm-agentserver",
                [
                    sys.executable,
                    "-m",
                    "jiuwenswarm.server.app_agentserver",
                    "--port",
                    str(self._ports["agent_server"]),
                ],
                cwd=self._swarm_repo,
                env=self._env,
            )
            processes.wait_tcp("jiuwenswarm-agentserver", agent_server, self._ports["agent_server"], timeout=300)
            swarm_gateway = processes.start(
                "jiuwenswarm-gateway",
                [
                    sys.executable,
                    "-m",
                    "jiuwenswarm.gateway.app_gateway",
                    "--agent-server-url",
                    f"ws://127.0.0.1:{self._ports['agent_server']}",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self._ports["web"]),
                ],
                cwd=self._swarm_repo,
                env=self._env,
            )
            processes.wait_tcp("jiuwenswarm-gateway", swarm_gateway, self._ports["tui"], timeout=300)
        except BaseException:
            self._session_proxy.close()
            raise

    def _build_env(self) -> dict[str, str]:
        pythonpath = os.pathsep.join(
            value
            for value in (
                str(self._swarm_repo),
                str(self._swarm_sdk_root),
                os.getenv("PYTHONPATH", ""),
            )
            if value
        )
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": pythonpath,
                "JIUWENSWARM_HOME": str(self._data_dir / "home"),
                "JIUWENSWARM_DATA_DIR": str(self._data_dir),
                "AGENT_SERVER_HOST": "127.0.0.1",
                "AGENT_SERVER_PORT": str(self._ports["agent_server"]),
                "AGENT_SERVER_URL": f"ws://127.0.0.1:{self._ports['agent_server']}",
                "WEB_HOST": "127.0.0.1",
                "WEB_PORT": str(self._ports["web"]),
                "GATEWAY_HOST": "127.0.0.1",
                "GATEWAY_PORT": str(self._ports["tui"]),
                "BROWSER_RUNTIME_MCP_ENABLED": "0",
            }
        )
        return env

    def _prepare_locked_sdk(self) -> Path:
        lock_path = self._swarm_repo / "uv.lock"
        with lock_path.open("rb") as lock_file:
            packages = tomllib.load(lock_file)["package"]
        source = next(package["source"] for package in packages if package["name"] == "openjiuwen")
        revision = str(source["git"]).rsplit("#", maxsplit=1)[-1]
        sdk_root = self._data_dir / "locked-agent-core"
        sdk_root.mkdir(parents=True)
        archive_path = sdk_root / "openjiuwen.tar"
        with archive_path.open("wb") as archive:
            archived = subprocess.run(
                ["git", "archive", revision, "openjiuwen"],
                cwd=self._repo_root,
                stdout=archive,
                stderr=subprocess.PIPE,
                check=False,
            )
        if archived.returncode != 0:
            raise RuntimeError(
                f"JiuwenSwarm locked openjiuwen revision {revision} is unavailable locally: "
                f"{archived.stderr.decode(errors='replace')}"
            )
        with tarfile.open(archive_path) as archive:
            archive.extractall(sdk_root, filter="data")
        archive_path.unlink()
        return sdk_root

    def _initialize_workspace(self, gateway_url: str) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        initialized = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from jiuwenswarm.common.utils import prepare_workspace; "
                    "prepare_workspace(overwrite=False, preferred_language='zh')"
                ),
            ],
            cwd=self._repo_root,
            env=self._env,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if initialized.returncode != 0:
            raise RuntimeError(f"JiuwenSwarm workspace initialization failed\n{initialized.stderr}")
        config_path = self._data_dir / "config" / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model_client = config["models"]["defaults"][0]["model_client_config"]
        model_client.update(
            {
                "api_base": f"{gateway_url}/v1",
                "api_key": "online-rl-system-test",
                "model_name": self._model_id,
                "client_provider": "OpenAI",
                "verify_ssl": False,
                "custom_headers": {"X-Agent-Session-Id": self._session_id},
            }
        )
        config["react"]["enable_read_image_multimodal"] = False
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (self._data_dir / "config" / ".env").write_text(
            "\n".join(
                (
                    f"API_BASE={gateway_url}/v1",
                    "API_KEY=online-rl-system-test",
                    f"MODEL_NAME={self._model_id}",
                    "MODEL_PROVIDER=OpenAI",
                    "BROWSER_RUNTIME_MCP_ENABLED=0",
                    "LOG_LEVEL=INFO",
                    "PREFERRED_LANGUAGE=zh",
                )
            )
            + "\n",
            encoding="utf-8",
        )

    def run(self, marker: str) -> _AgentRun:
        prompt = f"Do not call tools. Reply with exactly {marker} and nothing else."
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "jiuwenswarm.cli.main",
                "chat",
                prompt,
                "--mode",
                "agent",
                "--session",
                f"training-st-{uuid.uuid4()}",
                "--cwd",
                str(self._workspace),
                "--project-dir",
                str(self._workspace),
                "--trusted-dir",
                str(self._workspace),
                "--gateway-url",
                f"ws://127.0.0.1:{self._ports['tui']}/tui",
                "--json",
                "--timeout",
                os.getenv("ONLINE_RL_TRAINING_AGENT_TIMEOUT", "900"),
            ],
            cwd=self._swarm_repo,
            env=self._env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=float(os.getenv("ONLINE_RL_TRAINING_AGENT_TIMEOUT", "900")) + 60,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"JiuwenSwarm failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
        payload = self._last_json(completed.stdout)
        if payload.get("ok") is not True:
            raise RuntimeError(f"JiuwenSwarm returned a failed result: {payload}")
        output = str(payload.get("content") or "")
        return _AgentRun(output=output, passed=output.strip() == marker)

    def close(self) -> None:
        self._session_proxy.close()

    @staticmethod
    def _last_json(stdout: str) -> dict[str, Any]:
        for line in reversed(stdout.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise RuntimeError(f"JiuwenSwarm did not emit JSON\n{stdout[-4000:]}")


class RealOnlineRLTrainingSystem:
    """Own and verify one isolated real-GPU Online RL training system."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = Path(work_dir).resolve()
        self._repo_root = Path(__file__).resolve().parents[5]
        self._aigw_repo = (
            Path(
                os.getenv(
                    "AIGW_REPO",
                    str(self._repo_root.parent / "AgentBox-Platform/AgentBox-Platform/AgentInfra/Adapter"),
                )
            )
            .expanduser()
            .resolve()
        )
        self._model_path = (
            Path(os.getenv("ONLINE_RL_TRAINING_MODEL_PATH", "/data1/models/Qwen/Qwen3-4B-Instruct-2507"))
            .expanduser()
            .resolve()
        )
        self._model_id = os.getenv("ONLINE_RL_TRAINING_MODEL_ID", "qwen3-4b-online-rl-st").strip()
        self._gateway_port = _free_port()
        self._service_port = _free_port()
        self._vllm_port = _free_port()
        self._gateway_url = f"http://127.0.0.1:{self._gateway_port}"
        self._vllm_url = f"http://127.0.0.1:{self._vllm_port}"
        self._crypto_socket_path = self._work_dir / "crypto.sock"
        self._processes = _ProcessGroup(self._work_dir / "logs")
        self._redis_container: str | None = None
        self._crypto_socket: socket.socket | None = None
        self._crypto_thread: threading.Thread | None = None

    def __enter__(self) -> RealOnlineRLTrainingSystem:
        self._preflight()
        (self._work_dir / "logs").mkdir(parents=True, exist_ok=True)
        try:
            self._start_redis()
            self._start_vllm()
            self._start_crypto_socket()
            self._write_configs()
            self._start_aigw()
            self._register_vllm()
            self._start_service()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        self.close()

    def train_and_measure(self, agent_kind: AgentKind) -> TrainingEffect:
        session_id = f"{agent_kind}-training-st-{uuid.uuid4().hex}"
        workspace = self._work_dir / f"{agent_kind}-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        adapter = self._agent_adapter(agent_kind, session_id=session_id, workspace=workspace)
        try:
            for marker, reward in (
                (_POSITIVE_MARKER, 1.0),
                (_POSITIVE_MARKER, 1.0),
                (_NEGATIVE_MARKER, 0.0),
                (_NEGATIVE_MARKER, 0.0),
            ):
                self._collect_attempt(adapter, session_id=session_id, marker=marker, reward=reward)

            samples = self._trajectory_details()
            rewarded = [sample for sample in samples if float(sample["judge"]["score"]) == 1.0]
            unrewarded = [sample for sample in samples if float(sample["judge"]["score"]) == 0.0]
            if not rewarded or not unrewarded:
                raise AssertionError("training ST requires both rewarded and unrewarded samples")

            started = self._signed_request("POST", "/v1/rl/training/runs", json={})
            self._require_status(started, {200, 201}, "start Training Run")
            run = self._wait_training(str(started.json()["training_run_id"]))
            if run["status"] != "succeeded":
                raise AssertionError(f"Training Run failed: {run}\n{_tail(self._work_dir / 'logs' / 'aigw.log')}")

            trained_policy = str(run["lora_name"])
            lora_path = Path(str(run["lora_path"]))
            lora_tensor_count, lora_abs_max = self._lora_evidence(lora_path)
            preference_margin_gain = self._preference_margin_gain(
                rewarded=rewarded,
                unrewarded=unrewarded,
                trained_policy=trained_policy,
            )

            post_task = self._start_task(session_id)
            post_run = adapter.run(_POSITIVE_MARKER)
            self._stop_task(str(post_task["rl_task_id"]))
            post_passed = post_task["policy_lora_name"] == trained_policy and post_run.passed
            return TrainingEffect(
                training_run_status=str(run["status"]),
                base_policy="base",
                trained_policy=trained_policy,
                lora_tensor_count=lora_tensor_count,
                lora_abs_max=lora_abs_max,
                rewarded_samples=len(rewarded),
                unrewarded_samples=len(unrewarded),
                preference_margin_gain=preference_margin_gain,
                post_training_task_passed=post_passed,
            )
        finally:
            adapter.close()

    def _preflight(self) -> None:
        binary = Path(os.getenv("AIGW_BIN", str(self._aigw_repo / "output" / "aigw" / "aigw")))
        required = {
            "AIGW binary": binary,
            "model config": self._model_path / "config.json",
            "model tokenizer": self._model_path / "tokenizer.json",
        }
        missing = [f"{name}: {path}" for name, path in required.items() if not path.is_file()]
        if missing:
            raise RuntimeError("missing real training ST prerequisite(s): " + ", ".join(missing))
        if len(_HMAC_KEY) != 32:
            raise AssertionError("training ST HMAC key must be 32 bytes")

    def _start_redis(self) -> None:
        name = f"online-rl-training-st-{uuid.uuid4().hex[:12]}"
        created = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "-p",
                "127.0.0.1::6379",
                "redis:7-alpine",
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._redis_container = created.stdout.strip()
        port = (
            subprocess.run(
                ["docker", "port", self._redis_container, "6379/tcp"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.rsplit(":", 1)[1]
            .strip()
        )
        self._redis_url = f"redis://127.0.0.1:{port}/0"

    def _start_vllm(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "CUDA_VISIBLE_DEVICES": os.getenv("ONLINE_RL_TRAINING_VLLM_GPU", "0"),
                "VLLM_ALLOW_RUNTIME_LORA_UPDATING": "true",
            }
        )
        command = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            str(self._model_path),
            "--served-model-name",
            self._model_id,
            "--host",
            "127.0.0.1",
            "--port",
            str(self._vllm_port),
            "--trust-remote-code",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            "hermes",
            "--enable-lora",
            "--max-loras",
            "4",
            "--max-lora-rank",
            "16",
            "--max-model-len",
            os.getenv("ONLINE_RL_TRAINING_MODEL_LEN", "33792"),
            "--max-num-seqs",
            "1",
            "--gpu-memory-utilization",
            os.getenv("ONLINE_RL_TRAINING_VLLM_MEMORY", "0.85"),
            "--enforce-eager",
            "--generation-config",
            "vllm",
            *shlex.split(os.getenv("ONLINE_RL_TRAINING_VLLM_ARGS", "")),
        ]
        process = self._processes.start("vllm", command, cwd=self._repo_root, env=env)
        self._processes.wait_http("vllm", process, f"{self._vllm_url}/health", timeout=600)

    def _start_crypto_socket(self) -> None:
        self._crypto_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._crypto_socket.bind(str(self._crypto_socket_path))
        self._crypto_socket.listen(1)

        def serve_key() -> None:
            assert self._crypto_socket is not None
            connection, _ = self._crypto_socket.accept()
            with connection:
                connection.sendall(
                    json.dumps({"apiHmacKey": _HMAC_KEY.decode(), "insHmacKey": _HMAC_KEY.decode()}).encode()
                )

        self._crypto_thread = threading.Thread(target=serve_key, daemon=True)
        self._crypto_thread.start()

    def _write_configs(self) -> None:
        lora_root = self._work_dir / "loras"
        lora_root.mkdir()
        service_config = {
            "listen_host": "127.0.0.1",
            "listen_port": self._service_port,
            "redis_url": self._redis_url,
            "model_id": self._model_id,
            "base_model_path": str(self._model_path),
            "aigw_endpoint": self._gateway_url,
            "min_samples_for_training": 4,
            "max_samples_per_run": 32,
            "nproc_per_node": len(self._training_gpu_ids()),
            "training_gpu_ids": ",".join(self._training_gpu_ids()),
            "lora_repository_path": str(lora_root),
            "record_dir": str(self._work_dir / "records"),
            "log_path": str(self._work_dir / "logs" / "rl-service.log"),
            "log_level": "INFO",
        }
        self._service_config = self._work_dir / "rl-service.yaml"
        self._service_config.write_text(yaml.safe_dump(service_config, sort_keys=False), encoding="utf-8")
        gateway_config = {
            "global": {
                "host": "127.0.0.1",
                "port": str(self._gateway_port),
                "logPath": str(self._work_dir / "logs"),
                "logLevel": "info",
                "securitySchema": "default",
                "cryptoSock": str(self._crypto_socket_path),
                "reqTimeout": 900,
                "snapshotUpdateInterval": 3,
            },
            "proxy": {"enable": True, "timeout": 900, "maxRetry": 0},
            "tokenizers": [
                {
                    "tokenizeModelName": "training-st-tokenizer",
                    "configPath": str(self._model_path / "tokenizer.json"),
                    "tokenizerType": "huggingfaceTokenizers",
                }
            ],
            "globalSchedulers": [
                {
                    "model": self._model_id,
                    "blockSize": 64,
                    "deployPolicy": "mixed",
                    "maxTimeToFirstToken": 100,
                    "maxTimeBetweenTokens": 100,
                    "tokenizeModelName": "training-st-tokenizer",
                    "skipInstanceConnection": True,
                    "loadBalancer": {
                        "mixed": "roundRobin",
                        "batchSize": 8,
                        "reservedBlockNumber": 8,
                        "minMatchedLength": 1,
                    },
                }
            ],
            "limits": {
                "totalInsNum": 2,
                "insNumPerModel": 2,
                "modelNum": 1,
                "concurrency": 4,
                "maxPromptRunes": 131072,
            },
            "predictor": {"predictType": "none"},
            "lora": {
                "root": str(lora_root),
                "statePath": str(self._work_dir / "lora-state.json"),
                "operationTimeout": "120s",
            },
            "onlineRL": {
                "command": [
                    sys.executable,
                    "-m",
                    "openjiuwen.agent_evolving.agent_rl.online.service",
                ],
                "configPath": str(self._service_config),
                "endpoint": f"http://127.0.0.1:{self._service_port}",
                "hookTimeout": "30s",
                "hookRetries": 0,
                "drainTimeout": "120s",
                "controlTimeout": "300s",
            },
        }
        self._gateway_config = self._work_dir / "aigw.json"
        self._gateway_config.write_text(json.dumps(gateway_config), encoding="utf-8")

    def _training_gpu_ids(self) -> list[str]:
        return [value.strip() for value in os.getenv("ONLINE_RL_TRAINING_GPU_IDS", "1,2").split(",") if value.strip()]

    def _start_aigw(self) -> None:
        binary = Path(os.getenv("AIGW_BIN", str(self._aigw_repo / "output" / "aigw" / "aigw")))
        env = dict(os.environ)
        env.update(
            {
                "CUDA_VISIBLE_DEVICES": ",".join(self._training_gpu_ids()),
                "ONLINE_RL_DETERMINISTIC_SEED": "7",
                "PYTHONHASHSEED": "7",
                "ONLINE_RL_MAX_PROMPT_LENGTH": "2048",
                "ONLINE_RL_MAX_RESPONSE_LENGTH": "512",
                "ONLINE_RL_TRAIN_BATCH_SIZE": "4",
                "ONLINE_RL_PPO_MINI_BATCH_SIZE": "4",
                "ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU": "1",
                "ONLINE_RL_ACTOR_LEARNING_RATE": "0.0001",
                "ONLINE_RL_ACTOR_PPO_MAX_TOKEN_LEN_PER_GPU": "4096",
                "ONLINE_RL_LOG_PROB_MICRO_BATCH_SIZE_PER_GPU": "1",
                "ONLINE_RL_REF_LOG_PROB_MAX_TOKEN_LEN_PER_GPU": "4096",
                "ONLINE_RL_FSDP_MODEL_DTYPE": "bfloat16",
            }
        )
        process = self._processes.start(
            "aigw",
            [str(binary), f"--config={self._gateway_config}"],
            cwd=self._aigw_repo,
            env=env,
        )
        self._processes.wait_http("aigw", process, f"{self._gateway_url}/aigw/v1/health", timeout=180)

    def _register_vllm(self) -> None:
        payload = {
            "name": "real-training-vllm",
            "model": self._model_id,
            "instanceIp": "127.0.0.1",
            "port": str(self._vllm_port),
            "role": "mixed",
            "groupID": "",
            "dpRank": -1,
        }
        deadline = time.monotonic() + 30
        while True:
            response = self._signed_request("POST", "/aigw/v1/register-instance", json=payload)
            if response.status_code != 503 or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        self._require_status(response, {200}, "register vLLM")

    def _start_service(self) -> None:
        response = self._signed_request("POST", "/v1/rl/service/start", timeout=330)
        self._require_status(response, {200, 201}, "start RL Service")
        if response.json().get("status") != "running":
            raise AssertionError(f"RL Service did not enter running state: {response.text}")

    def _agent_adapter(self, agent_kind: AgentKind, *, session_id: str, workspace: Path) -> _AgentAdapter:
        if agent_kind == "claude_code":
            return _ClaudeCodeAdapter(
                gateway_url=self._gateway_url,
                model_id=self._model_id,
                session_id=session_id,
                workspace=workspace,
            )
        if agent_kind == "jiuwenswarm":
            return _JiuwenSwarmAdapter(
                gateway_url=self._gateway_url,
                model_id=self._model_id,
                session_id=session_id,
                workspace=workspace,
                data_dir=self._work_dir / "jiuwenswarm-data",
                processes=self._processes,
                repo_root=self._repo_root,
            )
        raise ValueError(f"unsupported training ST agent: {agent_kind}")

    def _collect_attempt(self, adapter: _AgentAdapter, *, session_id: str, marker: str, reward: float) -> None:
        task = self._start_task(session_id)
        run = adapter.run(marker)
        if not run.passed:
            raise AssertionError(f"Agent did not produce the controlled marker {marker!r}: {run.output!r}")
        task_id = str(task["rl_task_id"])
        self._stop_task(task_id)
        rewarded = self._signed_request("POST", f"/v1/rl/tasks/{task_id}/reward", json={"reward": reward})
        self._require_status(rewarded, {200}, "submit terminal reward")
        if int(rewarded.json()["sample_count"]) < 1:
            raise AssertionError(f"Agent attempt produced no trainable samples: {rewarded.text}")

    def _start_task(self, session_id: str) -> dict[str, Any]:
        response = self._signed_request(
            "POST",
            "/v1/rl/tasks/start",
            headers={"X-Agent-Session-Id": session_id},
            json={"reward_mode": "terminal"},
        )
        self._require_status(response, {200, 201}, "start RL Task")
        return response.json()

    def _stop_task(self, task_id: str) -> None:
        response = self._signed_request("POST", f"/v1/rl/tasks/{task_id}/stop", timeout=180)
        self._require_status(response, {200}, "stop RL Task")

    def _trajectory_details(self) -> list[dict[str, Any]]:
        response = self._signed_request("GET", "/v1/rl/trajectories?limit=100")
        self._require_status(response, {200}, "list trajectories")
        details: list[dict[str, Any]] = []
        for item in response.json()["items"]:
            detail = self._signed_request("GET", f"/v1/rl/trajectories/{item['trajectory_id']}")
            self._require_status(detail, {200}, "get trajectory")
            details.append(detail.json())
        return details

    def _wait_training(self, training_run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + float(os.getenv("ONLINE_RL_TRAINING_TIMEOUT", "2400"))
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = self._signed_request("GET", f"/v1/rl/training/runs/{training_run_id}")
            self._require_status(response, {200}, "get Training Run")
            last = response.json()
            if last["status"] in {"succeeded", "failed", "canceled"}:
                return last
            time.sleep(2)
        raise TimeoutError(f"Training Run timed out: {last}")

    def _preference_margin_gain(
        self,
        *,
        rewarded: list[dict[str, Any]],
        unrewarded: list[dict[str, Any]],
        trained_policy: str,
    ) -> float:
        rewarded_delta = self._mean([self._sample_logprob_delta(sample, trained_policy) for sample in rewarded])
        unrewarded_delta = self._mean([self._sample_logprob_delta(sample, trained_policy) for sample in unrewarded])
        return rewarded_delta - unrewarded_delta

    def _sample_logprob_delta(self, sample: dict[str, Any], trained_policy: str) -> float:
        trajectory = sample["trajectory"]
        prompt_ids = list(trajectory["prompt_ids"])
        response_ids = list(trajectory["response_ids"])
        base_logprobs = self._completion_logprobs(self._model_id, prompt_ids, response_ids)
        trained_logprobs = self._completion_logprobs(trained_policy, prompt_ids, response_ids)
        return self._mean(trained_logprobs) - self._mean(base_logprobs)

    def _completion_logprobs(self, model: str, prompt_ids: list[int], response_ids: list[int]) -> list[float]:
        payload = {
            "model": model,
            "prompt": prompt_ids + response_ids,
            "max_tokens": 1,
            "temperature": 0,
            "echo": True,
            "logprobs": 1,
            "return_token_ids": True,
        }
        response = requests.post(f"{self._vllm_url}/v1/completions", json=payload, timeout=180)
        self._require_status(response, {200}, "measure trained policy logprobs")
        token_logprobs = response.json()["choices"][0]["logprobs"]["token_logprobs"]
        measured = token_logprobs[len(prompt_ids) : len(prompt_ids) + len(response_ids)]
        logprobs = [float(value) for value in measured if value is not None]
        if len(logprobs) != len(response_ids):
            raise AssertionError(
                f"completion logprob alignment mismatch: expected={len(response_ids)} actual={len(logprobs)}"
            )
        return logprobs

    @staticmethod
    def _mean(values: list[float]) -> float:
        if not values:
            raise AssertionError("cannot calculate an empty mean")
        return sum(values) / len(values)

    @staticmethod
    def _lora_evidence(lora_path: Path) -> tuple[int, float]:
        from safetensors.torch import load_file

        state = load_file(str(lora_path / "adapter_model.safetensors"), device="cpu")
        if not state:
            return 0, 0.0
        return len(state), max(float(tensor.abs().max().item()) for tensor in state.values())

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> requests.Response:
        request_headers = dict(headers or {})
        body = b"" if json is None else __import__("json").dumps(json, separators=(",", ":")).encode()
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        timestamp = str(int(time.time() * 1000))
        request_headers["X-Timestamp"] = timestamp
        request_headers["X-Signature"] = hmac.new(_HMAC_KEY, timestamp.encode() + body, hashlib.sha256).hexdigest()
        return requests.request(
            method,
            f"{self._gateway_url}{path}",
            headers=request_headers,
            data=body,
            timeout=timeout,
        )

    @staticmethod
    def _require_status(response: requests.Response, statuses: set[int], operation: str) -> None:
        if response.status_code not in statuses:
            raise AssertionError(f"{operation} failed: status={response.status_code} body={response.text[:4000]}")

    def close(self) -> None:
        failures: list[Exception] = []
        try:
            if hasattr(self, "_gateway_config"):
                self._signed_request("POST", "/v1/rl/service/stop", timeout=180)
        except requests.RequestException:
            pass
        try:
            self._processes.close()
        except Exception as exc:  # pragma: no cover - cleanup diagnostics
            failures.append(exc)
        if self._crypto_socket is not None:
            try:
                self._crypto_socket.close()
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                failures.append(exc)
            else:
                self._crypto_socket = None
        if self._crypto_thread is not None:
            try:
                self._crypto_thread.join(timeout=5)
                if self._crypto_thread.is_alive():
                    raise RuntimeError("crypto key server thread did not stop")
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                failures.append(exc)
            else:
                self._crypto_thread = None
        try:
            self._crypto_socket_path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - cleanup diagnostics
            failures.append(exc)
        if self._redis_container is not None:
            removed = subprocess.run(
                ["docker", "rm", "-f", self._redis_container],
                check=False,
                capture_output=True,
                text=True,
            )
            if removed.returncode == 0:
                self._redis_container = None
            else:  # pragma: no cover - cleanup diagnostics
                failures.append(RuntimeError(f"failed to remove Redis container: {removed.stderr.strip()}"))
        if failures:
            raise ExceptionGroup("failed to close real online-RL training system", failures)


__all__ = ["RealOnlineRLTrainingSystem", "TrainingEffect"]
