# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Yuanrong sandbox management wrapper used by scheduler plugins."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("online_rl.sandbox")
_SENSITIVE_ENV_KEYS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
_EXPORT_RE = re.compile(r"(^|[;\n])(?P<prefix>\s*export\s+)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\n;]*)")


@dataclass
class YuanrongSandboxConfig:
    image: str = ""
    cpu: int = 2000
    memory: int = 4096
    cpu_limit: int = 0
    mem_limit: int = 0
    port_forwardings: list[int] = field(default_factory=lambda: [8000])
    idle_timeout: int = 600
    schedule_timeout: int = 30
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    mounts: list[Any] | None = None
    upstream: str | None = None
    proxy_port: int = 8766
    tunnel_connect_timeout: float | None = None
    runtime: str = "runsc"
    rootfs: str | None = None
    node_id: str | None = None
    swerex_port: int = 8000
    swerex_auth_token: str = "test_token_123"
    install_swerex: bool = True
    pip_index_url: str = "https://repo.huaweicloud.com/repository/pypi/simple"
    pip_trusted_host: str = "repo.huaweicloud.com"


@dataclass
class SandboxCommandResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


@dataclass
class SandboxEntryInfo:
    name: str
    path: str
    type: str  # "file" | "dir" | "symlink"
    size: int
    permissions: str
    modified_time: float


