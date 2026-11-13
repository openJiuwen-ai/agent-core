#!/usr/bin/env python3
"""SWE-bench rollout runner with pluggable sandbox backends.

The default backend creates one isolated YuanRong ``sandbox_type=docker``
instance per case.  ``sandbox.backend=local_docker`` uses the local Docker CLI
with the same payload and lifecycle interface.  The instance runs this file in
bootstrap mode; JiuwenSwarm/OpenJiuwen are injected by read-only runtime mounts
and SFTOnlineRail writes a local WAL that the host promotes and uploads after
evaluation.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
import concurrent.futures
import copy
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$")
NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
HOST_WORKSPACE_ROOT_ENV = "SFT_SWE_HOST_WORKSPACE_ROOT"
HOST_WORKSPACE_SUFFIX = (".sft-swe-direct", "children")
GOLDEN_START_DEFAULT = "## Internal Note: Pair Programming Handoff"
GOLDEN_END_DEFAULT = "## STATEMENT-ENDS"
SFT_SAMPLE_PROTOCOL = "sft-sample-v1"
SUPERVISOR_PROFILE_FIELDS = ("model_name", "provider", "api_base", "api_key")
# This is deliberately a neutral placeholder.  Deployments must provide the
# host directory that contains YuanRong's ``yr`` package in suite/config.json;
# silently falling back to a path from one developer machine makes a packaged
# skill non-portable and turns a missing mount into a confusing timeout.
DEFAULT_HOST_SITE_PACKAGES = "/path/to/host/site-packages"
# The prompt/config selects a non-secret alias, never an arbitrary credential
# path.  The first location is the 5173 main-Agent path; the second supports
# direct host-side diagnostics by the deployment user.
SUPERVISOR_PROFILES = {
    "supervisor-default": (
        Path("/home/root/.sft-swe-rollout/supervisor-profile.json"),
        Path.home() / ".config/sft-swe-rollout/supervisor-profile.json",
    ),
}


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc


def load_supervisor_profile(alias: str) -> dict[str, str]:
    """Load a provider-neutral supervisor model profile by a fixed alias."""
    candidates = SUPERVISOR_PROFILES.get(alias)
    if candidates is None:
        raise RuntimeError(
            "unknown supervisor profile alias; allowed aliases: "
            + ", ".join(sorted(SUPERVISOR_PROFILES))
        )
    supplied = next((path for path in candidates if path.exists()), candidates[0])
    if supplied.is_symlink():
        raise RuntimeError("supervisor profile must not be a symlink")
    try:
        resolved = supplied.resolve(strict=True)
        profile_stat = resolved.stat()
    except OSError as exc:
        raise RuntimeError(
            f"supervisor profile {alias!r} is missing or unreadable at its protected location"
        ) from exc
    if not stat.S_ISREG(profile_stat.st_mode):
        raise RuntimeError("supervisor profile must be a regular file")
    if profile_stat.st_uid not in {0, os.geteuid()}:
        raise RuntimeError("supervisor profile must be owned by root or the current user")
    if profile_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError("supervisor profile must not be accessible by group or other users")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("supervisor profile is not valid UTF-8 JSON") from exc
    if not isinstance(raw, Mapping):
        raise RuntimeError("supervisor profile must contain one JSON object")
    actual_fields = {str(key) for key in raw}
    expected_fields = set(SUPERVISOR_PROFILE_FIELDS)
    if actual_fields != expected_fields:
        raise RuntimeError(
            "supervisor profile fields must be exactly: "
            + ", ".join(SUPERVISOR_PROFILE_FIELDS)
        )
    values: dict[str, str] = {}
    for field in SUPERVISOR_PROFILE_FIELDS:
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("supervisor profile fields must all be nonempty strings")
        values[field] = value.strip()
    if any(any(char in value for char in ("\n", "\r", "\x00")) for value in values.values()):
        raise RuntimeError("supervisor profile fields must not contain control characters")
    parsed = urllib.parse.urlsplit(values["api_base"])
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("supervisor profile api_base must be a credential-free HTTPS URL")
    return values


def load_rows(path: Path, reader_python: Path | None = None) -> list[dict[str, Any]]:
    """Load a SWE dataset from JSON/JSONL or a local parquet file.

    Parquet support is deliberately implemented here instead of importing an
    old runner helper.  The control/evaluator environment normally contains
    pyarrow while the host skill environment does not, so parquet is decoded
    by that explicitly configured Python interpreter.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = load_json(path)
        rows = value.get("instances", value.get("cases", value)) if isinstance(value, dict) else value
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif suffix == ".parquet":
        if reader_python is None:
            raise RuntimeError(f"parquet reader Python is not configured: {path}")
        code = (
            "import json, pyarrow.parquet as pq, sys; "
            "print(json.dumps(pq.read_table(sys.argv[1]).to_pylist(), ensure_ascii=False))"
        )
        try:
            proc = subprocess.run(
                [str(reader_python), "-c", code, str(path)], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"unable to read parquet dataset {path}: {exc}") from exc
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"parquet reader returned invalid JSON for {path}: {proc.stderr[-500:]}") from exc
    else:
        raise RuntimeError(f"unsupported dataset format: {path}")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"dataset must contain a list of objects: {path}")
    return [dict(row) for row in rows]


def marker_bounds(config: dict[str, Any]) -> tuple[str, str]:
    gold = config.get("gold") or {}
    return (
        str(gold.get("marker_start") or GOLDEN_START_DEFAULT),
        str(gold.get("marker_end") or GOLDEN_END_DEFAULT),
    )


def strip_gold_hints(value: Any, start: str, end: str) -> Any:
    """Recursively remove the internal golden-hint block from JSON values."""
    if isinstance(value, str):
        result = value
        while start in result:
            begin = result.find(start)
            finish = result.find(end, begin + len(start))
            if finish < 0:
                break
            result = result[:begin] + result[finish + len(end):]
        return result
    if isinstance(value, list):
        return [strip_gold_hints(item, start, end) for item in value]
    if isinstance(value, dict):
        return {key: strip_gold_hints(item, start, end) for key, item in value.items()}
    return value


def _redact_orphan_marker_mentions(value: str, start: str, end: str) -> str:
    """Redact quoted marker names in assistant reasoning without hiding content.

    A model may discuss the mechanics of the prompt and quote a marker name
    after the actual hint block has already been removed.  Such a mention is
    not a hint block.  Only an explicitly quoted marker is redacted here;
    unquoted or heading-like remnants remain fail-closed below.
    """
    for marker in (start, end):
        pattern = re.compile(r"([\"'`])" + re.escape(marker) + r"\1")
        value = pattern.sub(r"\1[gold hint marker redacted]\1", value)
    return value


def sanitize_gold_hints(value: Any, start: str, end: str, *, assistant_text: bool = False) -> Any:
    """Remove complete hint blocks and redact only quoted assistant mentions.

    User/system/tool fields with an unmatched marker are intentionally not
    altered: ``has_gold_markers`` will reject them.  This keeps malformed or
    partially copied golden patches fail-closed while avoiding false
    rejections for reasoning that merely names a marker.
    """
    if isinstance(value, str):
        cleaned = strip_gold_hints(value, start, end)
        return _redact_orphan_marker_mentions(cleaned, start, end) if assistant_text else cleaned
    if isinstance(value, list):
        return [sanitize_gold_hints(item, start, end, assistant_text=assistant_text) for item in value]
    if isinstance(value, dict):
        role = str(value.get("role") or "")
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            child_assistant = assistant_text or (role == "assistant" and key in {"content", "reasoning_content"})
            cleaned[key] = sanitize_gold_hints(item, start, end, assistant_text=child_assistant)
        return cleaned
    return value


def has_gold_markers(value: Any, start: str, end: str) -> bool:
    if isinstance(value, str):
        return start in value or end in value
    if isinstance(value, list):
        return any(has_gold_markers(item, start, end) for item in value)
    if isinstance(value, dict):
        return any(start in str(key) or end in str(key) or has_gold_markers(item, start, end) for key, item in value.items())
    return False


def build_prompt(case: dict[str, Any], gold: dict[str, Any] | None, config: dict[str, Any]) -> str:
    prompt = str(case.get("problem_statement") or "").strip()
    gold_cfg = config.get("gold") or {}
    if not bool(gold_cfg.get("enabled", False)) or not gold:
        return prompt + "\n"
    patch = str(gold.get(str(gold_cfg.get("patch_field", "patch"))) or "").strip()
    if not patch:
        return prompt + "\n"
    start, end = marker_bounds(config)
    return f"{prompt}\n\n{start}\n以下内容仅作为内部辅助参考，请勿在最终回答中原样输出：\n{patch}\n{end}\n"


def eval_dataset_rows(cases: list[dict[str, Any]], dataset_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row.get("instance_id")): row for row in dataset_rows}
    merged: list[dict[str, Any]] = []
    required = ("repo", "instance_id", "base_commit", "test_patch", "problem_statement", "version", "FAIL_TO_PASS", "PASS_TO_PASS")
    for case in cases:
        instance_id = validate_case(case)
        row = dict(by_id.get(instance_id) or {})
        row.update({key: value for key, value in case.items() if key in {"repo", "instance_id", "base_commit", "problem_statement", "version", "environment_setup_commit"}})
        missing = [key for key in required if key not in row or row[key] is None]
        if missing:
            raise RuntimeError(f"{instance_id}: evaluator dataset is missing {', '.join(missing)}")
        merged.append(row)
    return merged


