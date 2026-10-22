"""Async RPC proxy; importing this module never imports macOS frameworks."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .errors import InputNotStarted
from .process import stop_process
from .schema import Action, DesktopSettings, Observation


class DriverError(RuntimeError):
    """Native RPC failed or the input executor is no longer usable."""


class MacOSProcessDriver:
    """Own a macOS worker process and exchange typed JSON commands."""

    def __init__(self, settings: DesktopSettings, *, inherited_lease_fd=None):
        self.settings = settings
        self.process = None
        self.stopped = False
        self._lease_fd = inherited_lease_fd
        self._rpc_lock = asyncio.Lock()

    async def start(self):
        """Start the isolated worker with an inherited desktop lease."""
        if self.process is not None or self.stopped:
            raise DriverError("driver instances are single-use")
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("worker.py")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(() if self._lease_fd is None else (self._lease_fd,)),
            limit=16 * 1024 * 1024,
        )
        await self._rpc("start", settings=self.settings.model_dump())

    async def _rpc(self, method, **kwargs):
        async with self._rpc_lock:
            if self.stopped or self.process is None or self.process.returncode is not None:
                raise DriverError("desktop executor has stopped")
            self.process.stdin.write((json.dumps({"method": method, **kwargs}) + "\n").encode())
            await self.process.stdin.drain()
            line = await self.process.stdout.readline()
            if not line:
                raise DriverError("desktop executor exited")
            response = json.loads(line)
            if not response["ok"]:
                if response.get("input_not_started") is True:
                    raise InputNotStarted(response["error"])
                raise DriverError(response["error"])
            return response.get("result")

    async def observe(self):
        """Capture the configured window or scoped display and its coordinate transform."""
        response = await self._rpc("observe")
        response["bounds"] = tuple(response["bounds"])
        return Observation(**response)

    async def act(self, action: Action, observation: Observation):
        # Images never travel back to the input worker as action payloads.
        """Apply one validated action tied to the previous observation."""
        metadata = asdict(observation)
        metadata.pop("png_b64")
        metadata.pop("elements")  # native references/metadata are resolved only from worker state
        await self._rpc("act", action=action.model_dump(), observation=metadata)

    async def execute_batch(self, actions, observation, **limits):
        """Execute a validated batch in the owned worker, preserving per-step receipts."""
        metadata = {k: v for k, v in asdict(observation).items() if k not in {"png_b64", "elements"}}
        return await self._rpc("batch", actions=[a.model_dump() for a in actions], observation=metadata, **limits)

    async def close(self):
        """Fence new input and reap the owned executor group."""
        self.stopped = True  # fence new input before first await
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()
        await stop_process(self.process)
