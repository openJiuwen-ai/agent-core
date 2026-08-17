# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared Docker runtime helpers for CPU-only SFT rollout containers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.abstract.rollouter import (
    TaskRolloutCommandResult,
    TaskRolloutCommandSpec,
)

logger = logging.getLogger(__name__)

SFT_JIUWENCLAW_DOTENV_KEYS = (
    "API_BASE",
    "API_KEY",
    "MODEL_NAME",
    "CUSTOM_HEADERS",
    "TRAJECTORY_GATEWAY_URL",
    "TRAJECTORY_GATEWAY_API_KEY",
    "USE_RL_ONLINE_RAIL",
    "TRAIN_BACKEND",
    "SFT_ONLINE_UPLOAD_MODE",
    "RL_ONLINE_CAPTURE_MODE",
    "RL_ONLINE_SESSION_DONE_ON_INVOKE_END",
    "TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K",
    "RL_ONLINE_TENANT_ID",
    "WEB_USER_ID",
    "USE_CONTEXT_COMPRESSION_RAIL",
    "JIUWENSWARM_CONTEXT_WINDOW_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES",
    "JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS",
    "JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS",
    "SFT_SCENARIO",
    "SFT_TASK_PROMPT",
    "SFT_DOCKER_IMAGE",
    "SFT_INSTANCE_ID",
    "SFT_DATASET_CASE_JSON",
)


@dataclass(frozen=True)
class SFTJiuwenclawDockerRequest:
    """Inputs needed to run jiuwenclaw inside one CPU-only SWE Docker container."""

    image: str
    task_prompt: str
    instance_id: str
    dataset_case: dict[str, Any]
    gateway_url: str
    supervisor_url: str
    supervisor_token: str = "EMPTY"
    supervisor_model: str = ""
    tenant_id: str = "local-web-user"
    rollout_command: str = ""
    data_dir: str = ""
    sft_upload_mode: str = "raw"
    extra_env: dict[str, str] | None = None


def docker_runtime_mounts() -> tuple[list[str], str, str]:
    """Return source/conda mounts, container PYTHONPATH, and optional conda activation prefix."""

    agent_core_host = Path(
        os.getenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", "") or Path(__file__).resolve().parents[6]
    ).resolve()
    configured_jiuwenclaw_host = os.getenv("SFT_DOCKER_JIUWENCLAW_HOST_PATH", "").strip()
    jiuwenclaw_host = default_jiuwenclaw_host_path(agent_core_host)
    agent_core_container = os.getenv("SFT_DOCKER_AGENT_CORE_CONTAINER_PATH", str(agent_core_host))
    jiuwenclaw_container = os.getenv("SFT_DOCKER_JIUWENCLAW_CONTAINER_PATH", str(jiuwenclaw_host))

    mounts = ["-v", f"{agent_core_host}:{agent_core_container}:ro"]
    pythonpath = [agent_core_container]
    if configured_jiuwenclaw_host or jiuwenclaw_host.exists():
        mounts.extend(["-v", f"{jiuwenclaw_host}:{jiuwenclaw_container}:ro"])
        pythonpath.append(jiuwenclaw_container)
    command_prefix = host_conda_command_prefix(mounts)
    return mounts, ":".join(pythonpath), command_prefix


def build_jiuwenclaw_docker_command(request: SFTJiuwenclawDockerRequest) -> list[str]:
    """Build the shared CPU-only command used by original and supervisor SFT rollouts."""

    docker_mounts, pythonpath, command_prefix = docker_runtime_mounts()
    rollout_command = request.rollout_command.strip() or default_jiuwenclaw_task_command()
    wrapped_command = f"{command_prefix} {rollout_command}" if command_prefix else rollout_command
    dataset_case_json = json.dumps(
        normalize_dataset_case(
            request.dataset_case,
            image=request.image,
            task_prompt=request.task_prompt,
            instance_id=request.instance_id,
        ),
        ensure_ascii=False,
    )
    data_dir = request.data_dir or f"/tmp/jiuwenswarm-{request.instance_id or 'case'}"
    env = build_jiuwenclaw_docker_env(
        request,
        dataset_case_json=dataset_case_json,
        pythonpath=pythonpath,
        data_dir=data_dir,
    )
    env.update(request.extra_env or {})
    return [
        "docker",
        "run",
        "--rm",
        *docker_mounts,
        *_docker_env_args(env),
        request.image,
        "bash",
        "-lc",
        wrapped_command,
    ]


