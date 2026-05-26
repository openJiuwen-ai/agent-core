import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from .config import PipelineConfig

_DOCKERIGNORE = """\
__pycache__
*.pyc
*.pyo
.git
.gitignore
.node_modules
*.egg-info
dist
build
.eggs
.pytest_cache
.ruff_cache
.mypy_cache
.venv
*.so
*.whl
"""


class ContainerManager:
    """Manages Docker container lifecycle with base image support"""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.container_id: str | None = None
        self.image_tag = f"evolution/{config.task_id}:latest"
        self._rewritten_dockerfile: Path | None = None

    async def ensure_base_image(self) -> bool:
        if not self.config.force_rebuild_base_image:
            result = await self._run_command(
                ["docker", "image", "inspect", self.config.base_image],
                timeout=10
            )
            if result["returncode"] == 0:
                logger.info("Base image already exists: %s", self.config.base_image)
                return True
        else:
            logger.info("Force rebuild requested for base image: %s", self.config.base_image)

        if not self.config.auto_build_base_image:
            logger.warning("Base image not found and auto_build disabled: %s", self.config.base_image)
            return False

        logger.info("Building base image: %s", self.config.base_image)
        build_ctx = Path(tempfile.mkdtemp(prefix="jiuwenswarm_base_"))

        try:
            project_root = Path(__file__).parent.parent.parent
            agent_core_src = project_root / "agent-core"
            jiuwenswarm_src = project_root / "jiuwenswarm"

            install_mode = self.config.base_image_install_mode

            if install_mode == "auto":
                use_local_source = agent_core_src.exists() and jiuwenswarm_src.exists()
                use_git_source = False
            elif install_mode == "local":
                use_local_source = True
                use_git_source = False
                if not agent_core_src.exists() or not jiuwenswarm_src.exists():
                    logger.error("  Error: install_mode=local but source not found")
                    return False
            elif install_mode == "git":
                use_local_source = False
                use_git_source = True
            elif install_mode == "pypi":
                use_local_source = False
                use_git_source = False
            else:
                logger.warning("  Warning: unknown install_mode '%s', defaulting to auto", install_mode)
                use_local_source = agent_core_src.exists() and jiuwenswarm_src.exists()
                use_git_source = False

            if use_local_source:
                logger.info("  Install mode: local source (agent-core and jiuwenswarm from %s)", project_root)
            elif use_git_source:
                logger.info("  Install mode: git (agent-core from %s, jiuwenswarm from %s)", 
                        self.config.agent_core_git_url, self.config.jiuwenswarm_git_url)
            else:
                logger.info("  Install mode: PyPI (pip install openjiuwen jiuwenswarm)")

            dockerfile_src = self.config.base_image_dockerfile
            if dockerfile_src and dockerfile_src.exists():
                shutil.copy(dockerfile_src, build_ctx / "Dockerfile")
            else:
                (build_ctx / "Dockerfile").write_text(
                    self._get_base_dockerfile(
                        use_local_source=use_local_source,
                        use_git_source=use_git_source,
                    ),
                    encoding="utf-8"
                )

            if use_local_source:
                shutil.copytree(agent_core_src, build_ctx / "agent-core")
                shutil.copytree(jiuwenswarm_src, build_ctx / "jiuwenswarm")

            (build_ctx / ".dockerignore").write_text(_DOCKERIGNORE, encoding="utf-8")

            cmd = ["docker", "build", "-t", self.config.base_image, str(build_ctx)]
            result = await self._run_command(cmd, timeout=1800)

            if result["returncode"] == 0:
                logger.info("Base image built successfully: %s", self.config.base_image)
                return True
            else:
                logger.error("Failed to build base image: %s", result['stderr'])
                return False
        finally:
            shutil.rmtree(build_ctx, ignore_errors=True)

    def _get_base_dockerfile(self, use_local_source: bool = True, use_git_source: bool = False) -> str:
        if use_local_source:
            return self._get_local_source_dockerfile()
        if use_git_source:
            return self._get_git_dockerfile()
        return self._get_pypi_dockerfile()

    @staticmethod
    def _get_local_source_dockerfile() -> str:
        return """ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \\
    python3 \\
    python3-pip \\
    python3-venv \\
    git \\
    curl \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY agent-core /tmp/agent-core
RUN pip install \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    /tmp/agent-core && rm -rf /tmp/agent-core

COPY jiuwenswarm /tmp/jiuwenswarm
RUN pip install \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    /tmp/jiuwenswarm && rm -rf /tmp/jiuwenswarm

RUN echo "yes" | jiuwenswarm-init --force || true

ENV EVOLUTION_AUTO_SCAN=true

RUN if [ -f /root/.jiuwenswarm/config/.env ]; then \\
        grep -q "EVOLUTION_AUTO_SCAN" /root/.jiuwenswarm/config/.env || \\
        echo -e "\\n# Skill Evolution Configuration" \\
            "EVOLUTION_AUTO_SCAN=\\"true\\"" >> /root/.jiuwenswarm/config/.env; \\
    fi

WORKDIR /root
"""

    @staticmethod
    def _get_pypi_dockerfile() -> str:
        return """ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \\
    python3 \\
    python3-pip \\
    python3-venv \\
    git \\
    curl \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    openjiuwen jiuwenswarm

RUN echo "yes" | jiuwenswarm-init --force || true

ENV EVOLUTION_AUTO_SCAN=true

RUN if [ -f /root/.jiuwenswarm/config/.env ]; then \\
        grep -q "EVOLUTION_AUTO_SCAN" /root/.jiuwenswarm/config/.env || \\
        echo -e "\\n# Skill Evolution Configuration" \\
            "EVOLUTION_AUTO_SCAN=\\"true\\"" >> /root/.jiuwenswarm/config/.env; \\
    fi

WORKDIR /root
"""

    def _get_git_dockerfile(self) -> str:
        agent_core_url = self.config.agent_core_git_url
        jiuwenswarm_url = self.config.jiuwenswarm_git_url
        return f"""ARG BASE_IMAGE=ubuntu:24.04
FROM ${{BASE_IMAGE}}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \\
    python3 \\
    python3-pip \\
    python3-venv \\
    git \\
    curl \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    git+{agent_core_url}

RUN pip install \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    git+{jiuwenswarm_url}

RUN echo "yes" | jiuwenswarm-init --force || true

ENV EVOLUTION_AUTO_SCAN=true

RUN if [ -f /root/.jiuwenswarm/config/.env ]; then \\
        grep -q "EVOLUTION_AUTO_SCAN" /root/.jiuwenswarm/config/.env || \\
        echo -e "\\n# Skill Evolution Configuration" \\
            "EVOLUTION_AUTO_SCAN=\\"true\\"" >> /root/.jiuwenswarm/config/.env; \\
    fi

WORKDIR /root
"""

    async def _prepare_task_dockerfile(self) -> tuple[Path, Path]:
        dockerfile = self.config.dockerfile.resolve()
        context = self.config.build_context.resolve()

        if not dockerfile.exists():
            return dockerfile, context

        content = dockerfile.read_text(encoding="utf-8")

        if f"FROM {self.config.base_image}" in content:
            logger.info("  Dockerfile already uses base image: %s", self.config.base_image)
            return dockerfile, context

        if not self.config.auto_rewrite_dockerfile:
            logger.info("  Auto-rewrite disabled, using original Dockerfile")
            return dockerfile, context

        rewrite_rules = [
            ("FROM ubuntu:24.04", "ubuntu:24.04"),
            ("FROM ubuntu:22.04", "ubuntu:22.04"),
            ("FROM ubuntu:20.04", "ubuntu:20.04"),
        ]

        python_slim_match = re.search(r'^FROM (python:\d[\d.]*-slim)', content, re.MULTILINE)
        if python_slim_match:
            rewrite_rules.append((python_slim_match.group(0), python_slim_match.group(1)))

        original_base = None
        for rule_base, _ in rewrite_rules:
            if rule_base in content:
                original_base = rule_base
                break

        if not original_base:
            logger.warning("  Dockerfile does not use a recognized base image, skipping rewrite")
            return dockerfile, context

        logger.info("  Auto-rewriting Dockerfile: %s → FROM %s", original_base, self.config.base_image)

        modified = content.replace(original_base, f"FROM {self.config.base_image}")
        modified = self._strip_base_image_overlaps(modified)

        temp_dockerfile = context / "Dockerfile.jiuwenswarm"
        temp_dockerfile.write_text(modified, encoding="utf-8")
        self._rewritten_dockerfile = temp_dockerfile

        logger.info("  Rewritten Dockerfile saved to: %s", temp_dockerfile)
        return temp_dockerfile, context

    @staticmethod
    def _strip_base_image_overlaps(content: str) -> str:
        lines = content.split("\n")
        result = []
        skip_patterns = [
            "python3-pip",
            "python3-venv",
            "python3 \\",
            "python3 -m venv /opt/venv",
            'PATH="/opt/venv/bin:$PATH"',
            "COPY agent-core",
            "COPY jiuwenswarm",
            "jiuwenswarm-init",
            "EVOLUTION_AUTO_SCAN",
        ]

        in_apt_install = False
        apt_packages = []
        apt_header_lines = []

        for line in lines:
            stripped = line.strip()

            if any(p in stripped for p in skip_patterns):
                if "python3 \\" not in stripped and "python3-pip" not in stripped and "python3-venv" not in stripped:
                    continue
                if "python3 \\" in stripped or "python3-pip" in stripped or "python3-venv" in stripped:
                    if in_apt_install:
                        continue

            if "apt-get install" in stripped and "apt-get update" not in stripped:
                in_apt_install = True
                apt_header_lines = [line]
                apt_packages = []
                continue

            if in_apt_install:
                if stripped.endswith("\\"):
                    pkg = stripped.rstrip("\\").strip()
                    if pkg not in ("python3", "python3-pip", "python3-venv"):
                        apt_packages.append(pkg)
                    continue
                else:
                    if stripped and stripped != "&& rm -rf /var/lib/apt/lists/*":
                        apt_packages.append(stripped)
                    in_apt_install = False

                    if apt_packages:
                        result.extend(apt_header_lines)
                        for i, pkg in enumerate(apt_packages):
                            if i < len(apt_packages) - 1:
                                result.append(f"    {pkg} \\")
                            else:
                                result.append(f"    {pkg}")
                    result.append("    && rm -rf /var/lib/apt/lists/*")
                    continue

            if "pip3 install --break-system-packages" in stripped:
                line = line.replace("pip3 install --break-system-packages", "pip install")

            if "pip3 install" in stripped and "--break-system-packages" not in stripped:
                line = line.replace("pip3 install", "pip install")

            result.append(line)

        return "\n".join(result)

    def cleanup_rewritten_dockerfile(self) -> None:
        if self._rewritten_dockerfile and self._rewritten_dockerfile.exists():
            try:
                self._rewritten_dockerfile.unlink()
                logger.info("  Cleaned up rewritten Dockerfile: %s", self._rewritten_dockerfile)
            except Exception as e:
                logger.debug("  Failed to clean up rewritten Dockerfile: %s", e)
            self._rewritten_dockerfile = None

    async def build_image(self) -> bool:
        if not await self.ensure_base_image():
            return False

        logger.info("Building Docker image: %s", self.image_tag)
        if self.config.docker_no_cache:
            logger.info("  Using --no-cache (will take longer)")

        dockerfile, context = await self._prepare_task_dockerfile()

        if not dockerfile.exists():
            logger.error("Error: Dockerfile not found: %s", dockerfile)
            return False

        cmd = ["docker", "build"]
        if self.config.docker_no_cache:
            cmd.append("--no-cache")
        cmd.extend([
            "-t", self.image_tag,
            "-f", str(dockerfile),
            str(context)
        ])

        try:
            result = await self._run_command(cmd, timeout=self.config.docker_build_timeout)
            if result["returncode"] == 0:
                logger.info("Image built successfully: %s", self.image_tag)
                return True
            else:
                logger.error("Failed to build image: %s", result['stderr'])
                return False
        except Exception as e:
            logger.error("Error building image: %s", e)
            return False
        finally:
            self.cleanup_rewritten_dockerfile()

    async def start_container(self) -> str | None:
        logger.info("Starting container...")

        memory_limit = f"{self.config.container_memory_mb}m"
        cpu_limit = str(self.config.container_cpus)

        cmd = [
            "docker", "run",
            "-d",
            "--memory", memory_limit,
            "--cpus", cpu_limit,
            "-e", f"DASHSCOPE_API_KEY={self.config.api_key or ''}",
            "-e", f"DASHSCOPE_BASE_URL={self.config.api_base or ''}",
            "-e", f"MODEL_NAME={self.config.model_name}",
            self.image_tag,
            "tail", "-f", "/dev/null"
        ]

        try:
            result = await self._run_command(cmd, timeout=60)
            if result["returncode"] == 0:
                self.container_id = result["stdout"].strip()
                logger.info("Container started: %s", self.container_id[:12])
                await asyncio.sleep(2)

                workspace_result = await self.exec_in_container(
                    f"mkdir -p {self.config.workspace_dir}",
                    timeout=10
                )
                if workspace_result["returncode"] == 0:
                    logger.info("Workspace directory created: %s", self.config.workspace_dir)
                else:
                    logger.warning("Warning: Failed to create workspace directory: %s", workspace_result['stderr'])

                return self.container_id
            else:
                logger.error("Failed to start container: %s", result['stderr'])
                return None
        except Exception as e:
            logger.error("Error starting container: %s", e)
            return None

    async def stop_container(self) -> None:
        if not self.container_id:
            return

        logger.info("Stopping container: %s", self.container_id[:12])

        try:
            await self._run_command(["docker", "stop", self.container_id], timeout=30)
            await self._run_command(["docker", "rm", self.container_id], timeout=30)
            logger.info("Container stopped and removed")
        except Exception as e:
            logger.error("Error stopping container: %s", e)
        finally:
            self.container_id = None

    async def exec_in_container(
        self,
        command: str,
        timeout: int = 300,
        workdir: str | None = None
    ) -> dict[str, Any]:
        if not self.container_id:
            return {"returncode": -1, "stdout": "", "stderr": "No container running"}

        cmd = ["docker", "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.append(self.container_id)

        if isinstance(command, str):
            cmd.extend(["bash", "-c", command])
        else:
            cmd.extend(command)

        try:
            return await self._run_command(cmd, timeout=timeout)
        except Exception as e:
            return {"returncode": -1, "stdout": "", "stderr": str(e)}

    async def copy_to_container(
        self,
        src: Path,
        dst: str
    ) -> bool:
        if not self.container_id:
            return False

        cmd = ["docker", "cp", str(src), f"{self.container_id}:{dst}"]

        try:
            result = await self._run_command(cmd, timeout=60)
            return result["returncode"] == 0
        except Exception as e:
            logger.error("Error copying to container: %s", e)
            return False

    async def copy_from_container(
        self,
        src: str,
        dst: Path
    ) -> bool:
        if not self.container_id:
            return False

        cmd = ["docker", "cp", f"{self.container_id}:{src}", str(dst)]

        try:
            result = await self._run_command(cmd, timeout=60)
            return result["returncode"] == 0
        except Exception as e:
            logger.error("Error copying from container: %s", e)
            return False

    async def _run_command(
        self,
        cmd: list[str],
        timeout: int = 300
    ) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            return {
                "returncode": proc.returncode or 0,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace")
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s"
            }
        except Exception as e:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e)
            }
