#!/usr/bin/env python3
# coding: utf-8

"""Skill entrypoint for direct SFT supervisor replay.

The jiuwenswarm skill calls this wrapper with the original user request. The
wrapper keeps the public skill prompt short by filling internal online-SFT
defaults here, then delegates the actual rollout/training task creation to the
skill-local ``scripts/run_sft_optimize.py`` entrypoint.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
AGENT_CORE_ROOT = Path(__file__).resolve().parents[5]
SKILL_DEFAULT_DATASET_MAPPING = SKILL_DIR / "data" / "sft_short_10_cases.json"
REPO_DEFAULT_DATASET_MAPPING = (
    AGENT_CORE_ROOT / "examples" / "jiuwenrl_online" / "sft_e2e" / "data" / "sft_short_10_cases.json"
)
DEFAULT_DATASET_MAPPING = str(
    SKILL_DEFAULT_DATASET_MAPPING if SKILL_DEFAULT_DATASET_MAPPING.is_file() else REPO_DEFAULT_DATASET_MAPPING
)
DEFAULT_GATEWAY_URL = "http://172.17.0.5:18080"
DEFAULT_SCHEDULER_URL = "http://127.0.0.1:18080"
DEFAULT_SUPERVISOR_URL = "http://172.17.0.5:18002"
DEFAULT_SUPERVISOR_MODEL = "Qwen3-0.6B"
DEFAULT_TENANT_ID = "local-web-user"
DEFAULT_LIMIT = 10
DEFAULT_CONCURRENCY = 2
DEFAULT_TIMEOUT = 900


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        default="",
        help="Original user request text. The wrapper extracts dataset/concurrency/limit and URL overrides from it.",
    )
    parser.add_argument("--dataset-mapping", default="")
    parser.add_argument("--gateway-url", default="")
    parser.add_argument("--scheduler-url", default="")
    parser.add_argument("--supervisor-url", default="")
    parser.add_argument("--supervisor-token", default="")
    parser.add_argument("--supervisor-model", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--backend", default="", help="Rollout backend passed through to run_sft_optimize.py.")
    parser.add_argument("--local-repo-root", default="")
    parser.add_argument("--local-repo-work-root", default="")
    parser.add_argument("--trigger-training", action="store_true", help="Always trigger SFT training after replay.")
    parser.add_argument("--no-trigger-training", action="store_true", help="Only collect SFT samples.")
    parser.add_argument("--dry-run", action="store_true", help="Show the delegated rollout command without starting Docker.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request = args.request.strip()
    config = _build_config(args=args, request=request)
    command = _build_rollout_command(config, dry_run=args.dry_run)
    env = _subprocess_env()

    print("[sft-optimize-skill] resolved_config=" + _format_public_config(config))
    print("[sft-optimize-skill] command=" + _format_public_command(command))
    completed = subprocess.run(command, text=True, check=False, env=env)
    return int(completed.returncode)


def _build_config(*, args: argparse.Namespace, request: str) -> dict[str, object]:
    return {
        "dataset_mapping": _first_value(
            args.dataset_mapping,
            _extract_dataset_mapping(request),
            os.getenv("SFT_OPTIMIZE_DATASET_MAPPING", ""),
            os.getenv("SFT_DATASET_MAPPING", ""),
            DEFAULT_DATASET_MAPPING,
        ),
        "gateway_url": _first_value(
            args.gateway_url,
            _extract_scalar(
                request,
                (
                    "SFT_DOCKER_GATEWAY_URL",
                    "RL_GATEWAY_URL",
                    "TRAJECTORY_GATEWAY_URL",
                    "gateway_url",
                ),
            ),
            _extract_url_after(
                request,
                (
                    "gateway",
                    "网关",
                ),
            ),
            os.getenv("SFT_DOCKER_GATEWAY_URL", ""),
            os.getenv("RL_GATEWAY_URL", ""),
            os.getenv("TRAJECTORY_GATEWAY_URL", ""),
            DEFAULT_GATEWAY_URL,
        ),
        "scheduler_url": _first_value(
            args.scheduler_url,
            _extract_scalar(request, ("RL_SCHEDULER_URL", "scheduler_url")),
            _extract_url_after(request, ("scheduler", "调度")),
            os.getenv("RL_SCHEDULER_URL", ""),
            DEFAULT_SCHEDULER_URL,
        ),
        "supervisor_url": _first_value(
            args.supervisor_url,
            _extract_scalar(request, ("SUPERVISOR_URL", "supervisor_url")),
            _extract_url_after(request, ("supervisor", "教师", "强模型")),
            os.getenv("SUPERVISOR_URL", ""),
            DEFAULT_SUPERVISOR_URL,
        ),
        "supervisor_token": _first_value(
            args.supervisor_token,
            _extract_scalar(request, ("SUPERVISOR_TOKEN", "supervisor_token", "token", "Token", "令牌")),
            os.getenv("SUPERVISOR_TOKEN", ""),
            "EMPTY",
        ),
        "supervisor_model": _first_value(
            args.supervisor_model,
            _extract_scalar(request, ("SUPERVISOR_MODEL", "supervisor_model", "model", "模型")),
            os.getenv("SUPERVISOR_MODEL", ""),
            DEFAULT_SUPERVISOR_MODEL,
        ),
        "tenant_id": _first_value(
            args.tenant_id,
            _extract_scalar(request, ("tenant_id", "user_id", "用户", "租户")),
            os.getenv("RL_ONLINE_TENANT_ID", ""),
            os.getenv("WEB_USER_ID", ""),
            DEFAULT_TENANT_ID,
        ),
        "limit": _first_int(args.limit, _extract_int(request, ("limit", "用例数", "样本数", "条数")), "SFT_OPTIMIZE_LIMIT", DEFAULT_LIMIT),
        "offset": _first_int(args.offset, _extract_int(request, ("offset", "偏移")), "SFT_OPTIMIZE_OFFSET", 0),
        "concurrency": _first_int(
            args.concurrency,
            _extract_int(request, ("concurrency", "并发")),
            "SFT_ROLLOUT_CONCURRENCY",
            DEFAULT_CONCURRENCY,
        ),
        "timeout": _first_int(args.timeout, _extract_int(request, ("timeout", "超时")), "SFT_TASK_ROLLOUT_TIMEOUT", DEFAULT_TIMEOUT),
        "backend": _first_value(getattr(args, "backend", ""), os.getenv("SFT_ROLLOUT_BACKEND", ""), "docker"),
        "local_repo_root": _first_value(getattr(args, "local_repo_root", ""), os.getenv("SFT_LOCAL_REPO_ROOT", "")),
        "local_repo_work_root": _first_value(
            getattr(args, "local_repo_work_root", ""),
            os.getenv("SFT_LOCAL_REPO_WORK_ROOT", ""),
        ),
        "trigger_training": _resolve_trigger_training(args=args, request=request),
    }


def _build_rollout_command(config: dict[str, object], *, dry_run: bool) -> list[str]:
    script = _rollout_script_path()
    command = [
        _python_executable(),
        str(script),
        "--backend",
        str(config["backend"]),
        "--dataset-mapping",
        str(config["dataset_mapping"]),
        "--limit",
        str(config["limit"]),
        "--offset",
        str(config["offset"]),
        "--gateway-url",
        str(config["gateway_url"]),
        "--scheduler-url",
        str(config["scheduler_url"]),
        "--supervisor-url",
        str(config["supervisor_url"]),
        "--supervisor-token",
        str(config["supervisor_token"]),
        "--supervisor-model",
        str(config["supervisor_model"]),
        "--tenant-id",
        str(config["tenant_id"]),
        "--concurrency",
        str(config["concurrency"]),
        "--timeout",
        str(config["timeout"]),
    ]
    if str(config.get("local_repo_root") or "").strip():
        command.extend(["--local-repo-root", str(config["local_repo_root"])])
    if str(config.get("local_repo_work_root") or "").strip():
        command.extend(["--local-repo-work-root", str(config["local_repo_work_root"])])
    if bool(config["trigger_training"]):
        command.append("--trigger-training")
    if dry_run:
        command.append("--dry-run")
    return command


def _rollout_script_path() -> Path:
    local_script = Path(__file__).resolve().with_name("run_sft_optimize.py")
    if local_script.is_file():
        return local_script
    return _agent_core_root() / "examples" / "jiuwenrl_online" / "sft_rollout" / "run_sft_optimize.py"


def _python_executable() -> str:
    configured = os.getenv("SFT_OPTIMIZE_PYTHON", "").strip() or os.getenv("ONLINE_RL_PYTHON", "").strip()
    if configured:
        return configured
    if os.getenv("USE_CONDA", "1").strip().lower() in {"0", "false", "no", "off"}:
        return sys.executable
    conda_env = os.getenv("SFT_OPTIMIZE_CONDA_ENV", "").strip() or os.getenv("ONLINE_RL_CONDA_ENV", "").strip()
    candidates: list[Path] = []
    if conda_env:
        candidates.append(Path("/data1/lll/miniconda3/envs") / conda_env / "bin" / "python")
    candidates.append(Path("/data1/lll/miniconda3/envs/openjiuwen-sft/bin/python"))
    candidates.append(Path("/data1/lll/miniconda3/envs/openjiuwen-rl/bin/python"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _agent_core_root() -> Path:
    configured = os.getenv("AGENT_CORE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (Path(__file__).resolve().parents[5], Path.cwd()):
        if (candidate / "examples" / "jiuwenrl_online" / "sft_rollout" / "run_sft_optimize.py").is_file():
            return candidate.resolve()
    raise RuntimeError("failed to locate agent-core root; set AGENT_CORE_ROOT")


def _resolve_trigger_training(*, args: argparse.Namespace, request: str) -> bool:
    if args.no_trigger_training:
        return False
    if args.trigger_training:
        return True
    lowered = request.lower()
    if any(token in request for token in ("只采集", "仅采集", "不训练", "不要训练", "暂不训练")):
        return False
    if "collect only" in lowered or "no train" in lowered:
        return False
    if any(token in request for token in ("微调", "训练", "优化模型", "触发训练")):
        return True
    return _env_bool("SFT_OPTIMIZE_TRIGGER_TRAINING", default=True)


def _extract_dataset_mapping(text: str) -> str:
    labelled = _extract_scalar(text, ("dataset_mapping", "dataset", "cases", "数据集", "映射文件"))
    if labelled and _looks_like_dataset_path(labelled):
        return labelled
    match = re.search(r"(/[^ \n\r\t,，;；]+(?:\.jsonl?|\.md))", text)
    if match:
        return match.group(1)
    return ""


def _extract_url_after(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        # Users often write mixed labels such as "supervisor LLM 是 URL".
        # Keep request-provided URLs ahead of env defaults even when there are
        # a few descriptive words between the label and the URL.
        label_match = re.search(re.escape(label), text, flags=re.IGNORECASE)
        if label_match:
            window = text[label_match.start() : label_match.start() + 160]
            url_match = re.search(r"https?://[^ \n\r\t,，;；]+", window)
            if url_match:
                return _clean_scalar(url_match.group(0))
    return ""


def _extract_scalar(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*(?:=|:|：|是|为)\s*([^ \n\r\t,，;；]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_scalar(match.group(1))
    return ""


def _extract_int(text: str, labels: tuple[str, ...]) -> int:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*(?:=|:|：|是|为)?\s*(\d+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    if any(label in labels for label in ("limit", "用例数", "样本数", "条数")) and ("用例" in text or "轨迹" in text):
        match = re.search(r"(\d+)\s*(?:个|条)?(?:用例|轨迹|case|cases)", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    root = str(_agent_core_root())
    current = env.get("PYTHONPATH", "")
    paths = [root]
    if current:
        paths.append(current)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _first_value(*values: object) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _first_int(cli_value: int, extracted_value: int, env_key: str, default: int) -> int:
    if cli_value > 0:
        return cli_value
    if extracted_value > 0:
        return extracted_value
    raw = os.getenv(env_key, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(0, int(default))


def _looks_like_dataset_path(value: str) -> bool:
    return value.endswith((".json", ".jsonl", ".md"))


def _clean_scalar(value: str) -> str:
    return value.strip().strip("'\"`").rstrip("，,。.!！?？、；;")


def _env_bool(key: str, *, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _format_public_config(config: dict[str, object]) -> str:
    public = dict(config)
    public["supervisor_token"] = "***" if str(public.get("supervisor_token") or "") not in {"", "EMPTY"} else public.get("supervisor_token")
    return ", ".join(f"{key}={value}" for key, value in public.items())


def _format_public_command(command: list[str]) -> str:
    sanitized: list[str] = []
    hide_next = False
    for part in command:
        if hide_next:
            sanitized.append("***" if part != "EMPTY" else part)
            hide_next = False
            continue
        sanitized.append(part)
        if part == "--supervisor-token":
            hide_next = True
    return " ".join(_shell_quote(part) for part in sanitized)


def _shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
