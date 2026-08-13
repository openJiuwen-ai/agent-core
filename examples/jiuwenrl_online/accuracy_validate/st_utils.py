# coding: utf-8

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ST_DIR = Path(__file__).resolve().parent
AGENT_CORE_ROOT = ST_DIR.parents[2]
WORKSPACE_ROOT = AGENT_CORE_ROOT.parent
JIUWENRL_DIR = AGENT_CORE_ROOT / "examples" / "jiuwenrl_online"
FIXTURE_DIR = ST_DIR / "fixtures"
DIAGNOSTICS_DIR = ST_DIR / "diagnostics"
DEFAULT_TIMEOUT_SEC = float(os.getenv("ST_TEST_HTTP_TIMEOUT", "120"))


@dataclass(frozen=True)
class CompletionSignature:
    text: str
    token_ids: tuple[int, ...]
    finish_reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "token_ids": list(self.token_ids),
            "finish_reason": self.finish_reason,
        }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_json_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def post_json(url: str, payload: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            if not body.strip():
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"text": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"POST {url} failed: status={exc.code} body={body[:1000]}") from exc


def get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"GET {url} failed: status={exc.code} body={body[:1000]}") from exc


def vllm_url() -> str:
    return os.getenv("VLLM_URL", "http://127.0.0.1:18002").rstrip("/")


def model_name() -> str:
    return os.getenv("MODEL_NAME", "Qwen3-4B-Thinking-2507")


def deterministic_chat_body(messages: list[dict[str, str]], *, model: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model or model_name(),
        "messages": messages,
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": int(os.getenv("ST_TEST_TOP_K", "1")),
        "max_tokens": int(os.getenv("ST_TEST_MAX_TOKENS", "24")),
        "n": 1,
        "stream": False,
        "seed": int(os.getenv("ST_TEST_SEED", "20260713")),
        "logprobs": True,
        "top_logprobs": 1,
        "return_token_ids": True,
    }
    return body


def call_vllm_chat(messages: list[dict[str, str]], *, model: str | None = None) -> dict[str, Any]:
    body = deterministic_chat_body(messages, model=model)
    try:
        return post_json(f"{vllm_url()}/v1/chat/completions", body)
    except AssertionError as exc:
        if "top_k" not in str(exc):
            raise
        body.pop("top_k", None)
        return post_json(f"{vllm_url()}/v1/chat/completions", body)


def extract_signature(response: dict[str, Any]) -> CompletionSignature:
    choices = response.get("choices")
    assert isinstance(choices, list) and choices, f"missing choices: {response}"
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else {}
    text = str((message or {}).get("content") or choice.get("text") or "")
    token_ids = (
        choice.get("token_ids")
        or choice.get("completion_token_ids")
        or choice.get("response_token_ids")
        or response.get("completion_token_ids")
        or response.get("response_token_ids")
        or []
    )
    finish_reason = str(choice.get("finish_reason") or "")
    return CompletionSignature(
        text=text,
        token_ids=tuple(int(x) for x in token_ids),
        finish_reason=finish_reason,
    )


def extract_prompt_token_ids(response: dict[str, Any]) -> tuple[int, ...]:
    candidates = [response.get("prompt_token_ids"), response.get("prompt_ids")]
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            candidates.extend([choice.get("prompt_token_ids"), choice.get("prompt_ids")])
    for item in candidates:
        if isinstance(item, list) and item:
            return tuple(int(x) for x in item)
    return ()


def extract_logprobs(response: dict[str, Any]) -> tuple[float, ...]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ()
    direct = choices[0].get("logprobs")
    if isinstance(direct, list):
        return tuple(float(x) for x in direct)
    content = direct.get("content") if isinstance(direct, dict) else None
    if isinstance(content, list):
        values = []
        for item in content:
            if isinstance(item, dict) and "logprob" in item:
                values.append(float(item["logprob"]))
        return tuple(values)
    return ()


def adapter_dir_for(repo_root: Path, user_id: str) -> Path:
    latest = repo_root / user_id / "latest"
    if latest.exists() or latest.is_symlink():
        return latest.resolve()
    versions = sorted((repo_root / user_id).glob("v*"), key=lambda p: int(p.name[1:]))
    assert versions, f"no LoRA version found under {repo_root / user_id}"
    return versions[-1]


