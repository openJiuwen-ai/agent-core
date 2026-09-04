"""Small runtime helpers shared by the Evo-Bench RSI adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

DEFAULT_ENV_FILE = Path(".local/rsi/evobench.env")
DEFAULT_POLICY_CONFIG = Path(".local/rsi/models/token_plan_deepseek_v4_flash_single_harness.yaml")
DEFAULT_JUDGE_MODEL = "Qwen3.7-Plus"
DEFAULT_E2B_TEMPLATE = "evobench-20260808"


@dataclass(frozen=True)
class LocalModel:
    """Resolved Jiuwen model settings needed by the official evaluator."""

    api_base: str
    api_key: str
    model: str


def resolve_evobench_root(value: str) -> Path:
    """Find a usable Evo-Bench checkout from an argument or environment."""
    candidates: list[Path] = []
    if value.strip():
        candidates.append(Path(value).expanduser())
    if os.environ.get("EVOBENCH_ROOT", "").strip():
        candidates.append(Path(os.environ["EVOBENCH_ROOT"]).expanduser())
    workspace = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            workspace.parent / "Evo-Bench-official" / "Evo-Bench-main",
            workspace.parent / "Evo-Bench",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "evobench" / "cli.py").is_file():
            return resolved
    raise FileNotFoundError("Evo-Bench repository not found; pass --evobench-root or set EVOBENCH_ROOT")


def load_local_model(path: Path) -> LocalModel:
    """Load OpenAI-compatible fields from a Jiuwen model YAML."""
    resolved = path.expanduser().resolve()
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"model config must be a mapping: {resolved}")
    client = payload.get("model_client_config")
    request = payload.get("model_request_config")
    if not isinstance(client, dict) or not isinstance(request, dict):
        raise ValueError(f"model config has no Jiuwen client/request sections: {resolved}")
    values = {
        "api_base": _expand_model_value(client.get("api_base", ""), path=resolved),
        "api_key": _expand_model_value(client.get("api_key", ""), path=resolved),
        "model": _expand_model_value(request.get("model", ""), path=resolved),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"model config {resolved} is missing: {', '.join(missing)}")
    return LocalModel(**values)


def _expand_model_value(value: Any, *, path: Path) -> str:
    expanded = os.path.expandvars(str(value or "")).strip()
    unresolved = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", expanded)
    if unresolved:
        raise ValueError(f"model config {path} requires environment variables: {', '.join(sorted(set(unresolved)))}")
    return expanded


def write_evobench_model_config(
    path: Path,
    *,
    api_base_env: str,
    api_key_env: str,
    model: str,
    role: str,
) -> None:
    """Write a credential-free official Evo-Bench model configuration."""
    if role not in {"policy", "evolver", "judge"}:
        raise ValueError(f"unknown Evo-Bench model role: {role}")
    payload: dict[str, Any] = {
        "provider": "openai-compatible",
        "api_base_env": api_base_env,
        "api_key_env": api_key_env,
        "model": model,
        "temperature": 0.0 if role == "judge" else 1.0,
        "max_output_tokens": 65_536,
        "timeout_seconds": 1_200 if role == "judge" else 600,
        "require_api_key": True,
        "context_window_tokens": 256_000 if role == "policy" else 1_000_000,
    }
    if role != "judge":
        payload["reasoning_effort"] = "max"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE file."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def wsl_runtime_credentials() -> dict[str, str]:
    """Read named benchmark credentials already configured in WSL."""
    if os.name != "nt" or (os.environ.get("E2B_API_KEY") and os.environ.get("SERPER_API_KEY")):
        return {}
    try:
        completed = subprocess.run(
            ["wsl.exe", "bash", "-ic", 'printf \'%s\\n%s\' "$E2B_API_KEY" "$SERPER_API_KEY"'],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    values = completed.stdout.decode("utf-8", errors="ignore").splitlines()
    if completed.returncode != 0 or len(values) != 2:
        return {}
    return {
        name: value.strip().strip("\ufeff")
        for name, value in zip(("E2B_API_KEY", "SERPER_API_KEY"), values, strict=True)
        if value.strip().strip("\ufeff")
    }


def to_wsl(path: Path) -> str:
    """Translate an absolute host path to the corresponding WSL path."""
    resolved = path.expanduser().resolve()
    if os.name != "nt":
        return resolved.as_posix()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise ValueError(f"expected an absolute Windows path: {resolved}")
    return f"/mnt/{drive}/{PurePosixPath(*resolved.parts[1:]).as_posix()}"


def wsl_subprocess_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Forward selected variables through WSLENV instead of command argv."""
    environment = dict(os.environ)
    environment.update(values)
    forwarded = set(values)
    inherited = [
        item
        for item in environment.get("WSLENV", "").split(":")
        if item and item.split("/", 1)[0] not in forwarded
    ]
    environment["WSLENV"] = ":".join([*inherited, *values])
    return environment


__all__ = [
    "DEFAULT_E2B_TEMPLATE",
    "DEFAULT_ENV_FILE",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_POLICY_CONFIG",
    "load_local_model",
    "read_env_file",
    "resolve_evobench_root",
    "to_wsl",
    "write_evobench_model_config",
    "wsl_runtime_credentials",
    "wsl_subprocess_environment",
]
