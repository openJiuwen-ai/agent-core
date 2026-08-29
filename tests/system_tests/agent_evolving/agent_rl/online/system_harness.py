from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

import requests

_MODEL_ID = "model-1"
_HMAC_KEY = b"system-test-hmac-key-00000000000"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _VLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path in {"/v1/load_lora_adapter", "/v1/unload_lora_adapter"}:
            self.server.control_calls.append((self.path, payload))  # type: ignore[attr-defined]
            status = self.server.lora_status  # type: ignore[attr-defined]
            self._write_json(status, {"ok": status == 200})
            return
        if self.path != "/v1/chat/completions":
            self._write_json(404, {"error": {"message": "not found"}})
            return
        self.server.request_started.set()  # type: ignore[attr-defined]
        time.sleep(self.server.completion_delay)  # type: ignore[attr-defined]
        upstream_status = self.server.upstream_status  # type: ignore[attr-defined]
        if upstream_status != 200:
            self._write_json(upstream_status, {"error": {"message": f"fake upstream {upstream_status}"}})
            return
        self.server.completion_models.append(payload.get("model"))  # type: ignore[attr-defined]
        self.server.local_completion_models.append(payload.get("model"))  # type: ignore[attr-defined]
        tool_call = {
            "id": "toolu_system",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"query":"docs"}'},
        }
        message: dict[str, Any] = {"role": "assistant", "content": "fake completion"}
        finish_reason = "stop"
        if payload.get("tools"):
            message = {"role": "assistant", "content": "", "tool_calls": [tool_call]}
            finish_reason = "tool_calls"
        response = {
            "id": "chatcmpl-system",
            "object": "chat.completion",
            "created": 1,
            "model": payload.get("model", _MODEL_ID),
            "prompt_token_ids": [101, 102],
            "prompt_logprobs": [{"101": -0.1}, {"102": -0.1}],
            "rl_lora": {"name": payload.get("model", _MODEL_ID)},
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                    "token_ids": [201],
                    "logprobs": {"content": [{"token": "fake completion", "logprob": -0.2}]},
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        if payload.get("stream") is True:
            self._write_stream(response)
            return
        self._write_json(200, response)

    def log_message(self, format_: str, *args: Any) -> None:
        del format_, args

    def _write_json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_stream(self, response: dict[str, Any]) -> None:
        choice = response["choices"][0]
        chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": response["model"],
            "prompt_token_ids": response["prompt_token_ids"],
            "choices": [
                {
                    "index": 0,
                    "delta": choice["message"],
                    "finish_reason": choice["finish_reason"],
                    "token_ids": choice["token_ids"],
                    "logprobs": choice["logprobs"],
                }
            ],
        }
        usage = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": response["model"],
            "choices": [],
            "usage": response["usage"],
        }
        encoded = (
            "".join(f"data: {json.dumps(event, separators=(',', ':'))}\n\n" for event in (chunk, usage))
            + "data: [DONE]\n\n"
        )
        body = encoded.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OnlineRLSystem:
    """Own one isolated AIGW/RL Service system and its fake external seams."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = Path(work_dir)
        self._repo = Path(__file__).resolve().parents[5]
        default_aigw_repo = self._repo.parent / "AgentBox-Platform/AgentBox-Platform/AgentInfra/Adapter"
        self._aigw_repo = Path(os.environ.get("AIGW_REPO", str(default_aigw_repo))).expanduser().resolve()
        self._gateway_port = _free_port()
        self._service_port = _free_port()
        self._vllm_ports = [_free_port(), _free_port()]
        self._gateway_url = f"http://127.0.0.1:{self._gateway_port}"
        self._crypto_socket_path = self._work_dir / "crypto.sock"
        self._redis_container: str | None = None
        self._gateway: subprocess.Popen[bytes] | None = None
        self._gateway_log: BinaryIO | None = None
        self._vllms: list[ThreadingHTTPServer] = []
        self._vllm_threads: list[threading.Thread] = []
        self._completion_models: list[str] = []
        self._crypto_socket: socket.socket | None = None
        self._crypto_thread: threading.Thread | None = None

    def __enter__(self) -> OnlineRLSystem:
        try:
            self._start_redis()
            self._start_vllm()
            self._start_crypto_socket()
            self._write_configs()
            self._start_gateway()
            self._register_vllm()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def start_service(self) -> requests.Response:
        return self._request("POST", "/v1/rl/service/start")

    def stop_service(self) -> requests.Response:
        return self._request("POST", "/v1/rl/service/stop")

    def start_task(self, *, session_id: str, reward_mode: str) -> requests.Response:
        return self._request(
            "POST",
            "/v1/rl/tasks/start",
            headers={"X-Agent-Session-Id": session_id},
            json={"reward_mode": reward_mode},
        )

    def stop_task(self, task_id: str) -> requests.Response:
        return self._request("POST", f"/v1/rl/tasks/{task_id}/stop")

    def reward(self, task_id: str, reward: float) -> requests.Response:
        return self._request("POST", f"/v1/rl/tasks/{task_id}/reward", json={"reward": reward})

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        turn_id: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> requests.Response:
        headers = {}
        if session_id:
            headers["X-Agent-Session-Id"] = session_id
        if turn_id:
            headers["X-Agent-Turn-Id"] = turn_id
        payload: dict[str, Any] = {"model": _MODEL_ID, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
        return self._request(
            "POST",
            "/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    def anthropic(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        turn_id: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> requests.Response:
        headers = {}
        if session_id:
            headers["X-Agent-Session-Id"] = session_id
        if turn_id:
            headers["X-Agent-Turn-Id"] = turn_id
        payload: dict[str, Any] = {
            "model": _MODEL_ID,
            "max_tokens": 32,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        return self._request("POST", "/v1/messages", headers=headers, json=payload)

    def disconnect_completion(self, *, session_id: str) -> None:
        body = json.dumps(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "disconnect"}],
                "stream": False,
            },
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(time.time() * 1000))
        signature = hmac.new(_HMAC_KEY, timestamp.encode() + body, hashlib.sha256).hexdigest()
        request = (
            "POST /v1/chat/completions HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self._gateway_port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"X-Agent-Session-Id: {session_id}\r\n"
            f"X-Timestamp: {timestamp}\r\n"
            f"X-Signature: {signature}\r\n\r\n"
        ).encode() + body
        connection = socket.create_connection(("127.0.0.1", self._gateway_port), timeout=5)
        connection.sendall(request)
        self.wait_for_upstream_request()
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        connection.close()

    def trajectories(self) -> requests.Response:
        return self._request("GET", "/v1/rl/trajectories")

    def trajectory_stats(self) -> requests.Response:
        return self._request("GET", "/v1/rl/trajectories/stats")

    def trajectory(self, trajectory_id: str) -> requests.Response:
        return self._request("GET", f"/v1/rl/trajectories/{trajectory_id}")

    def task(self, task_id: str) -> requests.Response:
        return self._request("GET", f"/v1/rl/tasks/{task_id}")

    def start_training(self) -> requests.Response:
        return self._request("POST", "/v1/rl/training/runs", json={})

    def training(self, training_run_id: str) -> requests.Response:
        return self._request("GET", f"/v1/rl/training/runs/{training_run_id}")

    def stop_training(self, training_run_id: str) -> requests.Response:
        return self._request("POST", f"/v1/rl/training/runs/{training_run_id}/stop")

    def wait_training(self, training_run_id: str, *, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.training(training_run_id).json()
            if run["status"] not in {"pending", "running"}:
                return run
            time.sleep(0.05)
        raise AssertionError(f"Training Run did not finish: {training_run_id}")

    def lora(self) -> requests.Response:
        return self._request("GET", f"/v1/loras/{_MODEL_ID}")

    def delete_lora(self) -> requests.Response:
        return self._request("DELETE", f"/v1/loras/{_MODEL_ID}")

    def unsigned_request(self, method: str, path: str) -> requests.Response:
        return requests.request(method, self._gateway_url + path, timeout=5)

    def service(self) -> requests.Response:
        return self._request("GET", "/v1/rl/service")

    def wait_service(self, status: str, *, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.service().json()
            if record["status"] == status:
                return record
            time.sleep(0.05)
        raise AssertionError(f"RL Service did not reach {status}")

    def set_ppo_mode(self, mode: str) -> None:
        if mode not in {"success", "fail", "block"}:
            raise ValueError(f"unsupported fake PPO mode: {mode}")
        self._ppo_control_path.write_text(mode, encoding="utf-8")

    def set_fault(self, fault: str, mode: str) -> None:
        controls = {
            "before": self._before_control_path,
            "after": self._after_control_path,
            "judge": self._judge_control_path,
            "activation": self._activation_control_path,
        }
        if fault not in controls or mode not in {"success", "fail", "delay", "block"}:
            raise ValueError(f"unsupported fault control: {fault}={mode}")
        controls[fault].write_text(mode, encoding="utf-8")

    def set_upstream_status(self, status: int) -> None:
        for server in self._vllms:
            server.upstream_status = status  # type: ignore[attr-defined]

    def set_lora_status(self, status: int) -> None:
        for server in self._vllms:
            server.lora_status = status  # type: ignore[attr-defined]

    def set_lora_instance_status(self, index: int, status: int) -> None:
        self._vllms[index].lora_status = status  # type: ignore[attr-defined]

    def set_completion_delay(self, delay: float) -> None:
        for server in self._vllms:
            server.completion_delay = delay  # type: ignore[attr-defined]
            server.request_started.clear()  # type: ignore[attr-defined]

    def wait_for_upstream_request(self, *, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(server.request_started.is_set() for server in self._vllms):  # type: ignore[attr-defined]
                return
            time.sleep(0.01)
        raise AssertionError("fake vLLM did not receive a completion request")

    def add_vllm(self, *, lora_status: int = 200) -> int:
        index = self._start_vllm_instance(_free_port(), lora_status=lora_status)
        self._register_vllm([index])
        return index

    def crash_service(self) -> None:
        pid = int(self._service_pid_path.read_text(encoding="utf-8"))
        os.kill(pid, 9)

    def crash_gateway(self) -> None:
        if self._gateway is None or self._gateway.poll() is not None:
            raise RuntimeError("AIGW is not running")
        self._gateway.kill()
        self._gateway.wait(timeout=5)
        self._gateway = None
        if self._gateway_log is not None:
            self._gateway_log.close()
            self._gateway_log = None

    def restart_gateway(self) -> None:
        if self._gateway is not None:
            self.crash_gateway()
        if self._crypto_socket is not None:
            self._crypto_socket.close()
            self._crypto_socket = None
        if self._crypto_thread is not None:
            self._crypto_thread.join(timeout=5)
            self._crypto_thread = None
        self._crypto_socket_path.unlink(missing_ok=True)
        self._start_crypto_socket()
        self._start_gateway()
        self._register_vllm()

    def wait_service_process_exit(self, *, timeout: float = 5) -> None:
        pid = int(self._service_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not Path(f"/proc/{pid}").exists():
                return
            time.sleep(0.05)
        raise AssertionError(f"RL Service process {pid} survived AIGW exit")

    def pause_redis(self) -> None:
        if self._redis_container is None:
            raise RuntimeError("Redis fault injection requires the harness-owned container")
        subprocess.run(["docker", "pause", self._redis_container], check=True, stdout=subprocess.DEVNULL)

    def resume_redis(self) -> None:
        if self._redis_container is None:
            raise RuntimeError("Redis fault injection requires the harness-owned container")
        subprocess.run(["docker", "unpause", self._redis_container], check=True, stdout=subprocess.DEVNULL)

    @property
    def owns_redis(self) -> bool:
        return self._redis_container is not None

    @property
    def completion_models(self) -> list[str]:
        return list(self._completion_models)

    @property
    def lora_control_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for server in self._vllms for call in server.control_calls]  # type: ignore[attr-defined]

    def lora_calls(self, index: int) -> list[tuple[str, dict[str, Any]]]:
        return list(self._vllms[index].control_calls)  # type: ignore[attr-defined]

    def completion_count(self, index: int) -> int:
        return len(self._vllms[index].local_completion_models)  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._gateway is not None:
            if self._gateway.poll() is None:
                self._gateway.terminate()
                try:
                    self._gateway.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._gateway.kill()
                    self._gateway.wait(timeout=5)
            self._gateway = None
        if self._gateway_log is not None:
            self._gateway_log.close()
            self._gateway_log = None
        for server in self._vllms:
            server.shutdown()
            server.server_close()
        for thread in self._vllm_threads:
            thread.join(timeout=5)
        self._vllms.clear()
        self._vllm_threads.clear()
        if self._crypto_socket is not None:
            self._crypto_socket.close()
            self._crypto_socket = None
        if self._crypto_thread is not None:
            self._crypto_thread.join(timeout=5)
            self._crypto_thread = None
        self._crypto_socket_path.unlink(missing_ok=True)
        if self._redis_container is not None:
            subprocess.run(
                ["docker", "rm", "-f", self._redis_container],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self._redis_container = None

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        body = b""
        if "json" in kwargs:
            body = json.dumps(kwargs.pop("json"), separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        elif "data" in kwargs:
            raw_body = kwargs.pop("data")
            body = raw_body.encode() if isinstance(raw_body, str) else raw_body
        timestamp = str(int(time.time() * 1000))
        signature = hmac.new(_HMAC_KEY, timestamp.encode() + body, hashlib.sha256).hexdigest()
        headers.update({"X-Timestamp": timestamp, "X-Signature": signature})
        response = requests.request(
            method,
            self._gateway_url + path,
            data=body,
            headers=headers,
            timeout=15,
            **kwargs,
        )
        return response

    def _start_redis(self) -> None:
        configured = os.environ.get("ONLINE_RL_REDIS_URL", "").strip()
        if configured:
            self._redis_url = configured
            return
        name = f"online-rl-system-{uuid.uuid4().hex[:12]}"
        result = subprocess.run(
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
        self._redis_container = result.stdout.strip()
        port_result = subprocess.run(
            ["docker", "port", self._redis_container, "6379/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        self._redis_url = f"redis://127.0.0.1:{port_result.stdout.rsplit(':', 1)[1].strip()}/0"

    def _start_vllm(self) -> None:
        for port in self._vllm_ports:
            self._start_vllm_instance(port)

    def _start_vllm_instance(self, port: int, *, lora_status: int = 200) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", port), _VLLMHandler)
        server.completion_models = self._completion_models  # type: ignore[attr-defined]
        server.local_completion_models = []  # type: ignore[attr-defined]
        server.control_calls = []  # type: ignore[attr-defined]
        server.upstream_status = 200  # type: ignore[attr-defined]
        server.lora_status = lora_status  # type: ignore[attr-defined]
        server.completion_delay = 0.0  # type: ignore[attr-defined]
        server.request_started = threading.Event()  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._vllms.append(server)
        self._vllm_threads.append(thread)
        if port not in self._vllm_ports:
            self._vllm_ports.append(port)
        return len(self._vllms) - 1

    def _start_crypto_socket(self) -> None:
        self._crypto_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._crypto_socket.bind(str(self._crypto_socket_path))
        self._crypto_socket.listen(1)

        def serve_key() -> None:
            assert self._crypto_socket is not None
            connection, _ = self._crypto_socket.accept()
            with connection:
                connection.sendall(
                    json.dumps(
                        {
                            "apiHmacKey": _HMAC_KEY.decode(),
                            "insHmacKey": _HMAC_KEY.decode(),
                        }
                    ).encode()
                )

        self._crypto_thread = threading.Thread(target=serve_key, daemon=True)
        self._crypto_thread.start()

    def _write_configs(self) -> None:
        lora_root = self._work_dir / "loras"
        log_dir = self._work_dir / "aigw-logs"
        record_dir = self._work_dir / "records"
        lora_root.mkdir()
        log_dir.mkdir()
        record_dir.mkdir()
        service_config = {
            "listen_port": self._service_port,
            "redis_url": self._redis_url,
            "model_id": _MODEL_ID,
            "base_model_path": _MODEL_ID,
            "aigw_endpoint": self._gateway_url,
            "lora_activation_timeout": 5.0,
            "lora_repository_path": str(lora_root),
            "record_dir": str(record_dir),
        }
        self._ppo_control_path = self._work_dir / "ppo-control"
        self._ppo_control_path.write_text("success", encoding="utf-8")
        self._before_control_path = self._work_dir / "before-control"
        self._after_control_path = self._work_dir / "after-control"
        self._judge_control_path = self._work_dir / "judge-control"
        self._activation_control_path = self._work_dir / "activation-control"
        for control_path in (
            self._before_control_path,
            self._after_control_path,
            self._judge_control_path,
            self._activation_control_path,
        ):
            control_path.write_text("success", encoding="utf-8")
        self._service_pid_path = self._work_dir / "rl-service.pid"
        service_config["ppo_control_path"] = str(self._ppo_control_path)
        service_config["before_control_path"] = str(self._before_control_path)
        service_config["after_control_path"] = str(self._after_control_path)
        service_config["judge_control_path"] = str(self._judge_control_path)
        service_config["activation_control_path"] = str(self._activation_control_path)
        service_config["process_pid_path"] = str(self._service_pid_path)
        self._service_config = self._work_dir / "rl-service-system.json"
        self._service_config.write_text(json.dumps(service_config), encoding="utf-8")
        service_process = Path(__file__).with_name("rl_service_process.py")
        tokenizer = self._aigw_repo / "test/tokenizer/DeepSeek-R1-Distill-Qwen-7B/tokenizer.json"
        gateway_config = {
            "global": {
                "host": "127.0.0.1",
                "port": str(self._gateway_port),
                "logPath": str(log_dir),
                "logLevel": "warn",
                "securitySchema": "default",
                "cryptoSock": str(self._crypto_socket_path),
                "reqTimeout": 30,
                "snapshotUpdateInterval": 3,
            },
            "proxy": {"enable": True, "timeout": 10, "maxRetry": 0},
            "tokenizers": [
                {
                    "tokenizeModelName": "system-tokenizer",
                    "configPath": str(tokenizer),
                    "tokenizerType": "huggingfaceTokenizers",
                }
            ],
            "globalSchedulers": [
                {
                    "model": _MODEL_ID,
                    "blockSize": 64,
                    "deployPolicy": "mixed",
                    "maxTimeToFirstToken": 100,
                    "maxTimeBetweenTokens": 100,
                    "tokenizeModelName": "system-tokenizer",
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
                "totalInsNum": 8,
                "insNumPerModel": 8,
                "modelNum": 4,
                "concurrency": 16,
                "maxPromptRunes": 4096,
            },
            "predictor": {"predictType": "none"},
            "lora": {
                "root": str(lora_root),
                "statePath": str(self._work_dir / "lora-state.json"),
                "operationTimeout": "5s",
            },
            "onlineRL": {
                "command": [sys.executable, str(service_process)],
                "configPath": str(self._service_config),
                "endpoint": f"http://127.0.0.1:{self._service_port}",
                "hookTimeout": "300ms",
                "hookRetries": 0,
                "drainTimeout": "300ms",
                "controlTimeout": "10s",
            },
        }
        self._gateway_config = self._work_dir / "aigw-system.json"
        self._gateway_config.write_text(json.dumps(gateway_config), encoding="utf-8")

    def _start_gateway(self) -> None:
        binary = Path(os.environ.get("AIGW_BIN", self._aigw_repo / "output/aigw/aigw"))
        if not binary.is_file():
            raise RuntimeError(f"AIGW binary is missing: {binary}; run 'bash build.sh' in AgentBox Adapter")
        self._gateway_log = (self._work_dir / "aigw-process.log").open("ab")
        self._gateway = subprocess.Popen(
            [str(binary), f"--config={self._gateway_config}"],
            cwd=self._aigw_repo,
            stdout=self._gateway_log,
            stderr=subprocess.STDOUT,
        )
        self._wait_for_gateway()

    def _wait_for_gateway(self) -> None:
        assert self._gateway is not None
        last_error: Exception | None = None
        for _ in range(200):
            if self._gateway.poll() is not None:
                output = (self._work_dir / "aigw-process.log").read_text(errors="replace")
                raise RuntimeError(f"AIGW exited during startup:\n{output[-4000:]}")
            try:
                response = requests.get(self._gateway_url + "/aigw/v1/health", timeout=0.2)
                if response.status_code == 200:
                    return
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(0.05)
        raise RuntimeError(f"AIGW did not become ready: {last_error}")

    def _register_vllm(self, indexes: list[int] | None = None) -> None:
        for index in indexes if indexes is not None else list(range(len(self._vllms))):
            port = self._vllm_ports[index]
            payload = {
                "name": f"fake-vllm-{index + 1}",
                "model": _MODEL_ID,
                "instanceIp": "127.0.0.1",
                "port": str(port),
                "role": "mixed",
                "groupID": "",
                "dpRank": -1,
            }
            for _ in range(100):
                response = self._request("POST", "/aigw/v1/register-instance", json=payload)
                if response.status_code == 200:
                    break
                if response.status_code != 503:
                    break
                time.sleep(0.05)
            if response.status_code != 200:
                raise RuntimeError(f"fake vLLM registration failed: {response.status_code} {response.text}")


__all__ = ["OnlineRLSystem"]