def adapter_model_file(adapter_dir: Path) -> Path:
    candidates = [
        adapter_dir / "adapter_model.safetensors",
        adapter_dir / "adapter_model.bin",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise AssertionError(f"missing adapter model file in {adapter_dir}")


def adapter_manifest(adapter_dir: Path) -> dict[str, Any]:
    config_path = adapter_dir / "adapter_config.json"
    metadata_path = adapter_dir / "metadata.json"
    model_path = adapter_model_file(adapter_dir)
    assert config_path.exists(), f"missing {config_path}"
    assert metadata_path.exists(), f"missing {metadata_path}"
    metadata = load_json(metadata_path)
    metadata.pop("created_at", None)
    metadata.pop("version", None)
    return {
        "adapter_dir": str(adapter_dir),
        "adapter_config": load_json(config_path),
        "metadata_without_timestamp": metadata,
        "adapter_model_name": model_path.name,
        "adapter_config_sha256": sha256_file(config_path),
        "adapter_model_sha256": sha256_file(model_path),
    }


def compare_adapter_tensors(left: Path, right: Path) -> dict[str, Any]:
    import torch

    left_file = adapter_model_file(left)
    right_file = adapter_model_file(right)
    if left_file.suffix == ".safetensors":
        from safetensors.torch import load_file

        left_state = load_file(str(left_file), device="cpu")
        right_state = load_file(str(right_file), device="cpu")
    else:
        left_state = torch.load(str(left_file), map_location="cpu")
        right_state = torch.load(str(right_file), map_location="cpu")

    left_keys = set(left_state)
    right_keys = set(right_state)
    common = sorted(left_keys & right_keys)
    max_abs = 0.0
    mean_abs_num = 0.0
    mean_abs_den = 0
    different_tensors = 0
    for key in common:
        lhs = left_state[key].detach().float()
        rhs = right_state[key].detach().float()
        assert tuple(lhs.shape) == tuple(rhs.shape), f"shape mismatch for {key}: {lhs.shape} vs {rhs.shape}"
        diff = (lhs - rhs).abs()
        current_max = float(diff.max().item()) if diff.numel() else 0.0
        max_abs = max(max_abs, current_max)
        mean_abs_num += float(diff.sum().item())
        mean_abs_den += int(diff.numel())
        if current_max != 0.0:
            different_tensors += 1
    return {
        "left_only": sorted(left_keys - right_keys),
        "right_only": sorted(right_keys - left_keys),
        "common_tensors": len(common),
        "different_tensors": different_tensors,
        "max_abs": max_abs,
        "mean_abs": mean_abs_num / mean_abs_den if mean_abs_den else 0.0,
    }


def run_direct_training(*, fixture: Path, work_dir: Path, run_name: str) -> Path:
    script = JIUWENRL_DIR / "train_only" / "train_online_rl_from_trajectory_json.py"
    assert script.exists(), f"missing training script: {script}"
    lora_repo = work_dir / f"lora_repo_{run_name}"
    tmp_root = work_dir / f"records_{run_name}"
    user_id = f"st-a5-{run_name}"
    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "20260713")
    env.setdefault("ONLINE_RL_DETERMINISTIC_SEED", os.getenv("ST_TEST_SEED", "20260713"))
    env.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    env.setdefault("HYDRA_FULL_ERROR", "1")
    env.setdefault("RAY_DEDUP_LOGS", "0")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env["PYTHONPATH"] = (
        f"{DIAGNOSTICS_DIR}:{AGENT_CORE_ROOT}:{WORKSPACE_ROOT / 'jiuwenclaw'}:"
        f"{env.get('PYTHONPATH', '')}"
    )
    env["LORA_REPO"] = str(lora_repo)
    env["DIRECT_TRAIN_USER_ID"] = user_id
    env["DIRECT_TRAINING_COUNT"] = "1"
    env["ONLINE_RL_PPO_SAMPLES_PER_STEP"] = os.getenv("ONLINE_RL_PPO_SAMPLES_PER_STEP", "4")
    env["TRAIN_THRESHOLD"] = os.getenv("TRAIN_THRESHOLD", "4")
    visible_env = env.get("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
    env[visible_env] = os.getenv("TRAIN_GPU", env.get("TRAIN_GPU", "4,5,6,7"))

    cmd = [
        sys.executable,
        str(script),
        str(fixture),
        "--lora-repo",
        str(lora_repo),
        "--user-id",
        user_id,
        "--train-gpu",
        os.getenv("TRAIN_GPU", env.get("TRAIN_GPU", "4,5,6,7")),
        "--ppo-samples-per-step",
        env["ONLINE_RL_PPO_SAMPLES_PER_STEP"],
        "--training-count",
        "1",
        "--tmp-root",
        str(tmp_root),
        "--no-hotload",
    ]
    if os.getenv("MODEL_PATH"):
        cmd.extend(["--model-path", os.environ["MODEL_PATH"]])
    if os.getenv("MODEL_NAME"):
        cmd.extend(["--model-name", os.environ["MODEL_NAME"]])
    if os.getenv("PPO_CONFIG_PATH"):
        cmd.extend(["--ppo-config-path", os.environ["PPO_CONFIG_PATH"]])

    log_path = work_dir / f"train_{run_name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(os.getenv("ST_TEST_TRAIN_TIMEOUT_SEC", "7200")),
        )
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:])
        raise AssertionError(f"training failed for {run_name}, log={log_path}\n{tail}")
    return adapter_dir_for(lora_repo, user_id)


def load_lora_adapter(name: str, adapter_dir: Path) -> None:
    payload = {
        "lora_name": name,
        "lora_path": str(adapter_dir),
        "load_inplace": True,
    }
    post_json(f"{vllm_url()}/v1/load_lora_adapter", payload, timeout=DEFAULT_TIMEOUT_SEC)
    deadline = time.time() + float(os.getenv("ST_TEST_LORA_LOAD_TIMEOUT_SEC", "120"))
    while time.time() < deadline:
        models = get_json(f"{vllm_url()}/v1/models")
        for item in models.get("data", []):
            if item.get("id") == name:
                root = str(item.get("root") or "")
                if not root or Path(root).resolve() == adapter_dir.resolve():
                    return
        time.sleep(1)
    raise AssertionError(f"LoRA {name} was not visible in /v1/models after load")


def maybe_copy_artifacts(tmp_path: Path) -> None:
    if os.getenv("ST_TEST_KEEP_ARTIFACTS", "0") != "1":
        return
    dst_root = Path(os.getenv("ST_TEST_ARTIFACT_ROOT", str(ST_DIR / "artifacts")))
    dst = dst_root / re.sub(r"[^A-Za-z0-9_.-]+", "_", tmp_path.name)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(tmp_path, dst)