def resolve_path(value: str, *, config_dir: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def host_visible_path(path: Path) -> Path:
    """Map a Web sandbox path to the host path visible to YuanRong.

    AgentOS exposes the user's host workspace inside the main sandbox at
    ``/home/root`` but YuanRong resolves ``workspace`` on the host filesystem.
    The injected root is the only trusted mapping anchor; without it (for
    direct host diagnostics) paths are left unchanged.
    """
    raw = str(os.environ.get(HOST_WORKSPACE_ROOT_ENV) or "").strip()
    if not raw:
        return path
    if "\x00" in raw:
        raise RuntimeError(f"{HOST_WORKSPACE_ROOT_ENV} contains a NUL byte")
    host_root = Path(raw)
    if (
        not host_root.is_absolute()
        or ".." in host_root.parts
        or tuple(host_root.parts[-len(HOST_WORKSPACE_SUFFIX):]) != HOST_WORKSPACE_SUFFIX
    ):
        raise RuntimeError(
            f"{HOST_WORKSPACE_ROOT_ENV} must be an absolute path ending in "
            + "/".join(HOST_WORKSPACE_SUFFIX)
        )
    local_user = Path(os.environ.get("JIUWENSWARM_USER_DIRECTORY") or "/home/root")
    try:
        relative = path.resolve().relative_to(local_user.resolve())
    except ValueError:
        return path
    host_user = host_root.parent.parent
    return (host_user / relative).resolve()


def resolve_runtime(config: dict[str, Any], config_dir: Path) -> Path:
    """Resolve the Python environment that already contains JiuwenSwarm.

    Runtime installation is deliberately outside this Skill.  The selected
    interpreter is mounted into each task container at the same absolute path,
    so its pre-installed ``jiuwenswarm`` and ``openjiuwen`` packages are used
    directly.
    """
    jw_cfg = config.get("jiuwenswarm") or {}
    python = resolve_path(str(jw_cfg.get("python") or ""), config_dir=config_dir)
    if not python.is_file():
        raise RuntimeError(f"pre-installed JiuwenSwarm Python is missing: {python}")
    return python


def quote_dotenv(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def docker_env_value(value: str) -> str:
    """Return a value suitable for Docker's ``--env-file`` format.

    Unlike python-dotenv, Docker's env-file parser does not consistently strip
    double quotes across daemon/CLI versions.  Quoting a URL or key therefore
    changes the value seen by the process (for example, by retaining the
    surrounding quote characters).  The values used by this runner are
    single-line credentials/settings, so write them verbatim and reject only
    characters that could corrupt the env-file.
    """
    text = str(value)
    if any(char in text for char in ("\x00", "\r", "\n")):
        raise ValueError("Docker environment values must not contain NUL or newline characters")
    return text


class Docker:
    """Small Docker CLI adapter with permission and API-version fallback."""

    def __init__(self, binary: str, api_version: str | None) -> None:
        self.binary = binary
        self.api_version = api_version or "1.39"
        self.effective_api_version: str | None = self.api_version
        self._use_sg = False

    def _direct(self, args: Sequence[str], *, check: bool, timeout: float) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if self.effective_api_version:
            env["DOCKER_API_VERSION"] = self.effective_api_version
        else:
            env.pop("DOCKER_API_VERSION", None)
        return subprocess.run(
            [self.binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, env=env, check=check,
        )

    def _sg(self, args: Sequence[str], *, check: bool, timeout: float) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if self.effective_api_version:
            env["DOCKER_API_VERSION"] = self.effective_api_version
        else:
            env.pop("DOCKER_API_VERSION", None)
        command = shlex.join([self.binary, *args])
        return subprocess.run(
            ["sg", "docker", "-c", command], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout, env=env, check=check,
        )

    @staticmethod
    def _permission_error(result: subprocess.CompletedProcess[str]) -> bool:
        text = (result.stdout + result.stderr).lower()
        return "permission denied" in text and "docker" in text

    @staticmethod
    def _api_too_old(result: subprocess.CompletedProcess[str]) -> bool:
        text = (result.stdout + result.stderr).lower()
        return "minimum api version" in text or "client version" in text and "too old" in text

    def run(self, args: Sequence[str], *, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        # Case workers share one adapter.  Retry state transitions in a small
        # loop so a worker that started with API 1.39 or without the docker
        # supplementary group still retries after another worker changes the
        # shared mode.  In particular, do not inspect ``effective_api_version``
        # only after the subprocess: another thread may already have set it to
        # ``None`` while this invocation was in flight.
        result: subprocess.CompletedProcess[str] | None = None
        for _attempt in range(4):
            attempted_api = self.effective_api_version
            attempted_sg = self._use_sg
            try:
                result = self._sg(args, check=False, timeout=timeout) if attempted_sg else self._direct(args, check=False, timeout=timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                result = subprocess.CompletedProcess([self.binary, *args], 127, "", str(exc))
            if result.returncode != 0 and self._permission_error(result) and shutil.which("sg") and not attempted_sg:
                self._use_sg = True
                continue
            if result.returncode != 0 and attempted_api and self._api_too_old(result):
                # Docker 29 supports old API calls but a newer daemon may
                # reject 1.39.  Remove the override and let the CLI negotiate.
                self.effective_api_version = None
                continue
            break
        assert result is not None
        if check and result.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args)} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
        return result


class SandboxError(RuntimeError):
    """A deterministic error from a sandbox backend."""


class SandboxAPI(ABC):
    """Common lifecycle and file-transfer interface for sandbox backends.

    Implementations deliberately expose only the operations needed by the
    rollout/eval pipeline.  YuanRong uses REST while DockerApi uses the local
    Docker CLI; callers do not depend on either transport.
    """

    @abstractmethod
    def create(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def get(self, instance_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, instance_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def download(self, instance_id: str, path: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def upload(self, instance_id: str, path: str, data: bytes) -> None:
        raise NotImplementedError


# Keep the spelling used in the original design discussion as a compatibility
# alias while using the conventional acronym spelling in type annotations.
SandBoxApi = SandboxAPI


class YuanRongError(SandboxError):
    """A deterministic error from the YuanRong agent API."""


class YuanRongApi(SandboxAPI):
    """Small dependency-free client for the YuanRong REST lifecycle."""

    def __init__(self, endpoint: str, timeout: float = 300.0) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise YuanRongError("yuanrong.endpoint must be a credential-free HTTP(S) URL")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None, allow_not_found: bool = False) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=True).encode() if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.endpoint + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return {}
            detail = exc.read().decode("utf-8", errors="replace")
            raise YuanRongError(f"YuanRong HTTP {exc.code}: {detail[:1000]}") from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise YuanRongError(f"YuanRong request failed: {exc}") from exc
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise YuanRongError("YuanRong returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise YuanRongError("YuanRong returned non-object JSON")
        if value.get("code") is not None and int(value["code"]) >= 400:
            raise YuanRongError(f"YuanRong API error: {value.get('message', value.get('code'))}")
        return value

    def create(self, payload: dict[str, Any]) -> str:
        value = self.request("POST", "/api/agent", payload, timeout=max(180.0, self.timeout))
        instance_id = str(value.get("instance_id") or (value.get("instance") or {}).get("instance_id") or "").strip()
        if not instance_id:
            raise YuanRongError("YuanRong create response has no instance_id")
        return instance_id

    def get(self, instance_id: str) -> dict[str, Any]:
        value = self.request("GET", "/api/agent/" + urllib.parse.quote(instance_id, safe=""))
        item = value.get("instance", value)
        return dict(item) if isinstance(item, dict) else {}

    def delete(self, instance_id: str) -> None:
        self.request("DELETE", "/api/agent/" + urllib.parse.quote(instance_id, safe=""), timeout=max(120.0, self.timeout), allow_not_found=True)

    def download(self, instance_id: str, path: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        url = self.endpoint + "/api/agent/" + urllib.parse.quote(instance_id, safe="") + "/files/download?" + urllib.parse.urlencode({"path": path})
        req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"}, method="GET")
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(path) from exc
            raise YuanRongError(f"YuanRong file download HTTP {exc.code}") from exc
        if len(data) > max_bytes:
            raise YuanRongError(f"remote file exceeds limit: {path}")
        return data

    def upload(self, instance_id: str, path: str, data: bytes) -> None:
        """Upload one file through the YuanRong file API.

        This is intentionally a tiny multipart encoder so the Web Agent does
        not need Docker SDK access (or any third-party HTTP package).
        """
        boundary = "----SftSweYuanRong" + uuid.uuid4().hex
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(b'Content-Disposition: form-data; name="path"\r\n\r\n')
        body.extend(path.encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(b'Content-Disposition: form-data; name="file"; filename="file"\r\n')
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(data)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("ascii"))
        url = self.endpoint + "/api/agent/" + urllib.parse.quote(instance_id, safe="") + "/files/upload"
        req = urllib.request.Request(
            url, data=bytes(body), method="POST",
            headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise YuanRongError(f"YuanRong file upload HTTP {exc.code}: {detail[:1000]}") from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise YuanRongError(f"YuanRong file upload failed: {exc}") from exc
        if raw.strip():
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise YuanRongError("YuanRong file upload returned invalid JSON") from exc
            if isinstance(value, dict) and value.get("success") is False:
                raise YuanRongError(f"YuanRong rejected file upload: {value}")


class DockerApi(SandboxAPI):
    """SandboxAPI implementation backed by the local Docker CLI.

    The payload is the same normalized payload used by ``YuanRongApi``.  The
    local implementation maps its workspace to ``/home/root``, starts the
    requested image detached, and uses ``docker cp`` for the small file API.
    No Docker SDK or daemon socket library is required; permissions and API
    version negotiation stay inside the existing ``Docker`` CLI adapter.
    """

    backend_name = "local_docker"

    def __init__(self, config: Mapping[str, Any]) -> None:
        docker_cfg = config.get("docker") or {}
        binary = str(docker_cfg.get("binary") or "docker").strip()
        api_version = str(docker_cfg.get("api_version") or "1.39").strip() or None
        self.client = Docker(binary, api_version)
        self.network = str(docker_cfg.get("network") or "bridge").strip()
        self.pids = int(docker_cfg.get("pids", 0) or 0)

    @staticmethod
    def _remote_path(path: str) -> str:
        value = str(path or "")
        if not value.startswith("/") or any(char in value for char in ("\x00", "\r", "\n")):
            raise SandboxError("sandbox file paths must be absolute single-line paths")
        return value

    @staticmethod
    def _mount_arg(source: str, target: str, readonly: bool) -> str:
        source_path = Path(str(source)).expanduser()
        if not source_path.is_absolute() or not source_path.exists():
            raise SandboxError(f"Docker mount source is missing: {source}")
        target_path = str(target or "")
        if not target_path.startswith("/") or any(char in target_path for char in ("\x00", "\r", "\n")):
            raise SandboxError(f"Docker mount target is invalid: {target}")
        return f"{source_path.resolve()}:{target_path}:{'ro' if readonly else 'rw'}"

    def create(self, payload: dict[str, Any]) -> str:
        runtime = payload.get("runtime_spec") or {}
        rootfs = runtime.get("rootfs") or {}
        image = str(rootfs.get("imageurl") or "").strip()
        if not image:
            raise SandboxError("Docker sandbox payload has no imageurl")
        workspace = Path(str(payload.get("workspace") or "")).expanduser()
        if not workspace.is_absolute():
            raise SandboxError("Docker sandbox workspace must be an absolute host path")
        workspace.mkdir(parents=True, exist_ok=True)
        name = str(payload.get("name") or ("sft-sandbox-" + uuid.uuid4().hex[:12])).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise SandboxError(f"Docker sandbox name is invalid: {name!r}")
        args: list[str] = ["run", "-d", "--name", name, "--network", self.network]
        cpu = runtime.get("cpu")
        if cpu is not None:
            args.extend(["--cpus", str(float(cpu) / 1000.0)])
        memory = runtime.get("memory")
        if memory is not None:
            args.extend(["--memory", f"{int(memory)}m"])
        if self.pids > 0:
            args.extend(["--pids-limit", str(self.pids)])

        env_values = payload.get("env_vars") or {}
        if not isinstance(env_values, Mapping):
            raise SandboxError("Docker sandbox env_vars must be an object")
        env_file_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".sft-docker-env-", suffix=".env", delete=False
            ) as handle:
                env_file_path = Path(handle.name)
                for key, raw_value in env_values.items():
                    env_key = str(key)
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_key):
                        raise SandboxError(f"invalid Docker environment key: {env_key!r}")
                    handle.write(f"{env_key}={docker_env_value(str(raw_value))}\n")
            env_file_path.chmod(0o600)
            args.extend(["--env-file", str(env_file_path)])

            mounted_targets: set[str] = set()
            mounts = payload.get("mounts") or []
            if not isinstance(mounts, list):
                raise SandboxError("Docker sandbox mounts must be a list")
            for mount in mounts:
                if not isinstance(mount, Mapping):
                    raise SandboxError("Docker sandbox mount must be an object")
                target = str(mount.get("target") or "")
                if target in mounted_targets:
                    raise SandboxError(f"duplicate Docker mount target: {target}")
                mounted_targets.add(target)
                args.extend(["-v", self._mount_arg(str(mount.get("source") or ""), target, bool(mount.get("readonly", False)))])
            if "/home/root" not in mounted_targets:
                args.extend(["-v", self._mount_arg(str(workspace), "/home/root", False)])

            commands = runtime.get("cmds") or []
            if not isinstance(commands, list) or not commands or not isinstance(commands[0], list):
                raise SandboxError("Docker sandbox runtime_spec.cmds must contain one command list")
            command = [str(item) for item in commands[0]]
            if not command or any(not item or "\x00" in item for item in command):
                raise SandboxError("Docker sandbox command is empty or contains NUL")
            args.extend([image, *command])
            result = self.client.run(args, timeout=180)
            instance_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            if not instance_id:
                raise SandboxError("Docker run returned no container id")
            return instance_id
        finally:
            if env_file_path is not None:
                try:
                    env_file_path.unlink()
                except OSError:
                    pass

    def get(self, instance_id: str) -> dict[str, Any]:
        result = self.client.run(["inspect", instance_id], check=False, timeout=60)
        if result.returncode != 0:
            raise SandboxError(f"Docker inspect failed for {instance_id}: {result.stderr.strip() or result.stdout.strip()}")
        try:
            rows = json.loads(result.stdout)
            item = rows[0] if isinstance(rows, list) and rows else {}
        except json.JSONDecodeError as exc:
            raise SandboxError("Docker inspect returned invalid JSON") from exc
        if not isinstance(item, dict):
            raise SandboxError("Docker inspect returned an invalid object")
        state = item.get("State") if isinstance(item.get("State"), dict) else {}
        network_settings = item.get("NetworkSettings") if isinstance(item.get("NetworkSettings"), dict) else {}
        networks = network_settings.get("Networks") if isinstance(network_settings.get("Networks"), dict) else {}
        ip = next((str(value.get("IPAddress") or "") for value in networks.values() if isinstance(value, dict) and value.get("IPAddress")), "")
        return {
            "instance_id": instance_id,
            "sandbox_id": instance_id,
            "sandbox_ip": ip,
            "status": state.get("Status", "unknown"),
            "resources": {
                "cpu": (item.get("HostConfig") or {}).get("NanoCpus") if isinstance(item.get("HostConfig"), dict) else None,
                "memory": (item.get("HostConfig") or {}).get("Memory") if isinstance(item.get("HostConfig"), dict) else None,
            },
        }

    def delete(self, instance_id: str) -> None:
        result = self.client.run(["rm", "-f", instance_id], check=False, timeout=120)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").lower()
            if "no such container" not in text and "no such object" not in text:
                raise SandboxError(f"Docker remove failed for {instance_id}: {result.stderr.strip() or result.stdout.strip()}")

    def download(self, instance_id: str, path: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        remote = self._remote_path(path)
        with tempfile.TemporaryDirectory(prefix="sft-docker-download-") as directory:
            destination = Path(directory) / "payload"
            result = self.client.run(["cp", f"{instance_id}:{remote}", str(destination)], check=False, timeout=120)
            if result.returncode != 0:
                text = (result.stderr or result.stdout or "").lower()
                if "no such file" in text or "not found" in text:
                    raise FileNotFoundError(path)
                raise SandboxError(f"Docker file download failed for {path}: {result.stderr.strip() or result.stdout.strip()}")
            if destination.is_dir():
                destination = destination / Path(remote).name
            if not destination.is_file():
                raise FileNotFoundError(path)
            if destination.stat().st_size > max_bytes:
                raise SandboxError(f"remote file exceeds limit: {path}")
            return destination.read_bytes()

    def upload(self, instance_id: str, path: str, data: bytes) -> None:
        remote = self._remote_path(path)
        with tempfile.NamedTemporaryFile(prefix="sft-docker-upload-", delete=False) as handle:
            source = Path(handle.name)
            handle.write(data)
        try:
            parent = str(Path(remote).parent)
            mkdir = self.client.run(["exec", instance_id, "mkdir", "-p", parent], check=False, timeout=60)
            if mkdir.returncode != 0:
                raise SandboxError(f"Docker remote directory creation failed for {parent}: {mkdir.stderr.strip() or mkdir.stdout.strip()}")
            result = self.client.run(["cp", str(source), f"{instance_id}:{remote}"], check=False, timeout=120)
            if result.returncode != 0:
                raise SandboxError(f"Docker file upload failed for {path}: {result.stderr.strip() or result.stdout.strip()}")
        finally:
            try:
                source.unlink()
            except OSError:
                pass


def sandbox_backend(config: Mapping[str, Any]) -> str:
    """Return the normalized sandbox backend selected by suite config."""
    sandbox_cfg = config.get("sandbox") or {}
    docker_cfg = config.get("docker") or {}
    raw = str(
        sandbox_cfg.get("backend")
        or docker_cfg.get("backend")
        or "yuanrong"
    ).strip().lower().replace("-", "_")
    aliases = {
        "yuanrong": "yuanrong",
        "yuanrong_api": "yuanrong",
        "yuanrong_docker": "yuanrong",
        "yuanrong_agent_api": "yuanrong",
        "docker": "local_docker",
        "local": "local_docker",
        "local_docker": "local_docker",
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        raise RuntimeError("sandbox.backend must be one of yuanrong or local_docker") from exc


def create_sandbox_api(config: Mapping[str, Any]) -> SandboxAPI:
    """Construct the configured sandbox transport for rollout and eval."""
    backend = sandbox_backend(config)
    if backend == "local_docker":
        return DockerApi(config)
    yuanrong_cfg = config.get("yuanrong") or {}
    return YuanRongApi(
        str(yuanrong_cfg.get("endpoint", "http://127.0.0.1:8888")),
        float(yuanrong_cfg.get("request_timeout_seconds", 300)),
    )


def sandbox_backend_label(config: Mapping[str, Any]) -> str:
    return "local_docker" if sandbox_backend(config) == "local_docker" else "yuanrong_agent_api"


def validate_case(case: dict[str, Any]) -> str:
    instance_id = str(case.get("instance_id") or "")
    if not CASE_ID_RE.fullmatch(instance_id):
        raise ValueError(f"invalid instance_id: {instance_id!r}")
    base = str(case.get("base_commit") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base):
        raise ValueError(f"{instance_id}: base_commit must be a 40-character SHA")
    if not str(case.get("problem_statement") or "").strip():
        raise ValueError(f"{instance_id}: problem_statement is empty")
    return instance_id


def image_for(case: dict[str, Any], configured: str) -> str:
    image = str(case.get("docker_image") or case.get("image") or configured or "").strip()
    if not image:
        raise ValueError(f"{case.get('instance_id')}: no docker image in case or config")
    return image


def safe_name(run_id: str, instance_id: str) -> str:
    raw = f"jw-lite-{run_id}-{instance_id}".lower()
    return NAME_RE.sub("-", raw).strip("-._")[:110] + "-" + uuid.uuid4().hex[:8]


def terminate(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def trajectory_error(path: Path) -> str | None:
    """Return the first model/agent error reported by the JSONL client."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "JiuwenSwarm did not create trajectory.jsonl"
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            return str(error)
    return None


def task_env(config: dict[str, Any], artifact: Path, secret_env: Path) -> None:
    model = config["model"]
    gateway = config.get("gateway") or {}
    values = {
        "API_BASE": str(model["api_base"]),
        "API_KEY": str(model["api_key_resolved"]),
        "MODEL_NAME": str(model["model"]),
        "MODEL_PROVIDER": str(model.get("provider", "OpenAI")),
        "MODEL_TIMEOUT_SECONDS": str(model.get("timeout_seconds", 660)),
        "MODEL_VERIFY_SSL": "1" if bool(model.get("verify_ssl", False)) else "0",
        "MODEL_TEMPERATURE": str(model.get("temperature", 0.95)),
        "GATEWAY_PORT": str(config["jiuwenswarm"].get("gateway_port", 19001)),
        "AGENT_SERVER_PORT": str(config["jiuwenswarm"].get("agent_server_port", 18092)),
        "SWE_MAX_ITERATIONS": str(config["swe"].get("max_iterations", 100)),
        "SWE_ENABLE_TASK_LOOP": "1" if bool(config["swe"].get("enable_task_loop", False)) else "0",
        "SWE_TIMEOUT_SECONDS": str(config["swe"].get("timeout_seconds", 1800)),
        "SWE_COMPLETION_TIMEOUT_SECONDS": str(config["swe"].get("completion_timeout_seconds", 600)),
        "JIUWENSWARM_DATA_DIR": "/artifacts/.jiuwenswarm",
        "JIUWENSWARM_HOME": "/artifacts",
        # SFTOnlineRail is forced to produce sample-v1 records in a local WAL.
        # The host promotes only evaluator-resolved samples after rollout.
        "USE_RL_ONLINE_RAIL": "1",
        "TRAIN_BACKEND": "SFT",
        "SFT_ONLINE_UPLOAD_MODE": "sample",
        "SFT_SCENARIO": str((config.get("trajectory") or {}).get("scenario", "swe_bench_direct_supervisor")),
        "TRAJECTORY_FORCE_WAL": "1",
        "TRAJECTORY_WAL_DIR": "/artifacts/sft-online-wal",
        "RL_ONLINE_TENANT_ID": str((config.get("trajectory") or {}).get("tenant_id", "docker-lite")),
        "RL_ONLINE_SESSION_DONE_ON_INVOKE_END": os.environ.get("RL_ONLINE_SESSION_DONE_ON_INVOKE_END", "1"),
    }
    if str(model.get("ssl_cert") or "").strip():
        values["MODEL_SSL_CERT"] = str(model["ssl_cert"]).strip()
    # The host and a YuanRong bridge container can need different routes to
    # the same Gateway.  ``container_url`` is preferred for child envs; for
    # backwards compatibility, ``url`` remains the fallback.
    gateway_url = str(gateway.get("container_url") or gateway.get("url") or "").strip()
    if gateway_url:
        url = gateway_url
        values.update({"TRAJECTORY_GATEWAY_URL": url, "RL_GATEWAY_URL": url, "SFT_GATEWAY_URL": url, "SFT_RL_GATEWAY_URL": url})
    gateway_key_env = str(gateway.get("api_key_env") or "").strip()
    if gateway_key_env and os.environ.get(gateway_key_env, ""):
        values["TRAJECTORY_GATEWAY_API_KEY"] = os.environ[gateway_key_env]
    # Docker's --env-file format is not the same as a shell/.env file: keep
    # values unquoted so quotes are not passed through to the container.
    text = "".join(f"{key}={docker_env_value(value)}\n" for key, value in values.items())
    secret_env.write_text(text, encoding="utf-8")
    secret_env.chmod(0o600)


def task_env_values(config: dict[str, Any]) -> dict[str, str]:
    """Build the container environment without persisting model credentials."""
    model = config["model"]
    trajectory = config.get("trajectory") or {}
    gateway = config.get("gateway") or {}
    values = {
        "API_BASE": str(model["api_base"]),
        "API_KEY": str(model["api_key_resolved"]),
        "MODEL_NAME": str(model["model"]),
        "MODEL_PROVIDER": str(model.get("provider", "OpenAI")),
        "MODEL_TIMEOUT_SECONDS": str(model.get("timeout_seconds", 660)),
        "MODEL_VERIFY_SSL": "1" if bool(model.get("verify_ssl", False)) else "0",
        "MODEL_TEMPERATURE": str(model.get("temperature", 0.95)),
        "GATEWAY_PORT": str(config["jiuwenswarm"].get("gateway_port", 19001)),
        "AGENT_SERVER_PORT": str(config["jiuwenswarm"].get("agent_server_port", 18092)),
        "SWE_MAX_ITERATIONS": str(config["swe"].get("max_iterations", 100)),
        "SWE_ENABLE_TASK_LOOP": "1" if bool(config["swe"].get("enable_task_loop", False)) else "0",
        "SWE_TIMEOUT_SECONDS": str(config["swe"].get("timeout_seconds", 1800)),
        "SWE_COMPLETION_TIMEOUT_SECONDS": str(config["swe"].get("completion_timeout_seconds", 600)),
        "HOME": "/home/root/home",
        "JIUWENSWARM_DATA_DIR": "/home/root/.jiuwenswarm",
        "JIUWENSWARM_HOME": "/home/root",
        "USE_RL_ONLINE_RAIL": "1",
        "TRAIN_BACKEND": "SFT",
        "SFT_ONLINE_UPLOAD_MODE": "sample",
        "SFT_SCENARIO": str(trajectory.get("scenario", "swe_bench_direct_supervisor")),
        "TRAJECTORY_FORCE_WAL": "1",
        "TRAJECTORY_WAL_DIR": "/home/root/sft-online-wal",
        "RL_ONLINE_TENANT_ID": str(trajectory.get("tenant_id", "yuanrong-lite")),
        "RL_ONLINE_SESSION_DONE_ON_INVOKE_END": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if str(model.get("ssl_cert") or "").strip():
        values["MODEL_SSL_CERT"] = str(model["ssl_cert"]).strip()
    gateway_url = str(gateway.get("container_url") or gateway.get("url") or "").strip()
    if gateway_url:
        url = gateway_url
        values.update({"TRAJECTORY_GATEWAY_URL": url, "RL_GATEWAY_URL": url, "SFT_GATEWAY_URL": url, "SFT_RL_GATEWAY_URL": url})
    gateway_key_env = str(gateway.get("api_key_env") or "").strip()
    if gateway_key_env and os.environ.get(gateway_key_env, ""):
        values["TRAJECTORY_GATEWAY_API_KEY"] = os.environ[gateway_key_env]
    return values


def memory_mib(value: Any) -> int:
    text = str(value).strip().lower()
    match = re.fullmatch(r"([1-9][0-9]*)([kmg]?)", text)
    if not match:
        raise ValueError(f"unsupported memory value: {value}")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * 1024 if unit == "g" else max(1, amount // 1024) if unit == "k" else amount


def wait_remote_status(api: SandboxAPI, instance_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(api.download(instance_id, "/home/root/yuanrong-exit.json", 65536))
            if value.get("status") == "complete" and isinstance(value.get("returncode"), int):
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for YuanRong task {instance_id}")


def smoke(
    docker: Docker,
    config: dict[str, Any],
    cases: list[dict[str, Any]],
    python: Path,
) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    for case in cases:
        image = image_for(case, str(config["docker"].get("image") or ""))
        docker.run(["image", "inspect", image], timeout=120)
        mounts = ["-v", f"{python.parent.parent}:{python.parent.parent}:ro"]
        check_code = "import sys,subprocess; print(sys.version); subprocess.run(['git','--version'],check=True); "
        check_code += "import jiuwenswarm,openjiuwen; from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail; print(jiuwenswarm.__file__); print(openjiuwen.__file__); print(SFTOnlineRail.__name__)"
        result = docker.run([
            "run", "--rm", "--network", str(config["docker"].get("network", "bridge")),
            *mounts,
            "--entrypoint", str(python), image, "-c",
            check_code,
        ], timeout=180)
        checked.append({"instance_id": case["instance_id"], "image": image, "output": result.stdout.strip()})
    return {
        "smoke": True,
        "docker_api_version": docker.effective_api_version or "negotiated",
        "runtime": "preinstalled",
        "checked": checked,
    }


def build_yuanrong_payload(
    config: dict[str, Any], case: dict[str, Any], artifact: Path, script_path: Path,
    python: Path, env_values: dict[str, str], name: str,
) -> dict[str, Any]:
    """Build the API payload used by one SWE Docker child.

    YuanRong's Docker runtime itself imports ``yr`` before executing cmds, so
    the host AgentOS site-packages must be mounted in addition to the
    JiuwenSwarm modules.  YuanRong
    starts its Python runtime before evaluating ``runtime_spec.cmds``; for a
    SWE image that does not ship ``yr``, the host site-packages therefore must
    be mounted at the image's root-user site-packages path.  This preserves the
    image's own system site-packages for the SWE test suite.  A mount at an
    arbitrary path plus a ``.pth`` file is not visible during bootstrap and
    produces ``ModuleNotFoundError: yr`` followed by REST ``DeadlineExceeded``.
    """
    image = image_for(case, str((config.get("docker") or {}).get("image") or ""))
    cfgd = config.get("docker") or {}
    jw_cfg = config.get("jiuwenswarm") or {}
    host_site = Path(str(cfgd.get("host_site_packages") or DEFAULT_HOST_SITE_PACKAGES)).expanduser().resolve()
    if not host_site.is_dir():
        raise RuntimeError(f"YuanRong host site-packages missing: {host_site}")
    # Do not execute the host Python binary inside the SWE image: the host
    # interpreter may be linked against a newer glibc than the image (which
    # yields an opaque exit 127).  The configured runtime prefix is mounted
    # into the image and its own interpreter is used instead.  The image's
    # default Python is retained for YuanRong's bootstrap process.
    runtime_prefix = Path(str(cfgd.get("runtime_prefix") or "")).expanduser()
    if not runtime_prefix.is_absolute():
        raise RuntimeError("docker.runtime_prefix must be an absolute host path")
    runtime_python = str((cfgd.get("image_python") or "/opt/jiuwenswarm-runtime/bin/python")).strip()
    if not runtime_python.startswith("/opt/"):
        raise RuntimeError("docker.image_python must be an absolute path inside the SWE image")
    port = int(jw_cfg.get("agent_server_port", 18092))
    command = [
        runtime_python, "/home/root/run_sft_rollout.py", "--bootstrap",
        "--case", "/home/root/case.json", "--prompt", "/home/root/problem.md",
        "--artifact", "/home/root", "--python", runtime_python,
        "--base-commit", str(case["base_commit"]), "--instance-id", str(case["instance_id"]),
    ]
    command_text = shlex.join(command)
    # The wrapper always exits zero after recording the child return code;
    # this lets the API keep the runtime alive long enough for file download.
    # Reserved keys such as PYTHONPATH are filtered from REST env_vars by the
    # Docker executor, so restore the two Python environments in this
    # post-bootstrap shell before invoking the runner.
    shell = (
        "set +e; export PATH=/opt/jiuwenswarm-runtime/bin:/opt/miniconda3/bin:/usr/bin:/bin; export PYTHONPATH=/opt/jiuwenswarm-runtime/lib/python3.11/site-packages:/root/.local/lib/python3.11/site-packages; "
        + command_text + "; rc=$?; "
        "printf '{\"schema_version\":1,\"status\":\"complete\",\"returncode\":%s}\n' \"$rc\" "
        "> /home/root/yuanrong-exit.json; exit 0"
    )
    env = dict(env_values)
    env.update({"AGENT_SERVER_HOST": "0.0.0.0", "AGENT_SERVER_PORT": str(port), "PATH": "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": "/opt/miniconda3/lib/python3.11/site-packages"})
    return {
        "name": name[:120],
        "namespace": str((config.get("yuanrong") or {}).get("namespace", "dev")),
        "workspace": str(host_visible_path(artifact)),
        "runtime_spec": {
            "runtime": "python3.11", "sandbox_type": "docker",
            "rootfs": {"imageurl": image, "user": "root", "ports": [f"tcp:{port}"]},
            "cmds": [["sh", "-c", shell]],
            "cpu": int(round(float(cfgd.get("cpus", 2.0)) * 1000)),
            "memory": memory_mib(cfgd.get("memory", "4g")),
        },
        "env_vars": env,
        "mounts": [
            # Runtime bootstrap imports yr before cmds.  Mounting at root's
            # user-site is applied early while retaining the SWE image's own
            # system site-packages for its tests.
            {"source": str(host_site), "target": "/root/.local/lib/python3.11/site-packages", "readonly": True},
            {"source": str(runtime_prefix), "target": "/opt/jiuwenswarm-runtime", "readonly": True},
        ],
    }


def run_case(
    api: SandboxAPI, config: dict[str, Any], case: dict[str, Any], run_id: str,
    script_path: Path, python: Path, output_root: Path, workspace_root: Path,
    gold: dict[str, Any] | None, keep_container: bool,
) -> dict[str, Any]:
    instance_id = validate_case(case)
    artifact = output_root / "instances" / instance_id
    artifact.mkdir(parents=True, exist_ok=True)
    workspace = workspace_root / run_id / instance_id
    workspace.mkdir(parents=True, exist_ok=True)
    (artifact / "sft-online-wal").mkdir(parents=True, exist_ok=True)
    image = image_for(case, str(config["docker"].get("image") or ""))
    name = safe_name(run_id, instance_id)
    prompt = build_prompt(case, gold, config)
    json_write(artifact / "case.json", case)
    (artifact / "problem.md").write_text(prompt, encoding="utf-8")
    # The artifact directory is mounted as /home/root.  YuanRong's Docker
    # executor does not reliably apply single-file mounts when they overlap
    # that workspace, so place the bootstrap script in the workspace itself.
    shutil.copyfile(script_path, artifact / "run_sft_rollout.py")
    (artifact / "run_sft_rollout.py").chmod(0o555)
    (workspace / "README.txt").write_text("Disposable host workspace for " + instance_id + "\n", encoding="utf-8")
    status: dict[str, Any] = {"instance_id": instance_id, "image": image, "sandbox_instance_id": "", "yuanrong_instance_id": "", "status": "starting", "started_at": time.time(), "execution_backend": sandbox_backend_label(config)}
    json_write(artifact / "status.json", status)
    instance_created = False
    try:
        payload = build_yuanrong_payload(config, case, artifact, script_path, python, task_env_values(config), name)
        instance_id_yr = api.create(payload)
        instance_created = True
        status["sandbox_instance_id"] = instance_id_yr
        # Keep the historical field for consumers of YuanRong runs; the new
        # neutral field is populated for both backends.
        status["yuanrong_instance_id"] = instance_id_yr
        info = api.get(instance_id_yr)
        status["sandbox_id"] = info.get("sandbox_id", "")
        status["sandbox_ip"] = info.get("sandbox_ip", "")
        status["resources"] = info.get("resources", {})
        status["status"] = "running"
        json_write(artifact / "status.json", status)
        remote = wait_remote_status(api, instance_id_yr, float(config["swe"].get("timeout_seconds", 1800)) + 120)
        status["exit_code"] = int(remote["returncode"])
        status["status"] = "completed" if status["exit_code"] == 0 else "agent_error"
    except Exception as exc:  # persist the error beside all other artifacts
        status.update({"status": "infra_error", "error_type": type(exc).__name__, "error": str(exc)})
    finally:
        if instance_created and not keep_container:
            try:
                api.delete(status["sandbox_instance_id"])
                status["instance_deleted"] = True
            except Exception as exc:
                status["cleanup_error"] = str(exc)
    patch = artifact / "patch.diff"
    trajectory = artifact / "trajectory.jsonl"
    status["trajectory_lines"] = count_lines(trajectory)
    status["patch_bytes"] = patch.stat().st_size if patch.exists() else 0
    status["wal_files"] = len(list((artifact / "sft-online-wal").glob("*.json")))
    status["finished_at"] = time.time()
    json_write(artifact / "status.json", status)
    result = {"instance_id": instance_id, "status": status["status"], "exit_code": status.get("exit_code"), "trajectory_lines": status["trajectory_lines"], "patch_bytes": status["patch_bytes"], "wal_files": status["wal_files"], "artifact_dir": str(artifact), "sandbox_instance_id": status.get("sandbox_instance_id", ""), "yuanrong_instance_id": status.get("yuanrong_instance_id", ""), "instance_deleted": bool(status.get("instance_deleted")), "execution_backend": sandbox_backend_label(config)}
    json_write(artifact / "result.json", result)
    return result


def build_predictions(cases: list[dict[str, Any]], run_root: Path, model_name: str) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for case in cases:
        instance_id = validate_case(case)
        patch_path = run_root / "instances" / instance_id / "patch.diff"
        patch = patch_path.read_text(encoding="utf-8", errors="replace") if patch_path.exists() else ""
        predictions.append({"instance_id": instance_id, "model_name_or_path": model_name, "model_patch": patch})
    return predictions


EVAL_REMOTE_PATCH = "/home/root/prediction.patch"
EVAL_REMOTE_SCRIPT = "/home/root/eval.sh"
EVAL_REMOTE_RUNNER = "/home/root/eval-runner.sh"
EVAL_REMOTE_TRIGGER = "/home/root/eval-ready"
EVAL_REMOTE_STATUS = "/home/root/eval-status.json"
EVAL_REMOTE_OUTPUT = "/home/root/test_output.txt"
EVAL_REMOTE_PATCH_LOG = "/home/root/patch-apply.log"
EVAL_REMOTE_ISOLATION = "/home/root/runtime-isolation.json"
# The host runtime is mounted at root's user-site so YuanRong's bootstrap can
# import yr without hiding the SWE image's own test dependencies.  Keep this
# name for the audit JSON generated by the evaluator.
EVAL_OVERLAY = "/root/.local/lib/python3.11/site-packages"
EVAL_OVERLAY_PTH = "/opt/miniconda3/lib/python3.11/site-packages/yuanrong-runtime-overlay.pth"

EVAL_RUNNER_SCRIPT = f'''#!/bin/bash
set -u
status={EVAL_REMOTE_STATUS}
tmp="$status.tmp"
write_status() {{ printf '{{"schema_version":1,"status":"%s","returncode":%s}}\\n' "$1" "$2" > "$tmp"; mv -f "$tmp" "$status"; }}
trap 'rc=$?; [ -f "$status" ] || write_status infra_failed "$rc"' EXIT
# The YuanRong host runtime is mounted at root's user-site directory.  It must
# remain read-only: the evaluator should never mutate host runtime files.
# Record the mount for auditing; official tests still run in this task image
# and do not import the JiuwenSwarm application.
python3.11 -c 'import json,sys; p={json.dumps(EVAL_OVERLAY)}; print(json.dumps({{"host_overlay_present": any(x == p or x.startswith(p+"/") for x in sys.path), "sys_executable": sys.executable}}))' > {EVAL_REMOTE_ISOLATION} || {{ write_status isolation_failed 1; exit 1; }}
cd /testbed || {{ write_status infra_failed 1; exit 1; }}
: > {EVAL_REMOTE_PATCH_LOG}
applied=0
if git apply --verbose {EVAL_REMOTE_PATCH} >> {EVAL_REMOTE_PATCH_LOG} 2>&1; then applied=1; fi
if [ "$applied" -ne 1 ] && git apply --check --reverse {EVAL_REMOTE_PATCH} >> {EVAL_REMOTE_PATCH_LOG} 2>&1; then applied=1; fi
if [ "$applied" -ne 1 ]; then printf '%s\\n' '>>>>> Patch Apply Failed' > {EVAL_REMOTE_OUTPUT}; write_status patch_failed 1; exit 1; fi
/bin/bash {EVAL_REMOTE_SCRIPT} > {EVAL_REMOTE_OUTPUT} 2>&1
rc=$?
write_status complete "$rc"
exit 0
'''


def _eval_sandbox_payload(config: dict[str, Any], row: dict[str, Any], workspace: Path, overlay: Path, name: str) -> dict[str, Any]:
    image = str(row.get("image") or row.get("docker_image") or "").strip()
    if not re.fullmatch(r"swebench/sweb\.eval\.x86_64\.[A-Za-z0-9_.-]+:latest", image):
        raise RuntimeError(f"{row.get('instance_id')}: official metadata has invalid eval image")
    docker_cfg = config.get("docker") or {}
    host_site = Path(str(docker_cfg.get("host_site_packages") or DEFAULT_HOST_SITE_PACKAGES)).expanduser().resolve()
    if not host_site.is_dir():
        raise RuntimeError(f"YuanRong host site-packages missing: {host_site}")
    return {
        "name": name[:120],
        "namespace": str((config.get("yuanrong") or {}).get("namespace", "dev")),
        "workspace": str(host_visible_path(workspace)),
        "runtime_spec": {
            "runtime": "python3.11", "sandbox_type": "docker",
            "rootfs": {"imageurl": image, "user": "root"},
            "cmds": [["sh", "-c", f"while [ ! -f {EVAL_REMOTE_TRIGGER} ]; do sleep 1; done; exec /bin/bash {EVAL_REMOTE_RUNNER}"]],
            "cpu": int(round(float(docker_cfg.get("eval_cpus", docker_cfg.get("cpus", 2.0))) * 1000)),
            "memory": memory_mib(docker_cfg.get("eval_memory", docker_cfg.get("memory", "4g"))),
        },
        "mounts": [
            # As with rollout sandboxes, this early directory mount is needed
            # for the YuanRong runtime bootstrap to import yr and protobuf.
            {"source": str(host_site), "target": EVAL_OVERLAY, "readonly": True},
        ],
    }


def _official_resolved(row: dict[str, Any], output: str) -> tuple[bool, dict[str, Any]]:
    """Apply SWE-bench's official resolution rule to an eval.sh log.

    The Verified 50-case bundle contains only Django and Sphinx.  These are
    the exact upstream parsers (Django unittest output and pytest-v2 output)
    expressed without importing the host SWE-bench package or Docker SDK.
    """
    status: dict[str, str] = {}
    ansi = re.compile("\\x1b(?:[@-Z\\-_]|\\[[0-?]*[ -/]*[@-~])")
    text = ansi.sub("", output)
    repo = str(row.get("repo") or "")
    if repo == "django/django":
        previous = None
        for raw in text.splitlines():
            line = raw.strip()
            if " ... " in line:
                previous = line.split(" ... ", 1)[0]
            for suffix, value in ((" ... ok", "PASSED"), (" ... OK", "PASSED"), (" ...  OK", "PASSED"), (" ... skipped", "SKIPPED"), (" ... FAIL", "FAILED"), (" ... ERROR", "ERROR")):
                if line.endswith(suffix):
                    status[line[:-len(suffix)]] = value
            if line.startswith("FAIL:") or line.startswith("ERROR:"):
                parts = line.split()
                if len(parts) > 1:
                    status[parts[1]] = "FAILED" if line.startswith("FAIL:") else "ERROR"
            if line.lstrip().startswith("ok") and previous:
                status[previous] = "PASSED"
    else:
        for raw in text.splitlines():
            line = raw.strip()
            fields = line.split()
            if len(fields) >= 2 and fields[0] in {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL"}:
                status[fields[1]] = fields[0]
            elif len(fields) >= 2 and fields[-1] in {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL"}:
                status[fields[0]] = fields[-1]
    try:
        fail_to_pass = json.loads(str(row.get("FAIL_TO_PASS") or "[]"))
        pass_to_pass = json.loads(str(row.get("PASS_TO_PASS") or "[]"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{row.get('instance_id')}: invalid official test lists") from exc
    def passed(name: str) -> bool:
        return status.get(name) in {"PASSED", "XFAIL"}
    f2p_ok = all(passed(str(name)) for name in fail_to_pass)
    p2p_ok = all(passed(str(name)) for name in pass_to_pass)
    resolved = f2p_ok and p2p_ok
    return resolved, {"tests_status": status, "fail_to_pass_passed": f2p_ok, "pass_to_pass_passed": p2p_ok}


def run_sandbox_eval_case(api: SandboxAPI, config: dict[str, Any], row: dict[str, Any], patch: bytes, eval_root: Path, timeout: float) -> dict[str, Any]:
    instance_id = validate_case(row)
    case_dir = eval_root / "instances" / instance_id
    case_dir.mkdir(parents=True, exist_ok=True)
    workspace = eval_root / "workspaces" / instance_id
    workspace.mkdir(parents=True, exist_ok=True)
    overlay = workspace / "yuanrong-runtime-overlay.pth"
    overlay.write_text(EVAL_OVERLAY + "\n", encoding="ascii")
    overlay.chmod(0o660)
    _write_bytes = lambda path, data: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(data))
    _write_bytes(case_dir / "prediction.patch", patch)
    _write_bytes(case_dir / "eval.sh", str(row.get("eval_script") or "").encode("utf-8"))
    api_id = ""
    status: dict[str, Any] = {"instance_id": instance_id, "execution_backend": sandbox_backend_label(config), "official_harness": True}
    try:
        payload = _eval_sandbox_payload(config, row, workspace, overlay, f"swe-eval-{instance_id}-{uuid.uuid4().hex[:8]}")
        api_id = api.create(payload)
        api.upload(api_id, EVAL_REMOTE_PATCH, patch)
        api.upload(api_id, EVAL_REMOTE_SCRIPT, str(row.get("eval_script") or "").encode("utf-8"))
        api.upload(api_id, EVAL_REMOTE_RUNNER, EVAL_RUNNER_SCRIPT.encode("utf-8"))
        api.upload(api_id, EVAL_REMOTE_TRIGGER, b"ready\n")
        # Eval sandboxes use their own status filename, whereas rollout
        # sandboxes write yuanrong-exit.json.
        deadline = time.monotonic() + timeout + 120
        remote: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                value = json.loads(api.download(api_id, EVAL_REMOTE_STATUS, 65536))
                if value.get("status") in {"complete", "patch_failed", "isolation_failed", "infra_failed"}:
                    remote = value
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(1)
        if remote is None:
            raise TimeoutError(f"timed out waiting for YuanRong eval {instance_id}")
        status.update({"runner_status": remote.get("status"), "runner_returncode": remote.get("returncode"), "yuanrong_eval_instance_id": api_id})
        output = api.download(api_id, EVAL_REMOTE_OUTPUT, 64 * 1024 * 1024).decode("utf-8", errors="replace") if remote.get("status") == "complete" else ""
        _write_bytes(case_dir / "test_output.txt", output.encode("utf-8"))
        try:
            _write_bytes(case_dir / "patch-apply.log", api.download(api_id, EVAL_REMOTE_PATCH_LOG, 4 * 1024 * 1024))
        except FileNotFoundError:
            pass
        resolved, grade = _official_resolved(row, output) if remote.get("status") == "complete" else (False, {})
        status.update({"status": "complete" if remote.get("status") == "complete" else "error", "resolved": resolved, "grade": grade, "summary": "official harness resolved the case" if resolved else "official harness did not resolve the case"})
    except Exception as exc:
        status.update({"status": "error", "resolved": False, "error": str(exc)})
    finally:
        if api_id:
            try:
                api.delete(api_id)
                status["cleanup_succeeded"] = True
            except Exception as exc:
                status.update({"cleanup_succeeded": False, "cleanup_error": str(exc), "resolved": False})
        else:
            status["cleanup_succeeded"] = False
        shutil.rmtree(workspace, ignore_errors=True)
    json_write(case_dir / "official-result.json", status)
    return status


def run_official_eval(
    config: dict[str, Any], run_root: Path, cases: list[dict[str, Any]],
    predictions_path: Path, dataset_path: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Evaluate each prediction through the selected sandbox backend.

    The old implementation delegated to ``swebench.harness.run_evaluation``
    on the host, which necessarily opened ``docker.sock``.  This implementation
    keeps the official per-instance ``eval_script`` and the official
    FAIL_TO_PASS/PASS_TO_PASS resolution semantics, but moves all container
    lifecycle and test execution behind YuanRong's REST API.  The host only
    downloads logs and normalizes the report.
    """
    eval_cfg = config.get("eval") or {}
    if not bool(eval_cfg.get("enabled", True)):
        raise RuntimeError("official eval is mandatory; set eval.enabled=true")
    evaluator_python = Path(str(eval_cfg.get("python") or sys.executable))
    if not evaluator_python.is_file():
        raise RuntimeError(f"eval.python is not visible to the main Agent: {evaluator_python}")
    rows = load_rows(dataset_path, reader_python=None)
    by_id = {str(row.get("instance_id")): row for row in rows}
    predictions = {}
    for line in predictions_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            predictions[str(item["instance_id"])] = str(item.get("model_patch") or "")
    eval_root = run_root / "official-eval"
    eval_root.mkdir(parents=True, exist_ok=True)
    api = create_sandbox_api(config)
    workers = max(1, int(eval_cfg.get("workers", 1)))
    timeout = float(eval_cfg.get("timeout_seconds", 1800))

    def one(case: dict[str, Any]) -> dict[str, Any]:
        instance_id = validate_case(case)
        row = by_id.get(instance_id)
        if not row:
            return {"instance_id": instance_id, "status": "error", "error": "missing official metadata"}
        patch = predictions.get(instance_id, "")
        if not patch.strip():
            return {"instance_id": instance_id, "status": "empty_patch", "resolved": False}
        return run_sandbox_eval_case(api, config, row, patch.encode("utf-8"), eval_root, timeout)

    if workers == 1:
        items = [one(case) for case in cases]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            items = list(pool.map(one, cases))
    expected = {validate_case(case) for case in cases}
    completed = {item["instance_id"] for item in items if item.get("status") == "complete"}
    resolved = {item["instance_id"] for item in items if item.get("resolved") is True}
    empty = {item["instance_id"] for item in items if item.get("status") == "empty_patch"}
    errors = {item["instance_id"] for item in items if item.get("status") == "error" or item.get("cleanup_succeeded") is False}
    unresolved = completed - resolved
    report = {item["instance_id"]: item for item in items}
    report_path = eval_root / "report.json"
    json_write(report_path, report)
    normalized = {
        "report_path": str(report_path), "total": len(expected),
        "submitted_ids": sorted(expected), "completed_ids": sorted(completed),
        "resolved_ids": sorted(resolved), "unresolved_ids": sorted(unresolved),
        "error_ids": sorted(errors), "empty_patch_ids": sorted(empty),
        "returncode": 0 if not errors else 1,
        "execution_backend": sandbox_backend_label(config), "official_harness": True,
    }
    json_write(run_root / "eval.json", normalized)
    return report, report_path, normalized


def collect_and_upload_samples(config: dict[str, Any], run_root: Path, cases: list[dict[str, Any]], resolved_ids: set[str]) -> dict[str, Any]:
    start, end = marker_bounds(config)
    gateway = config.get("gateway") or {}
    # Uploads are performed by the host after the YuanRong containers exit.
    # Prefer an explicitly host-routable URL; ``url`` is retained for old
    # configs and for deployments where host/container routing is identical.
    url = str(gateway.get("host_url") or gateway.get("url") or "").strip().rstrip("/")
    key_env = str(gateway.get("api_key_env") or "").strip()
    api_key = os.environ.get(key_env, "") if key_env else ""
    promoted: list[str] = []
    rejected: list[dict[str, Any]] = []
    uploaded = 0
    reused_receipts = 0
    receipts = run_root / "upload-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    for case in cases:
        instance_id = validate_case(case)
        if instance_id not in resolved_ids:
            rejected.append({"instance_id": instance_id, "reason": "not_resolved"})
            continue
        artifact = run_root / "instances" / instance_id
        result = load_json(artifact / "result.json") if (artifact / "result.json").exists() else {}
        patch_path = artifact / "patch.diff"
        wal_files = sorted((artifact / "sft-online-wal").glob("*.json"))
        if result.get("status") != "completed":
            rejected.append({"instance_id": instance_id, "reason": "rollout_not_completed"}); continue
        if not patch_path.exists() or not patch_path.read_text(encoding="utf-8", errors="replace").strip():
            rejected.append({"instance_id": instance_id, "reason": "empty_patch"}); continue
        if not wal_files:
            rejected.append({"instance_id": instance_id, "reason": "wal_missing"}); continue
        case_uploaded = 0
        case_failed = False
        prepared: list[tuple[Path, dict[str, Any], Path, bool]] = []
        for wal_path in wal_files:
            try:
                receipt_path = receipts / f"{instance_id}-{wal_path.name}.json"
                previous = load_json(receipt_path) if receipt_path.exists() else {}
                already_uploaded = isinstance(previous, dict) and previous.get("uploaded") is True
                sample = load_json(wal_path)
                if not isinstance(sample, dict) or sample.get("protocol_version") != SFT_SAMPLE_PROTOCOL:
                    raise RuntimeError("WAL is not sft-sample-v1")
                for field in ("sample_id", "user_id", "source_raw_id"):
                    if not str(sample.get(field) or "").strip():
                        raise RuntimeError(f"WAL sample has no {field}")
                if not str(sample.get("session_id") or "").strip():
                    raise RuntimeError("WAL sample has no session_id")
                cleaned = sanitize_gold_hints(copy.deepcopy(sample), start, end)
                if has_gold_markers(cleaned, start, end):
                    raise RuntimeError("golden marker remains after sanitization")
                clean_path = run_root / "cleaned-samples" / instance_id / wal_path.name
                json_write(clean_path, cleaned)
                prepared.append((wal_path, cleaned, receipt_path, already_uploaded))
                if already_uploaded:
                    case_uploaded += 1
                    reused_receipts += 1
            except Exception as exc:
                rejected.append({"instance_id": instance_id, "wal": wal_path.name, "reason": str(exc)})
                case_failed = True
        # Validate every WAL before sending any record for this case.  This
        # avoids partial promotion when a later WAL contains a malformed hint.
        if case_failed:
            continue
        for wal_path, cleaned, receipt_path, already_uploaded in prepared:
            if already_uploaded:
                continue
            try:
                if url:
                    import urllib.error
                    import urllib.request
                    request = urllib.request.Request(
                        f"{url}/v1/gateway/upload/batch", data=json.dumps(cleaned, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}, method="POST",
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=float((gateway.get("timeout_seconds") or 30))) as response:
                            body = response.read().decode("utf-8", errors="replace")
                            code = response.status
                    except (urllib.error.URLError, OSError) as exc:
                        raise RuntimeError(f"Gateway upload failed: {exc}") from exc
                    json_write(receipt_path, {"uploaded": True, "status_code": code, "response": body})
                    uploaded += 1
                else:
                    json_write(receipt_path, {"uploaded": False, "reason": "gateway.url is empty"})
                case_uploaded += 1
            except Exception as exc:
                rejected.append({"instance_id": instance_id, "wal": wal_path.name, "reason": str(exc)})
                case_failed = True
        # A case is promoted atomically: one malformed or failed WAL invalidates
        # the whole case, even if an earlier WAL was already accepted.
        if case_uploaded and not case_failed and case_uploaded == len(wal_files):
            promoted.append(instance_id)
    upload_errors = [item for item in rejected if item.get("reason") not in {"not_resolved"}]
    summary = {
        "resolved_ids": sorted(resolved_ids),
        "promoted_ids": sorted(set(promoted)),
        "uploaded_samples": uploaded,
        "reused_upload_receipts": reused_receipts,
        "uploaded_samples_total": uploaded + reused_receipts if url else 0,
        "gateway_configured": bool(url),
        "rejected": rejected,
        "upload_errors": upload_errors,
    }
    json_write(run_root / "upload-summary.json", summary)
    return summary


def bootstrap(args: argparse.Namespace) -> int:
    """Run inside the SWE image; no Docker or host-only imports are needed."""
    artifact = Path(args.artifact)
    artifact.mkdir(parents=True, exist_ok=True)
    # YuanRong maps the case workspace to /home/root.  Keep all JiuwenSwarm
    # state and the SFT WAL inside that host-visible directory.
    (artifact / "home").mkdir(parents=True, exist_ok=True)
    status_path = artifact / "status.json"
    status = {"instance_id": args.instance_id, "status": "bootstrap"}
    json_write(status_path, status)
    runtime_env = os.environ.copy()
    app: subprocess.Popen[Any] | None = None
    cli: subprocess.Popen[Any] | None = None
    service_log = None
    try:
        project = Path("/testbed")
        if not (project / ".git").exists():
            raise RuntimeError("SWE image has no /testbed git checkout")
        subprocess.run(["git", "-C", str(project), "reset", "--hard", args.base_commit], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
        subprocess.run(["git", "-C", str(project), "clean", "-ffdx"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
        init_result = subprocess.run(
            [args.python, "-m", "jiuwenswarm.init_workspace", "-f"],
            check=True, cwd=artifact, timeout=180, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=runtime_env,
        )
        (artifact / "init-workspace.log").write_text(init_result.stdout or "", encoding="utf-8")
        data_root = artifact / ".jiuwenswarm"
        config_path = data_root / "config" / "config.yaml"
        try:
            import yaml  # type: ignore
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            defaults = config.setdefault("models", {}).setdefault("defaults", [{}])
            if not isinstance(defaults, list) or not defaults:
                defaults = [{}]
                config["models"]["defaults"] = defaults
            entry = defaults[0] if isinstance(defaults[0], dict) else {}
            mcc = entry.setdefault("model_client_config", {})
            mcc.update({"api_base": "${API_BASE}", "api_key": "${API_KEY}", "model_name": "${MODEL_NAME}", "client_provider": "${MODEL_PROVIDER}", "verify_ssl": os.getenv("MODEL_VERIFY_SSL", "0") == "1", "timeout": int(os.getenv("MODEL_TIMEOUT_SECONDS", "660"))})
            ssl_cert = os.getenv("MODEL_SSL_CERT", "").strip()
            if ssl_cert:
                mcc["ssl_cert"] = ssl_cert
            else:
                mcc.pop("ssl_cert", None)
            entry.setdefault("model_config_obj", {})["temperature"] = float(os.getenv("MODEL_TEMPERATURE", "0.95"))
            entry["alias"] = os.environ.get("MODEL_NAME", "swe-teacher")
            entry["is_default"] = True
            config.setdefault("react", {}).update({"model_name": os.environ.get("MODEL_NAME", "swe-teacher"), "enable_task_loop": os.getenv("SWE_ENABLE_TASK_LOOP", "0") == "1", "max_iterations": int(os.getenv("SWE_MAX_ITERATIONS", "100")), "completion_timeout": float(os.getenv("SWE_COMPLETION_TIMEOUT_SECONDS", "600"))})
            config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        except ImportError as exc:
            raise RuntimeError("PyYAML is required by the JiuwenSwarm runtime") from exc
        # JiuwenSwarm's app module loads get_env_file() with override=True, so
        # put the resolved values in its per-run config/.env. It is removed in
        # ``finally`` after both app and CLI have stopped.
        env_path = data_root / "config" / ".env"
        # The host env-file has already supplied API_BASE/API_KEY and ports.
        env_names = {
            "API_BASE", "API_KEY", "MODEL_NAME", "MODEL_PROVIDER",
            "MODEL_TIMEOUT_SECONDS", "MODEL_VERIFY_SSL", "MODEL_SSL_CERT",
            "MODEL_TEMPERATURE", "GATEWAY_PORT", "AGENT_SERVER_PORT",
            "JIUWENSWARM_DATA_DIR", "JIUWENSWARM_HOME", "RL_GATEWAY_URL",
            "SFT_GATEWAY_URL", "SFT_RL_GATEWAY_URL",
            "TRAJECTORY_GATEWAY_URL", "USE_RL_ONLINE_RAIL", "TRAIN_BACKEND",
            "TRAJECTORY_GATEWAY_API_KEY",
            "SFT_ONLINE_UPLOAD_MODE", "SFT_SCENARIO", "TRAJECTORY_FORCE_WAL",
            "TRAJECTORY_WAL_DIR", "RL_ONLINE_TENANT_ID",
            "RL_ONLINE_SESSION_DONE_ON_INVOKE_END",
        }
        env_path.write_text(
            "".join(
                f"{key}={quote_dotenv(os.environ[key])}\n"
                for key in sorted(env_names) if key in os.environ
            ),
            encoding="utf-8",
        )
        env_path.chmod(0o600)
        service_log = (artifact / "service.log").open("wb")
        app = subprocess.Popen([args.python, "-m", "jiuwenswarm.app", "--dotenv", str(env_path)], cwd=artifact, stdout=service_log, stderr=subprocess.STDOUT, start_new_session=True, env=runtime_env)
        port = int(os.environ.get("GATEWAY_PORT", "19001"))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if app.poll() is not None:
                raise RuntimeError(f"JiuwenSwarm app exited during startup: {app.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.25)
        else:
            raise TimeoutError(f"JiuwenSwarm gateway did not listen on {port}")
        trajectory = (artifact / "trajectory.jsonl").open("wb")
        stderr = (artifact / "chat.stderr").open("wb")
        cli = subprocess.Popen([args.python, "-m", "jiuwenswarm.cli.main", "chat", "--mode", "code.normal", "--cwd", str(project), "--project-dir", str(project), "--trusted-dir", str(project), "--session", f"swe-{args.instance_id}-{int(time.time())}", "--gateway-url", f"ws://127.0.0.1:{port}/tui", "--dotenv", str(env_path), "--jsonl", Path(args.prompt).read_text(encoding="utf-8")], cwd=project, stdout=trajectory, stderr=stderr, start_new_session=True, env=runtime_env)
        timeout = int(os.getenv("SWE_TIMEOUT_SECONDS", "1800"))
        try:
            cli_rc = cli.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate(cli)
            cli_rc = -signal.SIGKILL
            status["timed_out"] = True
        status["cli_exit_code"] = cli_rc
        reported_error = trajectory_error(artifact / "trajectory.jsonl")
        if reported_error:
            status["agent_error"] = reported_error
        status["status"] = "completed" if cli_rc == 0 and not reported_error else "agent_error"
        if status["status"] == "completed":
            wal_deadline = time.monotonic() + 10
            wal_dir = artifact / "sft-online-wal"
            while time.monotonic() < wal_deadline and not list(wal_dir.glob("*.json")):
                if app.poll() is not None:
                    break
                time.sleep(0.2)
        status["prepared_head"] = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(project), "add", "-N", "."], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # JiuwenSwarm stores its own session bookkeeping under these paths.
        # Keep that runtime metadata in the trajectory/log artifacts, but do
        # not mistake it for the SWE solution patch.
        patch_cmd = [
            "git", "-C", str(project), "diff", "--binary", args.base_commit,
            "--", ".",
            ":(exclude).agent_history/**",
            ":(exclude)coding_memory/**",
            ":(exclude)prompt_attachment/**",
        ]
        (artifact / "patch.diff").write_bytes(
            subprocess.check_output(patch_cmd, stderr=subprocess.STDOUT)
        )
        (artifact / "git-status.txt").write_text(subprocess.check_output(["git", "-C", str(project), "status", "--short", "--untracked-files=all"], text=True), encoding="utf-8")
    except Exception as exc:
        status.update({"status": "infra_error", "error_type": type(exc).__name__, "error": str(exc)})
        # The bootstrap process is PID 1 in the task container, so an
        # exception may not be visible in ``docker logs`` after the bind mount
        # is inspected.  Persist a useful traceback beside the status file.
        import traceback
        (artifact / "bootstrap-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        terminate(cli)
        terminate(app)
        try:
            env_path.unlink()  # type: ignore[name-defined]
        except (NameError, OSError):
            pass
        try:
            service_log.close()  # type: ignore[name-defined]
        except (NameError, AttributeError):
            pass
        status["finished_at"] = time.time()
        json_write(status_path, status)
    wal_dir = artifact / "sft-online-wal"
    status["wal_files"] = len(list(wal_dir.glob("*.json"))) if wal_dir.is_dir() else 0
    json_write(status_path, status)
    return 0 if status.get("status") == "completed" else 1


def host_main(args: argparse.Namespace) -> int:
    if not args.config:
        raise RuntimeError(
            "runner config is missing; use run_sft_testsuite.py --test-suite <name-or-path>"
        )
    config_path = Path(args.config).resolve()
    raw = load_json(config_path)
    if not isinstance(raw, dict):
        raise RuntimeError("config root must be an object")
    config_dir = config_path.parent
    cfg = json.loads(json.dumps(raw))
    docker_cfg = cfg.setdefault("docker", {})
    model = cfg.setdefault("model", {})
    # A smoke check only exercises Docker/Git/imports and must not need a
    # model credential.  Normal rollout requires one before a container is
    # started.
    profile_alias = str(model.get("profile") or "supervisor-default").strip()
    model["profile"] = profile_alias
    if not args.smoke:
        profile = load_supervisor_profile(profile_alias)
        model.update(
            {
                "model": profile["model_name"],
                "provider": profile["provider"],
                "api_base": profile["api_base"],
                "api_key_resolved": profile["api_key"],
            }
        )
    python = resolve_runtime(cfg, config_dir)
    case_path = resolve_path(str(cfg["swe"]["case_file"]), config_dir=config_dir)
    loaded = load_json(case_path)
    cases = loaded.get("instances", loaded.get("cases", loaded)) if isinstance(loaded, dict) else loaded
    if not isinstance(cases, list):
        raise RuntimeError("case_file must contain a JSON array")
    all_cases = [dict(case) for case in cases if isinstance(case, dict)]
    ids = {str(value) for value in cfg["swe"].get("case_ids", []) if value}
    selected = [case for case in all_cases if not ids or str(case.get("instance_id")) in ids]
    if args.case_id:
        selected = [case for case in selected if str(case.get("instance_id")) == args.case_id]
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise RuntimeError("case selection produced zero cases")
    workers = max(1, int(args.workers or docker_cfg.get("workers", 1)))
    output_root = resolve_path(str(docker_cfg.get("output_root", "/tmp/gen-swe-docker-lite/runs")), config_dir=config_dir)
    workspace_root = resolve_path(str(docker_cfg.get("workspace_root", "/tmp/gen-swe-docker-lite/workspaces")), config_dir=config_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    run_root = output_root / run_id
    run_root.mkdir(parents=True)
    backend_label = sandbox_backend_label(cfg)
    if args.smoke:
        api = create_sandbox_api(cfg)
        checked = []
        for case in selected:
            artifact = output_root / ("smoke-" + validate_case(case))
            artifact.mkdir(parents=True, exist_ok=True)
            try:
                payload = build_yuanrong_payload(cfg, case, artifact, Path(__file__).resolve(), python, {"PATH": "/opt/miniconda3/bin:/usr/bin:/bin"}, "sft-sandbox-smoke")
                instance_id = api.create(payload)
                info = api.get(instance_id)
                checked.append({"instance_id": case["instance_id"], "image": image_for(case, str(docker_cfg.get("image") or "")), "sandbox_instance_id": instance_id, "sandbox_type": info.get("sandbox_type"), "sandbox_id": info.get("sandbox_id"), "resources": info.get("resources")})
            finally:
                if 'instance_id' in locals():
                    api.delete(instance_id)
                    del instance_id
        print(json.dumps({"smoke": True, "execution_backend": backend_label, "checked": checked}, ensure_ascii=False, indent=2))
        return 0
    # Keep the configured workspace root explicit in the run artifact while
    # each case gets an isolated subdirectory.
    json_write(run_root / "run.json", {"run_id": run_id, "case_file": str(case_path), "case_ids": [validate_case(c) for c in selected], "config": {"sandbox": cfg.get("sandbox", {}), "docker": {k: v for k, v in docker_cfg.items() if k not in {"api_key", "api_key_resolved"}}, "model": {k: v for k, v in model.items() if k not in {"api_key", "api_key_resolved", "api_base", "model", "provider", "api_key_env"}}, "gateway": cfg.get("gateway", {}), "trajectory": cfg.get("trajectory", {}), "eval": cfg.get("eval", {}), "gold": cfg.get("gold", {}), "workspace_root": str(workspace_root), "runtime": "preinstalled", "python": str(python)}, "started_at": time.time()})
    script_path = Path(__file__).resolve()
    keep = bool(args.keep_container or docker_cfg.get("keep_container", False))
    gold_by_id: dict[str, dict[str, Any]] = {}
    gold_cfg = cfg.get("gold") or {}
    if bool(gold_cfg.get("enabled", False)):
        gold_source_value = str(gold_cfg.get("source") or "").strip()
        if not gold_source_value:
            raise RuntimeError("gold.enabled is true but gold.source is empty")
        gold_rows = load_rows(resolve_path(gold_source_value, config_dir=config_dir), reader_python=Path(str((cfg.get("eval") or {}).get("python") or python)))
        gold_by_id = {str(row.get("instance_id")): row for row in gold_rows}
    api = create_sandbox_api(cfg)
    fn = lambda case: run_case(api, cfg, case, run_id, script_path, python, run_root, workspace_root, gold_by_id.get(str(case.get("instance_id"))), keep)
    if workers == 1:
        results = [fn(case) for case in selected]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(fn, selected))
    summary = {"run_id": run_id, "execution_backend": backend_label, "requested": len(selected), "completed": sum(r["status"] == "completed" for r in results), "results": results, "run_dir": str(run_root)}
    json_write(run_root / "summary.json", summary)
    rollout_ok = summary["completed"] == len(selected)
    eval_cfg = cfg.get("eval") or {}
    # Evaluation is part of the normal contract.  There is deliberately no
    # public skip switch: samples are eligible for Gateway promotion only
    # after the official harness has run in YuanRong and reported resolved.
    if not bool(eval_cfg.get("enabled", True)):
        summary.update({"phase": "eval_disabled", "eval_error": "eval.enabled must be true"})
        json_write(run_root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    dataset_value = str(eval_cfg.get("dataset") or "").strip()
    if not dataset_value:
        raise RuntimeError("eval.dataset is required for the complete rollout/eval/upload flow")
    dataset_rows = load_rows(resolve_path(dataset_value, config_dir=config_dir), reader_python=Path(str(eval_cfg.get("python") or python)))
    merged_dataset = eval_dataset_rows(selected, dataset_rows)
    eval_dataset_path = run_root / "eval_dataset.json"
    json_write(eval_dataset_path, merged_dataset)
    predictions = build_predictions(selected, run_root, profile_alias)
    predictions_path = run_root / "predictions.jsonl"
    predictions_path.write_text("".join(json.dumps(prediction, ensure_ascii=False) + "\n" for prediction in predictions), encoding="utf-8")
    try:
        _report, _report_path, eval_summary = run_official_eval(cfg, run_root, selected, predictions_path, eval_dataset_path)
    except Exception as exc:
        summary.update({"phase": "eval_failed", "eval_error": str(exc)})
        json_write(run_root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    summary["eval"] = eval_summary
    upload_summary = collect_and_upload_samples(cfg, run_root, selected, set(eval_summary["resolved_ids"]))
    summary["upload"] = upload_summary
    summary["phase"] = "completed" if rollout_ok else "completed_with_rollout_failures"
    json_write(run_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # If a Gateway was configured, every eligible resolved sample must upload.
    gateway_ok = not upload_summary["gateway_configured"] or not upload_summary["upload_errors"]
    return 0 if rollout_ok and gateway_ok else 1


def self_test() -> int:
    cfg = {"gold": {"enabled": True}}
    sample = {"protocol_version": SFT_SAMPLE_PROTOCOL, "messages": [{"content": "before\n## Internal Note: Pair Programming Handoff\nsecret patch\n## STATEMENT-ENDS\nafter"}], "metadata": {"nested": "ok"}}
    start, end = marker_bounds(cfg)
    cleaned = sanitize_gold_hints(sample, start, end)
    assert cleaned["messages"][0]["content"] == "before\n\nafter"
    assert not has_gold_markers(cleaned, start, end)
    assistant_mention = {"role": "assistant", "reasoning_content": f'The prompt contains "{start}" as a marker.'}
    cleaned_mention = sanitize_gold_hints(assistant_mention, start, end)
    assert "[gold hint marker redacted]" in cleaned_mention["reasoning_content"]
    assert not has_gold_markers(cleaned_mention, start, end)
    malformed = {"role": "user", "content": f"before{start}unterminated"}
    assert has_gold_markers(sanitize_gold_hints(malformed, start, end), start, end)
    unquoted = {"role": "assistant", "reasoning_content": f"before\n{start}\nunterminated"}
    assert has_gold_markers(sanitize_gold_hints(unquoted, start, end), start, end)
    rows = eval_dataset_rows([{"instance_id": "django__django-1", "repo": "django/django", "base_commit": "0" * 40, "problem_statement": "x", "version": "3.1"}], [{"instance_id": "django__django-1", "test_patch": "", "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]"}])
    assert rows[0]["test_patch"] == ""
    django_row = {"repo": "django/django", "instance_id": "django__django-1", "FAIL_TO_PASS": json.dumps(["test_x (suite.Case)"]), "PASS_TO_PASS": json.dumps(["test_y (suite.Case)"])}
    django_log = ">>>>> Start Test Output\ntest_x (suite.Case) ... ok\ntest_y (suite.Case) ... ok\n>>>>> End Test Output\n"
    assert _official_resolved(django_row, django_log)[0] is True
    sphinx_row = {"repo": "sphinx-doc/sphinx", "instance_id": "sphinx-doc__sphinx-1", "FAIL_TO_PASS": json.dumps(["tests/test_x.py::test_x"]), "PASS_TO_PASS": "[]"}
    sphinx_log = ">>>>> Start Test Output\nPASSED tests/test_x.py::test_x\n>>>>> End Test Output\n"
    assert _official_resolved(sphinx_row, sphinx_log)[0] is True
    assert issubclass(YuanRongApi, SandboxAPI)
    assert issubclass(DockerApi, SandboxAPI)
    assert SandBoxApi is SandboxAPI
    assert sandbox_backend({"sandbox": {"backend": "yuanrong"}}) == "yuanrong"
    assert sandbox_backend({"sandbox": {"backend": "local_docker"}}) == "local_docker"
    assert "supervisor-default" in SUPERVISOR_PROFILES
    previous_root = os.environ.get(HOST_WORKSPACE_ROOT_ENV)
    previous_user = os.environ.get("JIUWENSWARM_USER_DIRECTORY")
    try:
        os.environ[HOST_WORKSPACE_ROOT_ENV] = "/home/agentos/users/test-user/.sft-swe-direct/children"
        os.environ["JIUWENSWARM_USER_DIRECTORY"] = "/home/root"
        mapped = host_visible_path(Path("/home/root/sft-runs/case"))
        assert mapped == Path("/home/agentos/users/test-user/sft-runs/case")
    finally:
        if previous_root is None:
            os.environ.pop(HOST_WORKSPACE_ROOT_ENV, None)
        else:
            os.environ[HOST_WORKSPACE_ROOT_ENV] = previous_root
        if previous_user is None:
            os.environ.pop("JIUWENSWARM_USER_DIRECTORY", None)
        else:
            os.environ["JIUWENSWARM_USER_DIRECTORY"] = previous_user
    print(json.dumps({"self_test": True, "checks": ["gold_sanitization", "assistant_marker_redaction", "malformed_marker_rejected", "marker_residue", "dataset_merge", "django_official_grading", "sphinx_official_grading", "sandbox_api_abstraction", "docker_backend_selection", "supervisor_profile_alias", "agentos_host_workspace_mapping"]}))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run SWE-bench JiuwenSwarm in pluggable Docker sandboxes")
    p.add_argument("--config", help="internal composed config produced by run_sft_testsuite.py")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--case-id")
    p.add_argument("--workers", type=int)
    p.add_argument("--keep-container", action="store_true")
    p.add_argument("--smoke", action="store_true", help="check the selected sandbox/image without calling the model")
    p.add_argument("--self-test", action="store_true", help="run local helper checks without Docker or model")
    p.add_argument("--bootstrap", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--case", help=argparse.SUPPRESS)
    p.add_argument("--prompt", help=argparse.SUPPRESS)
    p.add_argument("--artifact", help=argparse.SUPPRESS)
    p.add_argument("--python", help=argparse.SUPPRESS)
    p.add_argument("--base-commit", help=argparse.SUPPRESS)
    p.add_argument("--instance-id", help=argparse.SUPPRESS)
    return p


def main() -> int:
    args = parser().parse_args()
    if args.self_test:
        return self_test()
    if args.bootstrap:
        return bootstrap(args)
    return host_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