def build_jiuwenclaw_docker_env(
    request: SFTJiuwenclawDockerRequest,
    *,
    dataset_case_json: str,
    pythonpath: str,
    data_dir: str,
) -> dict[str, str]:
    """Build env for the SWE container and SFTOnlineRail injection.

    This is the only Python-side place that wires task metadata, supervisor LLM
    access, and gateway upload settings into jiuwenswarm/jiuwenclaw. Keeping
    these env vars together makes it easier to switch between raw replay and
    direct sample upload without changing the task container command.
    """

    env = {
        "SFT_TASK_PROMPT": request.task_prompt,
        "SFT_DOCKER_IMAGE": request.image,
        "SFT_INSTANCE_ID": request.instance_id,
        "SFT_DATASET_CASE_JSON": dataset_case_json,
        "API_BASE": f"{request.supervisor_url.rstrip('/')}/v1" if request.supervisor_url else "/v1",
        "API_KEY": request.supervisor_token,
        "MODEL_NAME": request.supervisor_model,
        "TRAJECTORY_GATEWAY_URL": request.gateway_url.rstrip("/"),
        "USE_RL_ONLINE_RAIL": "1",
        "TRAIN_BACKEND": "SFT",
        "SFT_ONLINE_UPLOAD_MODE": request.sft_upload_mode or "raw",
        "RL_ONLINE_CAPTURE_MODE": "raw_session",
        "RL_ONLINE_SESSION_DONE_ON_INVOKE_END": "1",
        "RL_ONLINE_TENANT_ID": request.tenant_id,
        "WEB_USER_ID": request.tenant_id,
        "PYTHONPATH": pythonpath,
        "JIUWENSWARM_DATA_DIR": data_dir,
        "SFT_JIUWENCLAW_HOME": data_dir,
        "SFT_TASK_PRINT_APP_LOG": os.getenv("SFT_TASK_PRINT_APP_LOG", "0"),
        "SFT_TASK_APP_LOG_TAIL": os.getenv("SFT_TASK_APP_LOG_TAIL", "240"),
        "SFT_TASK_MODE": os.getenv("SFT_TASK_MODE", "agent.fast"),
        "SFT_TASK_MAX_ITERATIONS": os.getenv("SFT_TASK_MAX_ITERATIONS", ""),
        "SFT_TASK_CHAT_TIMEOUT": os.getenv("SFT_TASK_CHAT_TIMEOUT", "600"),
    }
    # Keep the task container's prompt assembly below the supervisor/training
    # context budget without hard-coding one global value in the rollouter.
    env.update(_context_env_from_host())
    return env


def _context_env_from_host() -> dict[str, str]:
    keys = (
        "USE_CONTEXT_COMPRESSION_RAIL",
        "JIUWENSWARM_CONTEXT_WINDOW_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES",
        "JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS",
        "JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS",
    )
    return {key: os.environ[key] for key in keys if os.getenv(key)}


def _docker_env_args(env: dict[str, str]) -> list[str]:
    return [part for key, value in env.items() for part in ("-e", f"{key}={value}")]


def normalize_dataset_case(
    dataset_case: dict[str, Any],
    *,
    image: str,
    task_prompt: str,
    instance_id: str,
) -> dict[str, Any]:
    """Return dataset-case metadata with the fields required by SFTOnlineRail."""

    return {
        **dataset_case,
        "instance_id": instance_id,
        "image": image,
        "docker_image": image,
        "task_prompt": task_prompt,
        "prompt": task_prompt,
    }


def host_conda_command_prefix(mounts: list[str]) -> str:
    """Mount and activate the host conda env inside a SWE container."""

    if not env_bool("SFT_DOCKER_USE_HOST_CONDA", True):
        return ""
    configured_root = os.getenv("SFT_DOCKER_CONDA_ROOT", "").strip()
    conda_root = Path(configured_root or "/data1/lll/miniconda3").resolve()
    conda_env = os.getenv("SFT_DOCKER_CONDA_ENV", "openjiuwen-rl").strip() or "openjiuwen-rl"
    conda_sh = conda_root / "etc" / "profile.d" / "conda.sh"
    if not conda_sh.exists():
        if configured_root:
            logger.warning("SFT docker rollout configured host conda not found: %s", conda_sh)
        else:
            logger.warning("SFT docker rollout host conda not found: %s", conda_sh)
            return ""
        # Keep explicit SFT_DOCKER_CONDA_ROOT visible in generated commands for
        # CI/remote checks even when this host cannot validate that filesystem.
    mounts.extend(["-v", f"{conda_root}:{conda_root}:ro"])
    return f"set -e; source {shlex.quote(str(conda_sh))}; conda activate {shlex.quote(conda_env)};"


