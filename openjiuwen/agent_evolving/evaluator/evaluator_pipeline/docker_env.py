# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from .models import ExecResult


class DockerEnvironment:
    @staticmethod
    def _docker_path() -> str:
        docker_path = shutil.which("docker")
        if docker_path is None:
            raise RuntimeError("docker executable not found in PATH")
        return docker_path

    def __init__(
        self,
        image_tag: str,
        container_name: str | None = None,
        cpus: int = 1,
        memory_mb: int = 2048,
        timeout: int = 900,
    ):
        self.image_tag = image_tag
        self._container_name = container_name
        self._cpus = cpus
        self._memory_mb = memory_mb
        self._timeout = timeout
        self._container_id: str | None = None

    @property
    def is_running(self) -> bool:
        return self._container_id is not None

    @property
    def container_id(self) -> str | None:
        return self._container_id

    @property
    def container_name(self) -> str:
        return self._container_name or self.image_tag.replace("/", "_").replace(":", "_")

    def build(
        self,
        dockerfile_path: Path,
        build_context: Path,
        build_timeout: int = 600,
        no_cache: bool = False,
        build_args: dict[str, str] | None = None,
    ) -> str:
        if not dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile not found: {dockerfile_path}")

        cmd = [self._docker_path(), "build"]
        if no_cache:
            cmd.append("--no-cache")
        
        # Add build arguments
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        
        cmd.extend([
            "-t", self.image_tag,
            "-f", str(dockerfile_path),
            str(build_context),
        ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=build_timeout,
            )
            if result.returncode != 0:
                logger.error(f"=== Docker Build Error Details ===")
                logger.error(f"Command: {' '.join(cmd)}")
                logger.error(f"stdout:\n{result.stdout}")
                logger.error(f"stderr:\n{result.stderr}")
                logger.error(f"==================================")
                raise RuntimeError(
                    f"docker build failed (rc={result.returncode}): "
                    f"{result.stderr[:2000]}"
                )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"docker build timed out after {build_timeout}s") from e

        logger.info(f"Image built: {self.image_tag}")
        return self.image_tag

    async def start(self) -> None:
        if self._container_id:
            await self.stop()

        memory_limit = f"{self._memory_mb}m"
        cpu_limit = str(self._cpus)

        cmd = [
            self._docker_path(), "run",
            "-d",
            "--memory", memory_limit,
            "--cpus", cpu_limit,
            self.image_tag,
            "tail", "-f", "/dev/null",
        ]

        result = await self._run_command(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")

        self._container_id = result.stdout.strip()
        await asyncio.sleep(2)
        logger.info(f"Container started: {self._container_id[:12]}")

    async def stop(self) -> None:
        if not self._container_id:
            return

        logger.info(f"Stopping container: {self._container_id[:12]}")
        try:
            await self._run_command([self._docker_path(), "stop", self._container_id], timeout=30)
            await self._run_command([self._docker_path(), "rm", self._container_id], timeout=30)
        except Exception as e:
            logger.error(f"Error stopping container: {e}")
        finally:
            self._container_id = None

    async def exec(
        self,
        command: str,
        *,
        timeout: int = 300,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        if not self._container_id:
            return ExecResult(stderr="No container running", returncode=-1)

        cmd = [self._docker_path(), "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        if env:
            for k, v in env.items():
                cmd.extend(["-e", f"{k}={v}"])
        cmd.append(self._container_id)
        cmd.extend(["bash", "-c", command])

        raw = await self._run_command(cmd, timeout=timeout)
        return ExecResult(
            stdout=raw.stdout,
            stderr=raw.stderr,
            returncode=raw.returncode,
            timed_out=raw.returncode == -1 and "timed out" in raw.stderr.lower(),
        )

    async def copy_to(self, src: Path, dst: str) -> bool:
        if not self._container_id:
            return False

        cmd = [self._docker_path(), "cp", str(src), f"{self._container_id}:{dst}"]
        result = await self._run_command(cmd, timeout=60)
        return result.returncode == 0

    async def copy_from(self, src: str, dst: Path) -> bool:
        if not self._container_id:
            return False

        cmd = [self._docker_path(), "cp", f"{self._container_id}:{src}", str(dst)]
        result = await self._run_command(cmd, timeout=60)
        return result.returncode == 0

    async def _run_command(self, cmd: list[str], timeout: int = 300) -> ExecResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return ExecResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                returncode=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception as e:
                logger.warning(f"Failed to kill timed-out process: {e}")
            return ExecResult(
                stderr=f"Command timed out after {timeout}s",
                returncode=-1,
                timed_out=True,
            )
        except Exception as e:
            return ExecResult(stderr=str(e), returncode=-1)
