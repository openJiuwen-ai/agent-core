# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concrete task rollout backends used by the SFT task rollouter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ...abstract.rollouter import TaskRolloutBackend, TaskRolloutCommandResult, TaskRolloutCommandSpec
from ...core.task_rollouter import SFTTaskCase, SFTTaskRolloutConfig
from .docker_runtime import (
    SFTJiuwenclawDockerRequest,
    build_jiuwenclaw_docker_command,
    build_jiuwenclaw_docker_env,
    default_jiuwenclaw_host_path,
    default_jiuwenclaw_task_command,
    normalize_dataset_case,
)

logger = logging.getLogger(__name__)
AKERNEL_WAL_DIR = "/workspace/records/rail_v1_wal"
AKERNEL_DEFAULT_TASK_CWD = "/testbed"
DEFAULT_AKERNEL_SANDBOX_IMAGE_TEMPLATE = (
    "swr.cn-east-3.myhuaweicloud.com/openyuanrong/swe-bench-verified/{image_name_no_tag}:v2"
)
DEFAULT_AKERNEL_SANDBOX_IMAGE_PREFIX = "swr.cn-east-3.myhuaweicloud.com/openyuanrong"
DEFAULT_AKERNEL_PIP_PACKAGES = (
    "pyyaml ruamel.yaml websockets 'uvicorn[standard]' fastapi openai 'httpx[socks]' "
    "pydantic python-dotenv loguru requests json-repair psutil aiosqlite aiofiles "
    "aiohttp jsonschema jsonschema-path oauthlib python-dateutil filelock portalocker "
    "'sqlalchemy[asyncio]' sqlmodel anthropic tiktoken dashscope 'pymilvus<2.6.10' "
    "fastmcp mcp beautifulsoup4 trafilatura docx2txt python-docx pdfplumber openpyxl "
    "cacheout 'mermaid-py<0.9' pycryptodome charset-normalizer pysbd tenacity alembic "
    "anyio gitcode-api pyoxigraph a2ui-agent-sdk chromadb lark-oapi pgvector croniter "
    "python-telegram-bot discord.py dingtalk-stream wecom-aibot-sdk python-socks greenlet "
    "skillnet-ai mutagen google-genai opentelemetry-api opentelemetry-sdk "
    "opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http "
    "sqlite-vec python-multipart faiss-cpu numpy"
)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _task_rollout_extra_env(tenant_id: str) -> dict[str, str]:
    return {
        "CUSTOM_HEADERS": json.dumps({"x-user-id": tenant_id}, separators=(",", ":")),
        "ENABLE_TRAJECTORY_COLLECTION": "false",
        "MEMORY_ENGINE": "none",
        "JIUWENSWARM_LIGHT_PROFILE": "1",
    }


def _host_pythonpath() -> str:
    agent_core_host = Path(
        os.getenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", "") or Path(__file__).resolve().parents[6]
    ).resolve()
    jiuwenclaw_host = default_jiuwenclaw_host_path(agent_core_host)
    paths = [str(agent_core_host)]
    if jiuwenclaw_host.exists():
        paths.append(str(jiuwenclaw_host))
    current = os.getenv("PYTHONPATH", "").strip()
    if current:
        paths.append(current)
    return ":".join(paths)


def _repo_owner_and_name(case: SFTTaskCase) -> tuple[str, str]:
    repo = case.repo.strip()
    if repo and "/" in repo:
        owner, repo_name = repo.split("/", 1)
        return owner.strip(), repo_name.strip()
    owner = case.instance_id.split("__", 1)[0].strip()
    repo_name = repo.strip() or case.instance_id.split("__", 1)[1].split("-", 1)[0].strip()
    return owner, repo_name