class YuanrongSandboxManager:
    """Lazy lifecycle wrapper around ``akernel_sdk.Sandbox``."""

    def __init__(
        self,
        config: YuanrongSandboxConfig,
        *,
        sandbox_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._sandbox_factory = sandbox_factory
        self._sandbox = None

    @property
    def sandbox(self) -> Any:
        if self._sandbox is None:
            self.create()
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        sandbox_id = getattr(self.sandbox, "sandbox_id", "")
        return str(sandbox_id or "")

    def create(self) -> Any:
        if self._sandbox is not None:
            return self._sandbox

        upstream = self.config.upstream
        proxy_port = self.config.proxy_port
        tunnel_connect_timeout = self.config.tunnel_connect_timeout
        old_env: dict[str, str | None] = {}
        for key, value in self.config.env.items():
            old_env[key] = os.environ.get(key)
            os.environ[key] = value
        try:
            factory = self._sandbox_factory or self._load_default_factory()
            sandbox_env = {
                key: value
                for key, value in self.config.env.items()
                if key not in {"AKERNEL_SERVER_ADDRESS", "AKERNEL_TOKEN", "OPENYUANRONG_SERVER_ADDRESS"}
            }
            # akernel_sdk currently builds reverse-tunnel WS URLs as
            # ``ws://{gateway}/...``. Yuanrong's public 443 entrypoint is TLS,
            # so create the sandbox first and attach a corrected ``wss://``
            # TunnelClient below.
            sdk_upstream = None
            port_forwardings = list(self.config.port_forwardings or [])
            if upstream:
                port_forwardings.append(int(proxy_port) - 1)
            self._sandbox = factory(
                image=self.config.image or None,
                cpu=self.config.cpu,
                memory=self.config.memory,
                cpu_limit=self.config.cpu_limit,
                mem_limit=self.config.mem_limit,
                port_forwardings=port_forwardings,
                idle_timeout=self.config.idle_timeout,
                schedule_timeout=self.config.schedule_timeout,
                env=sandbox_env,
                cwd=self.config.cwd,
                mounts=self.config.mounts,
                upstream=sdk_upstream,
                proxy_port=proxy_port,
                tunnel_connect_timeout=tunnel_connect_timeout,
                runtime=self.config.runtime,
                rootfs=self.config.rootfs,
                node_id=self.config.node_id,
            )
            if upstream:
                self._attach_reverse_tunnel(
                    upstream=upstream,
                    proxy_port=proxy_port,
                    timeout=tunnel_connect_timeout,
                )
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        logger.info("Yuanrong sandbox created id=%s image=%s", self.sandbox_id, self.config.image)
        return self._sandbox

    def _attach_reverse_tunnel(self, *, upstream: str, proxy_port: int, timeout: float | None) -> None:
        """Attach reverse tunnel with a TLS-aware WebSocket URL."""

        from yr.sandbox.tunnel_client import TunnelClient

        tunnel_port = int(proxy_port) - 1
        timeout_seconds = float(timeout if timeout is not None else os.getenv("AKERNEL_TUNNEL_CONNECT_TIMEOUT", "60"))
        sandbox_instance = getattr(self.sandbox, "_instance")
        self._yr_get(sandbox_instance.start_tunnel_server.invoke(tunnel_port, proxy_port))
        tunnel_ws_url = self._build_tunnel_ws_url(tunnel_port)
        tunnel_client = TunnelClient(upstream)
        logger.info(
            "Starting Yuanrong reverse tunnel id=%s url=%s upstream=%s timeout=%.1fs",
            self.sandbox_id,
            tunnel_ws_url,
            upstream,
            timeout_seconds,
        )
        if not tunnel_client.start(tunnel_ws_url, timeout=timeout_seconds):
            tunnel_client.stop()
            raise RuntimeError(f"Yuanrong reverse tunnel connection timeout url={tunnel_ws_url}")
        setattr(self.sandbox, "_tunnel_client", tunnel_client)
        setattr(self.sandbox, "_proxy_port", int(proxy_port))

    def _build_tunnel_ws_url(self, tunnel_port: int) -> str:
        """Build the Traefik tunnel URL, using WSS for the AKernel TLS gateway."""

        gateway = (
            os.getenv("AKERNEL_GATEWAY_ADDRESS", "").strip()
            or os.getenv("YR_GATEWAY_ADDRESS", "").strip()
            or os.getenv("AKERNEL_SERVER_ADDRESS", "").strip()
            or os.getenv("YR_SERVER_ADDRESS", "").strip()
        )
        if not gateway:
            raise RuntimeError("AKERNEL_GATEWAY_ADDRESS/YR_GATEWAY_ADDRESS/AKERNEL_SERVER_ADDRESS is required")
        safe_id = self.sandbox_id.replace("@", "-at-").replace("/", "-").replace(".", "-").replace("_", "-")[:200]
        scheme = os.getenv("AKERNEL_GATEWAY_WS_SCHEME", "").strip()
        if not scheme:
            scheme = "wss" if gateway.rsplit(":", 1)[-1] == "443" else "ws"
        return f"{scheme}://{gateway}/{safe_id}/{int(tunnel_port)}"

    @staticmethod
    def _yr_get(value: Any) -> Any:
        import yr

        return yr.get(value)

    def run(self, command: str, *, timeout: int = 60, background: bool = False) -> SandboxCommandResult:
        logger.info(
            "Running sandbox command id=%s background=%s cmd=%s",
            self.sandbox_id,
            background,
            _redact_shell_command(command),
        )
        raw = self.sandbox.commands.run(command, timeout=timeout, background=background)
        return SandboxCommandResult(
            stdout=str(getattr(raw, "stdout", "") or ""),
            stderr=str(getattr(raw, "stderr", "") or ""),
            exit_code=getattr(raw, "exit_code", None),
        )

    def ensure_swerex_server(self, *, startup_wait_seconds: float = 2.0) -> str:
        if self.config.install_swerex:
            result = self.run(
                "pip config set global.index-url {index_url} && "
                "pip config set install.trusted-host {trusted_host} && "
                "python3 -m pip install -q swe-rex 2>&1 && echo INSTALL_OK".format(
                    index_url=self.config.pip_index_url,
                    trusted_host=self.config.pip_trusted_host,
                ),
                timeout=120,
            )
            if result.exit_code not in (0, None):
                raise RuntimeError(f"Failed to install swe-rex: {result.stdout}{result.stderr}")

        self.run(
            "python3 -m swerex --host 0.0.0.0 --port {port} --auth-token {token}".format(
                port=self.config.swerex_port,
                token=self.config.swerex_auth_token,
            ),
            background=True,
        )
        time.sleep(startup_wait_seconds)
        result = self.run(
            "python3 -c \"import urllib.request; "
            "req=urllib.request.Request('http://127.0.0.1:{port}/is_alive', "
            "headers={{'X-API-Key': '{token}'}});"
            "print(urllib.request.urlopen(req, timeout=5).read().decode())\"".format(
                port=self.config.swerex_port,
                token=self.config.swerex_auth_token,
            ),
            timeout=10,
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(f"swe-rex health check failed: {result.stdout}{result.stderr}")
        return self.get_port_url(self.config.swerex_port)

    def get_port_url(self, port: int, *, internal: bool = False, https: bool = True) -> str:
        url = self.sandbox.get_port_url(port, internal=internal)
        if https:
            return str(url).replace("http://", "https://", 1)
        return str(url)

    def get_tunnel_url(self) -> str:
        """Return the loopback URL exposed by the configured reverse tunnel."""

        return str(self.sandbox.get_tunnel_url())

    def close(self) -> None:
        if self._sandbox is None:
            return
        sandbox_id = self.sandbox_id
        try:
            self._sandbox.kill()
        finally:
            self._sandbox = None
        logger.info("Yuanrong sandbox closed id=%s", sandbox_id)

    def __enter__(self) -> YuanrongSandboxManager:
        self.create()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.close()

    @staticmethod
    def _load_default_factory() -> Callable[..., Any]:
        try:
            from akernel_sdk import Sandbox
        except ImportError as exc:
            raise ImportError(
                "akernel_sdk is required for YuanrongSandboxManager. "
                "Install akernel-sdk in the runtime environment."
            ) from exc
        return Sandbox

    # ========================================================================
    # Filesystem operations
    # ========================================================================

    def read(self, path: str, *, read_format: str = "text", **kwargs: Any) -> str | bytes:
        """Read file content from sandbox filesystem.

        Args:
            path: Remote file path in sandbox.
            read_format: "text" returns str, "bytes" returns bytes.

        Returns:
            File content as str or bytes.
        """
        if "format" in kwargs:
            read_format = str(kwargs.pop("format"))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"unexpected keyword argument(s): {unexpected}")
        logger.debug("Reading sandbox file path=%s format=%s", path, read_format)
        raw = self.sandbox.files.read(path, format=read_format)
        return raw

    def write(self, path: str, data: str | bytes) -> SandboxEntryInfo:
        """Write content to sandbox filesystem.

        Args:
            path: Remote file path in sandbox.
            data: Content to write (str or bytes).

        Returns:
            EntryInfo for the written file.
        """
        logger.debug("Writing sandbox file path=%s size=%d", path, len(data))
        raw = self.sandbox.files.write(path, data)
        return SandboxEntryInfo(
            name=raw.name,
            path=raw.path,
            type=raw.type,
            size=raw.size,
            permissions=raw.permissions,
            modified_time=raw.modified_time,
        )

    def list(self, path: str, *, depth: int = 1) -> list[SandboxEntryInfo]:
        """List directory entries in sandbox filesystem.

        Args:
            path: Remote directory path.
            depth: Recursion depth (1 = immediate children only).

        Returns:
            List of EntryInfo for entries in the directory.
        """
        logger.debug("Listing sandbox directory path=%s depth=%d", path, depth)
        raw_list = self.sandbox.files.list(path, depth=depth)
        return [
            SandboxEntryInfo(
                name=entry.name,
                path=entry.path,
                type=entry.type,
                size=entry.size,
                permissions=entry.permissions,
                modified_time=entry.modified_time,
            )
            for entry in raw_list
        ]

    def exists(self, path: str) -> bool:
        """Check if path exists in sandbox filesystem."""
        return self.sandbox.files.exists(path)

    def remove(self, path: str) -> None:
        """Remove file or directory in sandbox filesystem."""
        logger.debug("Removing sandbox path=%s", path)
        self.sandbox.files.remove(path)

    def make_dir(self, path: str) -> bool:
        """Create directory in sandbox filesystem."""
        logger.debug("Making directory in sandbox path=%s", path)
        return self.sandbox.files.make_dir(path)

    def get_file_info(self, path: str) -> SandboxEntryInfo:
        """Get entry info for a path in sandbox filesystem."""
        raw = self.sandbox.files.get_info(path)
        return SandboxEntryInfo(
            name=raw.name,
            path=raw.path,
            type=raw.type,
            size=raw.size,
            permissions=raw.permissions,
            modified_time=raw.modified_time,
        )

    def copy_from_local(self, local_path: str, remote_path: str) -> None:
        """Copy file from local machine to sandbox."""
        logger.debug("Copying from local to sandbox local=%s remote=%s", local_path, remote_path)
        self.sandbox.files.copy_from_local(local_path, remote_path)

    def copy_to_local(self, remote_path: str, local_path: str) -> None:
        """Copy file from sandbox to local machine."""
        logger.debug("Copying from sandbox to local remote=%s local=%s", remote_path, local_path)
        self.sandbox.files.copy_to_local(remote_path, local_path)

    def rename(self, old_path: str, new_path: str) -> SandboxEntryInfo:
        """Rename/move file or directory in sandbox."""
        logger.debug("Renaming sandbox path %s -> %s", old_path, new_path)
        raw = self.sandbox.files.rename(old_path, new_path)
        return SandboxEntryInfo(
            name=raw.name,
            path=raw.path,
            type=raw.type,
            size=raw.size,
            permissions=raw.permissions,
            modified_time=raw.modified_time,
        )

    # ========================================================================
    # Shell operations
    # ========================================================================

    def create_shell(
        self,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        shell: str = "/bin/bash",
        timeout: int = 60,
    ) -> Any:
        """Create an interactive shell session in sandbox.

        Args:
            cwd: Working directory for the shell.
            envs: Environment variables.
            shell: Shell binary path.
            timeout: Operation timeout in seconds.

        Returns:
            Shell instance for running commands.
        """
        logger.debug("Creating sandbox shell cwd=%s shell=%s", cwd, shell)
        return self.sandbox.shells.create(cwd=cwd, envs=envs, shell=shell, timeout=timeout)

    async def shell_run(
        self,
        shell: Any,
        cmd: str,
        *,
        envs: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: int = 60,
    ) -> SandboxCommandResult:
        """Run command in an existing shell session.

        Args:
            shell: Shell instance from create_shell().
            cmd: Command to run.
            envs: Environment variables.
            cwd: Working directory.
            timeout: Command timeout in seconds.

        Returns:
            SandboxCommandResult with stdout, stderr, exit_code.
        """
        logger.debug("Running command in sandbox shell cmd=%s", cmd)
        raw = await shell.run(cmd, envs=envs, cwd=cwd, timeout=timeout)
        return SandboxCommandResult(
            stdout=raw.stdout or "",
            stderr=raw.stderr or "",
            exit_code=raw.exit_code,
        )

    async def shell_close(self, shell: Any) -> None:
        """Close a shell session."""
        logger.debug("Closing sandbox shell")
        await shell.close()

    # ========================================================================
    # Process management
    # ========================================================================

    def list_commands(self) -> list[dict]:
        """List running processes in sandbox.

        Returns:
            List of process info dicts with pid, cmd, etc.
        """
        return self.sandbox.commands.list()

    def kill_command(self, pid: int) -> bool:
        """Kill a process by PID in sandbox.

        Args:
            pid: Process ID to kill.

        Returns:
            True if process was killed, False otherwise.
        """
        logger.debug("Killing sandbox process pid=%d", pid)
        return self.sandbox.commands.kill(pid)

    def send_stdin(self, pid: int, data: str, *, eof: bool = False) -> None:
        """Send stdin data to a running process.

        Args:
            pid: Target process PID.
            data: Data to send.
            eof: If True, close stdin after sending.
        """
        self.sandbox.commands.send_stdin(pid, data, eof=eof)

    def close_stdin(self, pid: int) -> None:
        """Close stdin for a running process."""
        self.sandbox.commands.close_stdin(pid)

    # ========================================================================
    # Sandbox lifecycle and info
    # ========================================================================

    def is_running(self) -> bool:
        """Check if sandbox is currently running."""
        return self.sandbox.is_running()

    def get_info(self) -> dict[str, Any]:
        """Get sandbox info (sandbox_id, state, cpu, memory, image)."""
        info = self.sandbox.get_info()
        return {
            "sandbox_id": info.sandbox_id,
            "state": info.state,
            "cpu": info.cpu,
            "memory": info.memory,
            "image": info.image,
        }

    @classmethod
    def delete(cls, name: str, namespace: str = "akernel") -> None:
        """Delete a sandbox by name.

        Args:
            name: Sandbox name to delete.
            namespace: Namespace for the sandbox.
        """
        from akernel_sdk import Sandbox
        Sandbox.delete(name, namespace=namespace)


def _redact_shell_command(command: str) -> str:
    """Hide inline exported credentials before commands are written to logs."""

    def replace(match: re.Match[str]) -> str:
        key = match.group("key").upper()
        if any(marker in key for marker in _SENSITIVE_ENV_KEYS):
            return f"{match.group(1)}{match.group('prefix')}{match.group('key')}=<redacted>"
        return match.group(0)

    return _EXPORT_RE.sub(replace, command)