def env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_int(key: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return max(minimum, int(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using %s", key, raw, default)
        value = default
    return max(minimum, int(value))


def sft_rollout_concurrency(default: int = 1) -> int:
    """Return the shared concurrency knob for original and supervisor SFT rollouts."""

    value = env_int("SFT_ROLLOUT_CONCURRENCY", default, minimum=1)
    legacy = os.getenv("SFT_DOCKER_ROLLOUT_CONCURRENCY", "").strip()
    if legacy and "SFT_ROLLOUT_CONCURRENCY" not in os.environ:
        value = env_int("SFT_DOCKER_ROLLOUT_CONCURRENCY", default, minimum=1)
    return value


async def run_docker_command_spec(spec: TaskRolloutCommandSpec) -> TaskRolloutCommandResult:
    """Run one Docker command in a worker thread and keep only bounded logs."""

    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            spec.command,
            env=spec.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, int(spec.timeout_seconds)),
            check=False,
        )
        return TaskRolloutCommandResult(
            name=spec.name,
            command=spec.command,
            exit_code=completed.returncode,
            stdout_tail=completed.stdout[-20000:],
            stderr_tail=completed.stderr[-4000:],
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timeout_message = f"Command timed out after {spec.timeout_seconds}s"
        logger.warning("%s name=%s command=%s", timeout_message, spec.name, shlex.join(spec.command))
        return TaskRolloutCommandResult(
            name=spec.name,
            command=spec.command,
            exit_code=124,
            stdout_tail=str(stdout)[-20000:],
            stderr_tail=(f"{stderr}\n{timeout_message}")[-4000:],
        )


async def run_docker_command_specs(
    specs: list[TaskRolloutCommandSpec],
    *,
    concurrency: int,
) -> list[TaskRolloutCommandResult]:
    """Run Docker rollout commands concurrently while preserving input order."""

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def _run_one(spec: TaskRolloutCommandSpec) -> TaskRolloutCommandResult:
        async with semaphore:
            logger.info(
                "Starting SFT Docker command name=%s command=%s",
                spec.name,
                shlex.join(spec.command[:8] + ["..."]),
            )
            result = await run_docker_command_spec(spec)
            logger.info("Finished SFT Docker command name=%s exit=%s", spec.name, result.exit_code)
            return result

    return list(await asyncio.gather(*(_run_one(spec) for spec in specs)))


def default_jiuwenclaw_host_path(agent_core_host: Path) -> Path:
    configured = os.getenv("SFT_DOCKER_JIUWENCLAW_HOST_PATH", "").strip()
    if configured:
        return Path(configured).resolve()
    for candidate in (
        agent_core_host.parent.parent / "refactor" / "jiuwenclaw",
        agent_core_host.parent / "jiuwenclaw",
        agent_core_host.parent.parent / "jiuwenclaw",
    ):
        if candidate.exists():
            return candidate.resolve()
    return (agent_core_host.parent / "jiuwenclaw").resolve()


def default_jiuwenclaw_task_command() -> str:
    """Return a shell command that runs one jiuwenclaw prompt and exits."""

    return textwrap.dedent(
        r"""
        set -e
        export AGENT_CORE_ROOT="${PYTHONPATH%%:*}"
        export WEB_HOST="${WEB_HOST:-127.0.0.1}"
        export WEB_PORT="${WEB_PORT:-19000}"
        export AGENT_PORT="${AGENT_PORT:-18092}"
        export SFT_TASK_CWD="${SFT_TASK_CWD:-/testbed}"
        export SFT_TASK_MODE="${SFT_TASK_MODE:-agent.fast}"
        export SFT_JIUWENCLAW_HOME="${SFT_JIUWENCLAW_HOME:-$JIUWENSWARM_DATA_DIR}"
        export HOME="$SFT_JIUWENCLAW_HOME"
        python - <<'PY'
        try:
            from jiuwenswarm.common.utils import prepare_workspace
        except Exception:
            from jiuwenclaw.utils import prepare_workspace

        prepare_workspace(overwrite=False)
        PY
        python - <<'PY'
        import os

        max_iterations = os.environ.get("SFT_TASK_MAX_ITERATIONS", "").strip()
        light_config = os.environ.get("SFT_TASK_LIGHT_CONFIG", "").strip().lower() in {"1", "true", "yes", "on"}
        if max_iterations or light_config:
            try:
                import yaml
                from jiuwenswarm.common.utils import get_config_file

                path = get_config_file()
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                react = data.setdefault("react", {})
                if max_iterations:
                    react["max_iterations"] = int(max_iterations)
                    print(f"[sft-task] set react.max_iterations={max_iterations} config={path}", flush=True)
                if light_config:
                    react["skill_mode"] = os.environ.get("SFT_TASK_SKILL_MODE", "auto_list")
                    subagents = react.setdefault("subagents", {})
                    for name in ("general_agent", "browser_agent", "research_agent"):
                        subagents.setdefault(name, {})["enabled"] = False
                    data["auto_memory_enabled"] = False
                    memory = data.setdefault("memory", {})
                    memory["engine"] = "none"
                    tools = data.get("tools")
                    if isinstance(tools, list):
                        data["tools"] = [item for item in tools if item not in {"skill"}]
                    print("[sft-task] applied light jiuwenswarm config", flush=True)
                path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            except Exception as exc:
                print(f"[sft-task] warning: failed to update config: {exc!r}", flush=True)
        PY
        python - <<'PY' > "$JIUWENSWARM_DATA_DIR/app_entry"
        import importlib.util

        print("jiuwenswarm.app" if importlib.util.find_spec("jiuwenswarm.app") else "jiuwenclaw.app")
        PY
        python - <<'PY'
        import os
        from pathlib import Path

        keys = (
            "API_BASE",
            "API_KEY",
            "MODEL_NAME",
            "CUSTOM_HEADERS",
            "TRAJECTORY_GATEWAY_URL",
            "TRAJECTORY_GATEWAY_API_KEY",
            "USE_RL_ONLINE_RAIL",
            "TRAIN_BACKEND",
            "SFT_ONLINE_UPLOAD_MODE",
            "RL_ONLINE_CAPTURE_MODE",
            "RL_ONLINE_SESSION_DONE_ON_INVOKE_END",
            "TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K",
            "RL_ONLINE_TENANT_ID",
            "WEB_USER_ID",
            "SFT_SCENARIO",
            "SFT_TASK_PROMPT",
            "SFT_DOCKER_IMAGE",
            "SFT_INSTANCE_ID",
            "SFT_DATASET_CASE_JSON",
        )
        env_lines = []
        for key in keys:
            value = os.environ.get(key)
            if value is not None:
                escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
                env_lines.append(f'{key}="{escaped}"\n')

        path = Path(os.environ["JIUWENSWARM_DATA_DIR"]) / "sft_online.env"
        with path.open("w", encoding="utf-8") as f:
            f.writelines(env_lines)

        try:
            from jiuwenswarm.common.utils import get_env_file

            config_env = get_env_file()
            config_env.parent.mkdir(parents=True, exist_ok=True)
            managed_keys = {f"{key}=" for key in keys}
            existing = []
            if config_env.exists():
                for line in config_env.read_text(encoding="utf-8").splitlines(True):
                    if not any(line.startswith(prefix) for prefix in managed_keys):
                        existing.append(line)
            with config_env.open("w", encoding="utf-8") as f:
                f.writelines(existing)
                if existing and not existing[-1].endswith("\n"):
                    f.write("\n")
                f.writelines(env_lines)
            print(f"[sft-task] wrote dotenv path={path} config_env={config_env}", flush=True)
        except Exception as exc:
            print(f"[sft-task] warning: failed to sync config dotenv: {exc!r}", flush=True)
        PY
        python - <<'PY'
        import importlib
        import os

        path = os.path.join(os.environ["JIUWENSWARM_DATA_DIR"], "sft_online.env")
        try:
            from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail

            sft_rail_path = getattr(importlib.import_module(SFTOnlineRail.__module__), "__file__", "")
        except Exception as exc:
            sft_rail_path = f"<import failed: {exc!r}>"
        print("[sft-task] online rail env", {
            "dotenv": path,
            "USE_RL_ONLINE_RAIL": os.environ.get("USE_RL_ONLINE_RAIL"),
            "TRAIN_BACKEND": os.environ.get("TRAIN_BACKEND"),
            "RL_ONLINE_CAPTURE_MODE": os.environ.get("RL_ONLINE_CAPTURE_MODE"),
            "SFT_ONLINE_UPLOAD_MODE": os.environ.get("SFT_ONLINE_UPLOAD_MODE"),
            "RL_ONLINE_SESSION_DONE_ON_INVOKE_END": os.environ.get("RL_ONLINE_SESSION_DONE_ON_INVOKE_END"),
            "RL_ONLINE_TENANT_ID": os.environ.get("RL_ONLINE_TENANT_ID"),
            "WEB_USER_ID": os.environ.get("WEB_USER_ID"),
            "TRAJECTORY_GATEWAY_URL": os.environ.get("TRAJECTORY_GATEWAY_URL"),
            "TRAJECTORY_WAL_DIR": os.environ.get("TRAJECTORY_WAL_DIR"),
            "TRAJECTORY_FORCE_WAL": os.environ.get("TRAJECTORY_FORCE_WAL"),
            "SFTOnlineRail": sft_rail_path,
        }, flush=True)
        PY
        app_entry="$(tail -n 1 "$JIUWENSWARM_DATA_DIR/app_entry" | tr -d '\r')"
        app_log="$JIUWENSWARM_DATA_DIR/app.log"
        app_cmd=(python -m "$app_entry" --dotenv "$JIUWENSWARM_DATA_DIR/sft_online.env")
        if command -v setsid >/dev/null 2>&1; then
          setsid "${app_cmd[@]}" > "$app_log" 2>&1 &
        else
          "${app_cmd[@]}" > "$app_log" 2>&1 &
        fi
        app_pid=$!
        cleanup() {
          if [ "${SFT_TASK_PRINT_APP_LOG:-0}" = "1" ] && [ -f "$JIUWENSWARM_DATA_DIR/app.log" ]; then
            printf '%s\n' "===== jiuwenclaw app.log =====" >&2
            tail -n "${SFT_TASK_APP_LOG_TAIL:-240}" "$JIUWENSWARM_DATA_DIR/app.log" >&2 || true
            printf '%s\n' "===== end jiuwenclaw app.log =====" >&2
          fi
          kill -TERM "-$app_pid" >/dev/null 2>&1 || true
          pkill -TERM -P "$app_pid" >/dev/null 2>&1 || true
          kill "$app_pid" >/dev/null 2>&1 || true
          wait "$app_pid" >/dev/null 2>&1 || true
          kill -KILL "-$app_pid" >/dev/null 2>&1 || true
          pkill -KILL -P "$app_pid" >/dev/null 2>&1 || true
        }
        trap cleanup EXIT
        python - <<'PY'
        import asyncio
        import json
        import os
        import time
        import uuid

        import websockets

        prompt = os.environ["SFT_TASK_PROMPT"]
        session_id = "sft-task-" + os.environ.get("SFT_INSTANCE_ID", uuid.uuid4().hex[:8])
        cwd = os.environ.get("SFT_TASK_CWD") or "/testbed"
        url = f"ws://127.0.0.1:{os.environ.get('WEB_PORT', '19000')}/ws"

        async def connect_with_retry():
            deadline = time.time() + 120
            last_exc = None
            while time.time() < deadline:
                try:
                    return await websockets.connect(url, max_size=16 * 2**20, close_timeout=2)
                except Exception as exc:
                    last_exc = exc
                    await asyncio.sleep(1)
            raise RuntimeError(f"failed to connect jiuwenclaw websocket {url}: {last_exc!r}")

        async def main():
            ws = await connect_with_retry()
            async with ws:
                try:
                    hello = await asyncio.wait_for(ws.recv(), timeout=15)
                    print(f"[sft-task] connected hello={hello[:160]}")
                except Exception:
                    print("[sft-task] connected without hello")
                req_id = "chat-" + uuid.uuid4().hex[:12]
                frame = {
                    "type": "req",
                    "id": req_id,
                    "method": "chat.send",
                    "is_stream": True,
                    "params": {
                        "session_id": session_id,
                        "content": prompt,
                        "query": prompt,
                        "mode": os.environ.get("SFT_TASK_MODE", "agent.fast"),
                        "cwd": cwd,
                        "project_dir": cwd,
                        "trusted_dirs": [cwd],
                        "session_done": True,
                        "close_session": True,
                    },
                }
                await ws.send(json.dumps(frame, ensure_ascii=False))
                deadline = time.time() + int(os.environ.get("SFT_TASK_CHAT_TIMEOUT", "600"))
                while time.time() < deadline:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
                    event = msg.get("event") or payload.get("event_type")
                    if msg.get("type") == "res" and msg.get("id") == req_id:
                        print(f"[sft-task] ack ok={msg.get('ok')}")
                    if event == "chat.processing_status":
                        print(f"[sft-task] processing={payload.get('is_processing')}")
                        if payload.get("is_processing") is False:
                            return
                raise TimeoutError("jiuwenclaw task did not finish")

        asyncio.run(main())
        PY
        sleep "${SFT_TASK_UPLOAD_SETTLE_SECONDS:-5}"
        """
    ).strip()