def _default_local_repo_root() -> Path:
    configured = os.getenv("SFT_LOCAL_REPO_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path("/data1/lll/workspace/openjiuwen/code-agent/benchmarks/swe-bench/swe-bench-test/github").resolve()


def _local_repo_source_path(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> Path:
    configured = (case.local_repo_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if (candidate / ".git").exists():
            return candidate
    root = Path(config.local_repo_root or _default_local_repo_root()).expanduser().resolve()
    owner, repo_name = _repo_owner_and_name(case)
    candidates = [
        root / owner,
        root / repo_name,
        root / owner / repo_name,
        root / repo_name / repo_name,
    ]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate.resolve()
    raise FileNotFoundError(
        f"local SWE repo checkout not found for {case.instance_id}: "
        f"searched {[str(path) for path in candidates]}"
    )


def _local_repo_work_dir(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> Path:
    configured = config.local_repo_work_root or os.getenv(
        "SFT_LOCAL_REPO_WORK_ROOT",
        "/tmp/jiuwenswarm-local-repos",
    )
    work_root = Path(configured)
    work_root = work_root.expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{case.instance_id}-", dir=str(work_root)))


def _prepare_local_repo_checkout(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> tuple[Path, Path, Path]:
    source_repo = _local_repo_source_path(case, config)
    work_dir = _local_repo_work_dir(case, config)
    repo_dir = work_dir / "repo"
    git_bin = shutil.which("git") or "/usr/bin/git"
    clone_result = subprocess.run(
        [git_bin, "clone", "--no-hardlinks", str(source_repo), str(repo_dir)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if clone_result.returncode != 0:
        detail = clone_result.stderr.strip() or clone_result.stdout.strip()
        raise RuntimeError(f"failed to clone local SWE repo {source_repo} -> {repo_dir}: {detail}")
    if case.base_commit:
        checkout_result = subprocess.run(
            [git_bin, "-C", str(repo_dir), "checkout", "--force", case.base_commit],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if checkout_result.returncode != 0:
            logger.warning(
                "local SWE repo checkout missing commit=%s repo=%s; continuing at current HEAD: %s",
                case.base_commit,
                source_repo,
                checkout_result.stderr.strip() or checkout_result.stdout.strip(),
            )
    return source_repo, work_dir, repo_dir


def _local_program_source_path(case: SFTTaskCase) -> Path:
    configured = (case.local_program_path or case.local_repo_path or "").strip()
    if not configured:
        raise FileNotFoundError(f"local program path is required for {case.instance_id}")
    candidate = Path(configured).expanduser().resolve()
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"local program directory not found for {case.instance_id}: {candidate}")


def _prepare_local_program_workspace(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> tuple[Path, Path, Path]:
    source_dir = _local_program_source_path(case)
    work_dir = _local_repo_work_dir(case, config)
    repo_dir = work_dir / "repo"
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git")
    shutil.copytree(source_dir, repo_dir, ignore=ignore)
    return source_dir, work_dir, repo_dir


def _base_task_env(
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
    *,
    data_dir: Path,
) -> dict[str, str]:
    dataset_case_json = json.dumps(
        normalize_dataset_case(
            case.dataset_case(),
            image=case.docker_image,
            task_prompt=case.task_prompt,
            instance_id=case.instance_id,
        ),
        ensure_ascii=False,
    )
    return build_jiuwenclaw_docker_env(
        SFTJiuwenclawDockerRequest(
            image=case.docker_image,
            task_prompt=case.task_prompt,
            instance_id=case.instance_id,
            dataset_case=case.dataset_case(),
            gateway_url=config.gateway_url,
            supervisor_url=config.supervisor_url,
            supervisor_token=config.supervisor_token,
            supervisor_model=config.supervisor_model,
            tenant_id=config.tenant_id,
            rollout_command=config.rollout_command,
            data_dir=str(data_dir),
            sft_upload_mode=config.sft_upload_mode,
            extra_env=_task_rollout_extra_env(config.tenant_id),
        ),
        dataset_case_json=dataset_case_json,
        pythonpath=_host_pythonpath(),
        data_dir=str(data_dir),
    )


def _build_host_process_env(
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
    *,
    repo_dir: Path,
    work_dir: Path,
    data_dir: Path,
    web_port: int,
    agent_port: int,
    index: int,
    extra: dict[str, str],
) -> dict[str, str]:
    env = _base_task_env(case, config, data_dir=data_dir)
    env.update(
        {
            "SFT_TASK_CWD": str(repo_dir),
            "HOME": str(data_dir),
            "WEB_PORT": str(web_port),
            "GATEWAY_PORT": str(config.local_repo_web_port_base + 1000 + index),
            "AGENT_SERVER_PORT": str(agent_port),
            "AGENT_PORT": str(agent_port),
            "SFT_LOCAL_REPO_WORKDIR": str(work_dir),
            **extra,
        }
    )
    merged_env = dict(os.environ)
    merged_env.update(env)
    return merged_env


def _host_task_command(config: SFTTaskRolloutConfig) -> list[str]:
    rollout_command = config.rollout_command.strip() or default_jiuwenclaw_task_command()
    python_bin = Path(sys.executable).resolve().parent
    command_prefix = f"set -e; export PATH={shlex.quote(str(python_bin))}:$PATH; hash -r;"
    command_text = f"{command_prefix} {rollout_command}"
    return ["bash", "-lc", command_text]


def _openai_api_base(url: str) -> str:
    """Normalize an OpenAI-compatible base URL without duplicating ``/v1``."""

    normalized = (url or "").strip().rstrip("/")
    if not normalized:
        return ""
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def _upstream_from_url(url: str) -> str:
    """Convert a local HTTP URL into the host:port form accepted by AKernel."""

    parsed = urlsplit((url or "").strip())
    if not parsed.hostname:
        raise ValueError(f"gateway URL must include a hostname: {url!r}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{host}:{port}"


def _akernel_env() -> dict[str, str]:
    """Return AKernel SDK settings without exposing credentials in logs."""

    env: dict[str, str] = {}
    for key in (
        "DEPLOYMENT",
        "AKERNEL_SERVER_ADDRESS",
        "OPENYUANRONG_SERVER_ADDRESS",
        "AKERNEL_TOKEN",
    ):
        value = os.getenv(key, "").strip()
        if value:
            env[key] = value
    env.setdefault("DEPLOYMENT", "openyuanrong")
    return env


def _post_gateway_payload(*, gateway_url: str, payload: dict[str, object], api_key: str = "") -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/gateway/upload/batch",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _replay_akernel_wal_to_gateway(manager, *, gateway_url: str, api_key: str = "") -> int:
    """Upload WAL payloads produced in a no-tunnel AKernel sandbox."""

    if not manager.exists(AKERNEL_WAL_DIR):
        return 0
    replayed = 0
    for entry in manager.list(AKERNEL_WAL_DIR, depth=1):
        if entry.type != "file" or not entry.path.endswith(".json"):
            continue
        payload = json.loads(str(manager.read(entry.path, format="text")))
        if not isinstance(payload, dict):
            continue
        _post_gateway_payload(gateway_url=gateway_url, payload=payload, api_key=api_key)
        replayed += 1
    return replayed


def _akernel_runtime_dependency_install_command() -> str:
    """Install runtime deps needed by bundled jiuwenswarm sources in SWE images."""

    if not _env_bool("AKERNEL_INSTALL_RUNTIME_DEPS", True):
        return "true"
    packages = shlex.split(os.getenv("AKERNEL_PIP_PACKAGES", DEFAULT_AKERNEL_PIP_PACKAGES))
    package_args = " ".join(shlex.quote(package) for package in packages)
    index_url = os.getenv("AKERNEL_PIP_INDEX_URL", "https://repo.huaweicloud.com/repository/pypi/simple")
    trusted_host = os.getenv("AKERNEL_PIP_TRUSTED_HOST", "repo.huaweicloud.com")
    return (
        f"python3 -m pip config set global.index-url {shlex.quote(index_url)} && "
        f"python3 -m pip config set install.trusted-host {shlex.quote(trusted_host)} && "
        f"python3 -m pip install -q {package_args}"
    )


def _resolve_akernel_sandbox_image(case: SFTTaskCase) -> str:
    """Return the Yuanrong sandbox image for one SWE case.

    The local override still wins if explicitly configured. Otherwise derive
    the sandbox image from the original SWE image name:
    ``swe.cn-east-3.myhuaweicloud.com/openyuanrong/swe-<original-name>``.
    The original tag is preserved when the dataset image includes one.
    """

    explicit = (
        os.getenv("AKERNEL_SANDBOX_IMAGE", "").strip()
        or os.getenv("ONLINE_RL_SANDBOX_IMAGE", "").strip()
    )
    if explicit:
        return explicit

    image = case.docker_image.strip()
    image_name = image.rsplit("/", 1)[-1] if image else ""
    image_name_no_tag, _, tag = image_name.partition(":")
    template = os.getenv("AKERNEL_SANDBOX_IMAGE_TEMPLATE", "").strip() or DEFAULT_AKERNEL_SANDBOX_IMAGE_TEMPLATE
    if template:
        return template.format(
            image=image,
            image_name=image_name,
            image_name_no_tag=image_name_no_tag,
            tag=tag or "latest",
            instance_id=case.instance_id,
        )

    prefix = os.getenv(
        "AKERNEL_SANDBOX_IMAGE_PREFIX",
        DEFAULT_AKERNEL_SANDBOX_IMAGE_PREFIX,
    ).rstrip("/")
    if not image:
        return f"{prefix}/swe-unknown"
    if image.startswith(prefix + "/"):
        return image

    for known_prefix in (
        "swr.cn-east-3.myhuaweicloud.com/openyuanrong/",
        "swe.cn-east-3.myhuaweicloud.com/openyuanrong/",
    ):
        if image.startswith(known_prefix):
            image = image[len(known_prefix):]
            break

    image_name = image.rsplit("/", 1)[-1]
    return f"{prefix}/swe-{image_name}"


def _archive_add_file(archive: tarfile.TarFile, source: Path, target: str) -> None:
    """Add one project metadata file when present."""

    if source.is_file():
        archive.add(source, arcname=target)


def _archive_add_tree(archive: tarfile.TarFile, source: Path, target: str) -> None:
    """Add a source tree while excluding caches and repository metadata."""

    excluded = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
    if not source.exists():
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_file():
            archive.add(path, arcname=str(Path(target) / relative))


def _archive_agent_core_project(archive: tarfile.TarFile, source: Path, target: str) -> None:
    """Package the current agent-core project in an editable-installable form."""

    for filename in ("pyproject.toml", "README.md", "README.zh.md", "LICENSE", "MANIFEST.in"):
        _archive_add_file(archive, source / filename, str(Path(target) / filename))
    _archive_add_tree(archive, source / "openjiuwen", str(Path(target) / "openjiuwen"))
    _archive_add_tree(
        archive,
        source / "examples" / "jiuwenrl_online" / "skills",
        str(Path(target) / "examples" / "jiuwenrl_online" / "skills"),
    )


def _archive_jiuwenclaw_project(archive: tarfile.TarFile, source: Path, target: str) -> None:
    """Package jiuwenswarm/jiuwenbox with project metadata for editable install."""

    for filename in ("pyproject.toml", "README.md", "README_CN.md", "LICENSE", "MANIFEST.in"):
        _archive_add_file(archive, source / filename, str(Path(target) / filename))
    _archive_add_tree(archive, source / "jiuwenswarm", str(Path(target) / "jiuwenswarm"))
    _archive_add_tree(
        archive,
        source / "jiuwenbox" / "src" / "jiuwenbox",
        str(Path(target) / "jiuwenbox" / "src" / "jiuwenbox"),
    )


def _akernel_should_bundle_task_source(case: SFTTaskCase) -> bool:
    """Return whether the remote sandbox should receive a local task tree."""

    if case.local_program_path:
        return True
    if case.local_repo_path:
        return True
    return _env_bool("AKERNEL_BUNDLE_TASK_SOURCE", False)


def _build_akernel_bundle(
    *,
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
) -> tuple[Path, Path]:
    """Package the current local agent sources for a remote sandbox.

    SWE AKernel images already contain the checked-out task repository under
    ``/testbed``. By default only the agent runtime is copied into the sandbox;
    local task source packaging is kept for local_program/local_repo debugging.
    """

    agent_core_root = Path(
        os.getenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", "") or Path(__file__).resolve().parents[6]
    ).resolve()
    jiuwenclaw_root = default_jiuwenclaw_host_path(agent_core_root)
    if not jiuwenclaw_root.exists():
        raise FileNotFoundError(f"jiuwenswarm source tree not found: {jiuwenclaw_root}")

    work_dir = _local_repo_work_dir(case, config)
    task_source = None
    if _akernel_should_bundle_task_source(case):
        if case.local_program_path:
            task_source = _local_program_source_path(case)
        else:
            _, _, task_source = _prepare_local_repo_checkout(case, config)

    archive_path = work_dir / "akernel-rollout.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        _archive_agent_core_project(archive, agent_core_root, "agent-core")
        _archive_jiuwenclaw_project(archive, jiuwenclaw_root, "jiuwenclaw")
        if task_source is not None:
            _archive_add_tree(archive, task_source, "task")
    return work_dir, archive_path


class DockerTaskRolloutBackend(TaskRolloutBackend):
    """Run each SWE case inside its declared task Docker image."""

    name = "docker"
    aliases = ("container", "swe_docker")

    def build_command(self, case: SFTTaskCase, config: SFTTaskRolloutConfig) -> list[str]:
        return build_jiuwenclaw_docker_command(
            SFTJiuwenclawDockerRequest(
                image=case.docker_image,
                task_prompt=case.task_prompt,
                instance_id=case.instance_id,
                dataset_case=case.dataset_case(),
                gateway_url=config.gateway_url,
                supervisor_url=config.supervisor_url,
                supervisor_token=config.supervisor_token,
                supervisor_model=config.supervisor_model,
                tenant_id=config.tenant_id,
                rollout_command=config.rollout_command,
                data_dir=f"/tmp/jiuwenswarm-{case.instance_id}",
                sft_upload_mode=config.sft_upload_mode,
                extra_env=_task_rollout_extra_env(config.tenant_id),
            )
        )

    def build_spec(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        del index
        return TaskRolloutCommandSpec(
            name=case.instance_id,
            command=self.build_command(case, config),
            timeout_seconds=config.timeout_seconds,
        )


class AKernelTaskRolloutBackend(TaskRolloutBackend):
    """Run one case in a remote Yuanrong/AKernel sandbox."""

    name = "akernel"
    aliases = ("local_repo", "local-repo", "local")

    def __init__(self, *, sandbox_manager_factory=None) -> None:
        self._sandbox_manager_factory = sandbox_manager_factory

    def build_spec(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        del config, index
        return TaskRolloutCommandSpec(
            name=case.instance_id,
            command=["akernel-sandbox", "--case", case.instance_id],
            timeout_seconds=900,
        )

    def _build_manager(self, *, case: SFTTaskCase, config: SFTTaskRolloutConfig):
        from ...sandbox import YuanrongSandboxConfig, YuanrongSandboxManager

        factory = self._sandbox_manager_factory or YuanrongSandboxManager
        use_tunnel = _env_bool("AKERNEL_USE_GATEWAY_TUNNEL", True)
        sandbox_config = YuanrongSandboxConfig(
            image=_resolve_akernel_sandbox_image(case),
            cpu=int(os.getenv("AKERNEL_SANDBOX_CPU", "2000")),
            memory=int(os.getenv("AKERNEL_SANDBOX_MEMORY", "8192")),
            cpu_limit=int(os.getenv("AKERNEL_SANDBOX_CPU_LIMIT", "0")),
            mem_limit=int(os.getenv("AKERNEL_SANDBOX_MEM_LIMIT", "0")),
            idle_timeout=int(os.getenv("AKERNEL_SANDBOX_IDLE_TIMEOUT", "1800")),
            schedule_timeout=int(os.getenv("AKERNEL_SANDBOX_SCHEDULE_TIMEOUT", "60")),
            env=_akernel_env(),
            cwd="/workspace",
            upstream=_upstream_from_url(config.gateway_url) if use_tunnel else None,
            proxy_port=int(os.getenv("AKERNEL_PROXY_PORT", "8766")),
            tunnel_connect_timeout=float(os.getenv("AKERNEL_TUNNEL_CONNECT_TIMEOUT", "30")),
        )
        if self._sandbox_manager_factory:
            return factory(sandbox_config)
        return factory(sandbox_config)

    async def run_case(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandResult:
        del index
        return await asyncio.to_thread(self._run_remote_case, case, config)

    def _run_remote_case(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
    ) -> TaskRolloutCommandResult:
        manager = None
        work_dir = None
        remote_command = ["akernel-sandbox", "--case", case.instance_id]
        try:
            work_dir, archive_path = _build_akernel_bundle(case=case, config=config)
            manager = self._build_manager(case=case, config=config)
            manager.create()
            use_tunnel = _env_bool("AKERNEL_USE_GATEWAY_TUNNEL", True)
            gateway_url = manager.get_tunnel_url().rstrip("/") if use_tunnel else config.gateway_url.rstrip("/")
            manager.copy_from_local(str(archive_path), "/tmp/akernel-rollout.tar.gz")
            extract = manager.run(
                "mkdir -p /workspace && tar -xzf /tmp/akernel-rollout.tar.gz -C /workspace",
                timeout=120,
            )
            if extract.exit_code not in (0, None):
                raise RuntimeError(f"failed to extract rollout bundle: {extract.stdout}{extract.stderr}")

            env = _base_task_env(case, config, data_dir=f"/tmp/jiuwenswarm-{case.instance_id}")
            env.update(
                {
                    "API_BASE": _openai_api_base(config.supervisor_url),
                    "API_KEY": config.supervisor_token,
                    "MODEL_NAME": config.supervisor_model,
                    "TRAJECTORY_GATEWAY_URL": gateway_url,
                    "TRAJECTORY_UPLOAD_TIMEOUT_SECONDS": os.getenv("AKERNEL_TRAJECTORY_UPLOAD_TIMEOUT_SECONDS", "2"),
                    "TRAJECTORY_WAL_DIR": AKERNEL_WAL_DIR,
                    "TRAJECTORY_FORCE_WAL": "0" if use_tunnel else "1",
                    "PYTHONPATH": "/workspace/agent-core:/workspace/jiuwenclaw",
                    "SFT_TASK_CWD": os.getenv("AKERNEL_TASK_CWD", AKERNEL_DEFAULT_TASK_CWD),
                    "HOME": f"/tmp/jiuwenswarm-{case.instance_id}",
                    "JIUWENSWARM_DATA_DIR": f"/tmp/jiuwenswarm-{case.instance_id}",
                    "SFT_JIUWENCLAW_HOME": f"/tmp/jiuwenswarm-{case.instance_id}",
                    "SFT_TASK_PRINT_APP_LOG": "1",
                    "SFT_TASK_APP_LOG_TAIL": os.getenv("SFT_TASK_APP_LOG_TAIL", "240"),
                }
            )
            env_lines = "\n".join(
                f"export {key}={shlex.quote(value)}" for key, value in env.items()
            )
            install_sources = os.getenv("AKERNEL_INSTALL_SOURCES", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            install_command = _akernel_runtime_dependency_install_command()
            if install_sources:
                # The sandbox bundle contains project metadata plus local source
                # trees, so editable installs keep the remote runtime aligned
                # with the current development checkout without fetching deps.
                install_command = (
                    f"{install_command} && "
                    "python3 -m pip install -q -e /workspace/agent-core --no-deps && "
                    "python3 -m pip install -q -e /workspace/jiuwenclaw --no-deps"
                )
            command = (
                f"set -e; rm -rf {shlex.quote(AKERNEL_WAL_DIR)}; {env_lines}; {install_command}; "
                f"{config.rollout_command.strip() or default_jiuwenclaw_task_command()}"
            )
            remote_command = ["akernel-sandbox", "run", case.instance_id]
            logger.info(
                "Running AKernel rollout case=%s sandbox=%s gateway_upstream=%s",
                case.instance_id,
                manager.sandbox_id,
                _upstream_from_url(config.gateway_url),
            )
            result = manager.run(command, timeout=config.timeout_seconds)
            replayed = _replay_akernel_wal_to_gateway(
                manager,
                gateway_url=config.gateway_url,
                api_key=os.getenv("TRAJECTORY_GATEWAY_API_KEY", ""),
            )
            return TaskRolloutCommandResult(
                name=case.instance_id,
                command=remote_command,
                exit_code=int(result.exit_code or 0),
                stdout_tail=(result.stdout + f"\n[sft-akernel] replayed_wal_payloads={replayed}\n")[-20000:],
                stderr_tail=result.stderr[-4000:],
            )
        except Exception as exc:
            logger.exception("AKernel rollout failed case=%s", case.instance_id)
            return TaskRolloutCommandResult(
                name=case.instance_id,
                command=remote_command,
                exit_code=1,
                stdout_tail="",
                stderr_tail=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if manager is not None:
                manager.close()
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)


class LocalProgramTaskRolloutBackend(TaskRolloutBackend):
    """Run a self-contained local Python task directory without Docker."""

    name = "local_program"
    aliases = ("local-program", "program")

    def build_spec(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        source_dir, work_dir, repo_dir = _prepare_local_program_workspace(case, config)
        data_dir = work_dir / "jiuwenswarm"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = _build_host_process_env(
            case,
            config,
            repo_dir=repo_dir,
            work_dir=work_dir,
            data_dir=data_dir,
            web_port=config.local_repo_web_port_base + index,
            agent_port=config.local_repo_agent_port_base + index,
            index=index,
            extra={
                "SFT_LOCAL_PROGRAM_SOURCE_DIR": str(source_dir),
                "SFT_LOCAL_PROGRAM_WORKDIR": str(work_dir),
                "SFT_TASK_LIGHT_CONFIG": os.getenv("SFT_TASK_LIGHT_CONFIG", "1"),
            },
        )
        logger.info(
            "Prepared local Python task case=%s source=%s workdir=%s",
            case.instance_id,
            source_dir,
            work_dir,
        )
        return TaskRolloutCommandSpec(
            name=case.instance_id,
            command=_host_task_command(config),
            timeout_seconds=config.timeout_seconds,
            env=env,
        )
