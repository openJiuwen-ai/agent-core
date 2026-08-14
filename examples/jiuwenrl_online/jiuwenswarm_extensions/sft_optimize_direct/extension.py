from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys
from typing import Any

from jiuwenswarm.extensions.hook_event import AgentServerHookEvents
from jiuwenswarm.extensions.sdk import BaseExtension


logger = logging.getLogger(__name__)


class SFTOptimizeDirectExtension(BaseExtension):
    """Bridge a local jiuwenswarm skill request to the SFT rollout wrapper.

    The regular `/skills use` flow asks the model to read SKILL.md and plan tool
    execution. Small local models may stop after reading the skill, so this
    extension makes the production SFT optimize entry deterministic without
    changing jiuwenswarm code: a matching user message is intercepted before the
    model call, the existing wrapper is executed, and the model only sees a
    short execution summary.
    """

    def __init__(self) -> None:
        self._registry = None

    async def initialize(self, config) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def register(self, registry) -> None:
        self._registry = registry
        registry.register(
            AgentServerHookEvents.BEFORE_CHAT_REQUEST,
            self._before_chat_request,
            priority=5,
        )

    async def _before_chat_request(self, ctx: Any) -> None:
        params = getattr(ctx, "params", None)
        if not isinstance(params, dict):
            return
        if params.get("_sft_optimize_direct_handled"):
            return
        request = _request_text(params)
        if not _should_handle(request):
            return

        params["_sft_optimize_direct_handled"] = True
        result = await _run_skill_wrapper(request)
        summary = _summary_text(result)
        params["query"] = summary
        params["content"] = summary
        params.setdefault("metadata", {})
        if isinstance(params["metadata"], dict):
            params["metadata"]["sft_optimize_direct"] = result


async def register_extensions(registry):
    extension = SFTOptimizeDirectExtension()
    extension.register(registry)
    return [extension]


def _request_text(params: dict[str, Any]) -> str:
    for key in ("query", "content"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _should_handle(request: str) -> bool:
    if os.getenv("SFT_OPTIMIZE_DIRECT_ENABLED", "1").lower() not in {"1", "true", "yes", "on"}:
        return False
    text = request.lower()
    if "sft-optimize" not in text and "sft optimize" not in text and "supervisor replay" not in text:
        return False
    return "数据集" in request or "dataset" in text or "dataset_mapping" in text


async def _run_skill_wrapper(request: str) -> dict[str, Any]:
    root = _agent_core_root()
    script = root / "examples" / "jiuwenrl_online" / "skills" / "sft-optimize" / "scripts" / "run_sft_optimize_skill.py"
    command = [_python_executable(), str(script), "--request", request]
    env = dict(os.environ)
    env["AGENT_CORE_ROOT"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join([str(root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    logger.info("[SFTOptimizeDirectExtension] running wrapper: %s", _redact_command(command))
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
    if proc.returncode == 0:
        logger.info("[SFTOptimizeDirectExtension] wrapper succeeded")
    else:
        logger.error("[SFTOptimizeDirectExtension] wrapper failed rc=%s stderr=%s", proc.returncode, stderr[-2000:])
    return {
        "returncode": int(proc.returncode or 0),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def _summary_text(result: dict[str, Any]) -> str:
    status = "成功" if result.get("returncode") == 0 else "失败"
    stdout_tail = str(result.get("stdout_tail") or "").strip()
    stderr_tail = str(result.get("stderr_tail") or "").strip()
    details = stdout_tail or stderr_tail or "no output"
    return (
        "sft-optimize 已执行完成。\n"
        f"状态: {status}\n"
        f"退出码: {result.get('returncode')}\n"
        "关键输出:\n"
        f"{details[-1800:]}"
    )


def _python_executable() -> str:
    configured = os.getenv("SFT_OPTIMIZE_PYTHON", "").strip() or os.getenv("ONLINE_RL_PYTHON", "").strip()
    if configured:
        return configured
    if os.getenv("USE_CONDA", "1").strip().lower() in {"0", "false", "no", "off"}:
        return sys.executable
    for candidate in (
        Path("/data1/lll/miniconda3/envs/openjiuwen-sft/bin/python"),
        Path("/data1/lll/miniconda3/envs/openjiuwen-rl/bin/python"),
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _agent_core_root() -> Path:
    configured = os.getenv("AGENT_CORE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "examples" / "jiuwenrl_online" / "sft_rollout" / "run_sft_optimize.py").is_file():
            return parent
    return Path.cwd().resolve()


def _redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, item in enumerate(redacted):
        if item == "--request" and index + 1 < len(redacted):
            redacted[index + 1] = "<request>"
    return redacted
