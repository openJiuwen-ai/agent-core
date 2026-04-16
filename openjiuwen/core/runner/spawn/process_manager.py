# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import anyio

from openjiuwen.core.common.logging import runner_logger as logger
from openjiuwen.core.common.background_tasks import BackgroundTask, create_background_task
from openjiuwen.core.runner.spawn.protocol import (
    Message,
    MessageType,
    deserialize_message_from_stream,
    serialize_message_to_stream,
)


@dataclass
class SpawnConfig:
    """Configuration for spawned process management."""

    health_check_interval: float = 5.0
    shutdown_timeout: float = 10.0
    health_check_timeout: float = 3.0


@dataclass
class SpawnedProcessHandle:
    """
    Handle for managing a spawned child process lifecycle.

    Provides async methods for communication, health checking, and shutdown.
    When consecutive health check failures reach ``max_health_failures``,
    the optional ``on_unhealthy`` callback is invoked once.
    """

    process_id: str
    process: asyncio.subprocess.Process
    config: SpawnConfig = field(default_factory=SpawnConfig)
    on_unhealthy: Optional[Callable[[], Any]] = field(default=None, repr=False)
    max_health_failures: int = field(default=2, repr=False)
    _health_check_task: Optional[BackgroundTask] = field(default=None, repr=False)
    _is_healthy: bool = field(default=True, repr=False)
    _shutdown_requested: bool = field(default=False, repr=False)
    _consecutive_failures: int = field(default=0, repr=False)
    _unhealthy_fired: bool = field(default=False, repr=False)

    @property
    def is_alive(self) -> bool:
        """Check if the process is still running."""
        return self.process.returncode is None

    @property
    def pid(self) -> Optional[int]:
        """Get the process ID."""
        return self.process.pid

    @property
    def exit_code(self) -> Optional[int]:
        """Get the exit code if process has terminated."""
        return self.process.returncode

    @property
    def is_healthy(self) -> bool:
        """Check if the process is healthy."""
        return self._is_healthy and self.is_alive

    async def send_message(self, message: Message) -> None:
        """
        Send a message to the child process via stdin.

        Args:
            message: The message to send

        Raises:
            RuntimeError: If process stdin is not available
        """
        if self.process.stdin is None:
            raise RuntimeError(f"Process {self.process_id} stdin is not available")

        if not self.is_alive:
            raise RuntimeError(f"Process {self.process_id} is not running")

        await serialize_message_to_stream(message, self.process.stdin)
        logger.debug(
            f"Sent message to process {self.process_id}",
            message_type=message.type.value,
            process_id=self.process_id,
        )

    async def receive_message(self) -> Optional[Message]:
        """
        Receive a message from the child process via stdout.

        Returns:
            The received message, or None if EOF

        Raises:
            RuntimeError: If process stdout is not available
        """
        if self.process.stdout is None:
            raise RuntimeError(f"Process {self.process_id} stdout is not available")

        message = await deserialize_message_from_stream(self.process.stdout)

        if message is not None:
            logger.debug(
                f"Received message from process {self.process_id}",
                message_type=message.type.value,
                process_id=self.process_id,
            )

        return message

    async def start_health_check(self, interval: Optional[float] = None) -> None:
        """
        Start periodic health checks in the background.

        Args:
            interval: Health check interval in seconds (defaults to config value)
        """
        if self._health_check_task is not None and not self._health_check_task.done():
            logger.warning(
                f"Health check already running for process {self.process_id}",
                process_id=self.process_id,
            )
            return

        check_interval = interval if interval is not None else self.config.health_check_interval

        async def health_check_loop():
            while self.is_alive and not self._shutdown_requested:
                try:
                    await asyncio.sleep(check_interval)

                    if not self.is_alive or self._shutdown_requested:
                        break

                    await self._perform_health_check()

                except asyncio.CancelledError:
                    logger.debug(
                        f"Health check cancelled for process {self.process_id}",
                        process_id=self.process_id,
                    )
                    break
                except Exception as e:
                    logger.error(
                        f"Health check error for process {self.process_id}",
                        process_id=self.process_id,
                        exception=e,
                    )
                    self._is_healthy = False
                    self._record_health_failure()

        self._health_check_task = await create_background_task(
            health_check_loop(),
            name=f"spawn_health_check:{self.process_id}",
            group="runner.spawn",
        )
        logger.info(
            f"Started health check for process {self.process_id}",
            process_id=self.process_id,
            interval=check_interval,
        )

    async def stop_health_check(self) -> None:
        """Stop the health check task."""
        if self._health_check_task is not None and not self._health_check_task.done():
            await self._health_check_task.cancel(reason="spawn_health_check_stopped")
            self._health_check_task = None
            logger.info(
                f"Stopped health check for process {self.process_id}",
                process_id=self.process_id,
            )

    async def shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Gracefully shutdown the process with timeout and force kill fallback.

        Args:
            timeout: Shutdown timeout in seconds (defaults to config value)

        Returns:
            True if shutdown was graceful, False if force killed
        """
        shutdown_timeout = timeout if timeout is not None else self.config.shutdown_timeout

        if not self.is_alive:
            logger.debug(
                f"Process {self.process_id} already terminated",
                process_id=self.process_id,
                exit_code=self.exit_code,
            )
            return True

        self._shutdown_requested = True

        await self.stop_health_check()

        try:
            shutdown_message = Message(
                type=MessageType.SHUTDOWN,
                payload={"reason": "parent_initiated"},
                message_id=str(uuid.uuid4()),
            )
            await self.send_message(shutdown_message)

            try:
                with anyio.fail_after(shutdown_timeout):
                    ack = await self._wait_for_shutdown_ack()

                if ack:
                    logger.info(
                        f"Received shutdown ack from process {self.process_id}",
                        process_id=self.process_id,
                    )

                    with anyio.fail_after(2.0):
                        await self.process.wait()
                    return True
            except TimeoutError:
                logger.warning(
                    f"Shutdown timeout for process {self.process_id}, terminating",
                    process_id=self.process_id,
                    timeout=shutdown_timeout,
                )

            return await self._force_terminate()

        except Exception as e:
            logger.error(
                f"Error during shutdown of process {self.process_id}",
                process_id=self.process_id,
                exception=e,
            )
            return await self._force_terminate()

    async def force_kill(self) -> None:
        """Force kill the process immediately."""
        if not self.is_alive:
            return

        self._shutdown_requested = True
        await self.stop_health_check()

        try:
            self.process.kill()
            await self.process.wait()
            logger.info(
                f"Force killed process {self.process_id}",
                process_id=self.process_id,
            )
        except ProcessLookupError:
            logger.debug(
                f"Process {self.process_id} already terminated",
                process_id=self.process_id,
            )

    async def wait_for_completion(self) -> int:
        """
        Wait for the process to complete.

        Returns:
            The exit code of the process
        """
        if not self.is_alive:
            return self.exit_code if self.exit_code is not None else -1

        await self.stop_health_check()

        if self.process.stdin:
            self.process.stdin.close()

        exit_code = await self.process.wait()

        logger.info(
            f"Process {self.process_id} completed",
            process_id=self.process_id,
            exit_code=exit_code,
        )

        return exit_code

    async def _perform_health_check(self) -> bool:
        """
        Perform a single health check.

        Returns:
            True if health check passed, False otherwise
        """
        try:
            health_check_msg = Message(
                type=MessageType.HEALTH_CHECK,
                payload={},
                message_id=str(uuid.uuid4()),
            )

            await self.send_message(health_check_msg)

            try:
                with anyio.fail_after(self.config.health_check_timeout):
                    response = await self._wait_for_health_check_response(health_check_msg.message_id)

                if response and response.type == MessageType.HEALTH_CHECK_RESPONSE:
                    self._is_healthy = True
                    self._consecutive_failures = 0
                    logger.debug(
                        f"Health check passed for process {self.process_id}",
                        process_id=self.process_id,
                    )
                    return True
                else:
                    self._is_healthy = False
                    logger.warning(
                        f"Invalid health check response from process {self.process_id}",
                        process_id=self.process_id,
                    )
                    self._record_health_failure()
                    return False

            except TimeoutError:
                self._is_healthy = False
                logger.warning(
                    f"Health check timeout for process {self.process_id}",
                    process_id=self.process_id,
                    timeout=self.config.health_check_timeout,
                )
                self._record_health_failure()
                return False

        except Exception as e:
            self._is_healthy = False
            logger.error(
                f"Health check failed for process {self.process_id}",
                process_id=self.process_id,
                exception=e,
            )
            self._record_health_failure()
            return False

    def _record_health_failure(self) -> None:
        """Increment consecutive failure count and fire on_unhealthy once."""
        self._consecutive_failures += 1
        if (
                self._consecutive_failures >= self.max_health_failures
                and not self._unhealthy_fired
                and self.on_unhealthy is not None
        ):
            self._unhealthy_fired = True
            logger.warning(
                f"Process {self.process_id} exceeded health failure threshold "
                f"({self._consecutive_failures}/{self.max_health_failures}), "
                "firing on_unhealthy callback",
                process_id=self.process_id,
            )
            try:
                self.on_unhealthy()
            except Exception as cb_err:
                logger.error(
                    f"on_unhealthy callback error for process {self.process_id}: {cb_err}",
                    process_id=self.process_id,
                )

    async def _wait_for_health_check_response(self, message_id: str) -> Optional[Message]:
        """
        Wait for health check response with matching message ID.

        Args:
            message_id: The message ID to match

        Returns:
            The health check response message, or None
        """
        while self.is_alive:
            message = await self.receive_message()
            if message is None:
                return None

            if message.type == MessageType.HEALTH_CHECK_RESPONSE:
                return message

            logger.debug(
                "Received non-health-check message during health check wait",
                message_type=message.type.value,
                process_id=self.process_id,
            )

        return None

    async def _wait_for_shutdown_ack(self) -> bool:
        """
        Wait for shutdown acknowledgment from the child process.

        Returns:
            True if shutdown ack received, False otherwise
        """
        while self.is_alive:
            message = await self.receive_message()
            if message is None:
                return False

            if message.type == MessageType.SHUTDOWN_ACK:
                return True

            if message.type == MessageType.DONE:
                return True

            logger.debug(
                "Received non-shutdown message during shutdown wait",
                message_type=message.type.value,
                process_id=self.process_id,
            )

        return False

    async def _force_terminate(self) -> bool:
        """
        Force terminate the process.

        Returns:
            False (indicating non-graceful shutdown)
        """
        if not self.is_alive:
            return True

        try:
            self.process.terminate()

            try:
                with anyio.fail_after(3.0):
                    await self.process.wait()
            except TimeoutError:
                logger.warning(
                    f"Process {self.process_id} did not terminate, killing",
                    process_id=self.process_id,
                )
                self.process.kill()
                await self.process.wait()

            logger.info(
                f"Force terminated process {self.process_id}",
                process_id=self.process_id,
            )
            return False

        except ProcessLookupError:
            logger.debug(
                f"Process {self.process_id} already terminated",
                process_id=self.process_id,
            )
            return True


async def spawn_process(
        agent_config: dict[str, Any],
        inputs: dict[str, Any],
        config: Optional[SpawnConfig] = None,
) -> SpawnedProcessHandle:
    """
    Spawn a new process to run an agent.

    Args:
        agent_config: Configuration for the agent
        inputs: Input data for the agent
        config: Spawn configuration (uses defaults if not provided)

    Returns:
        A SpawnedProcessHandle for managing the spawned process
    """
    if config is None:
        config = SpawnConfig()

    process_id = str(uuid.uuid4())

    cmd = [
        sys.executable,
        "-m",
        "openjiuwen.core.runner.spawn.child_process",
    ]

    logger.info(
        f"Spawning process {process_id}",
        process_id=process_id,
        command=" ".join(cmd),
    )

    env = os.environ.copy()
    logging_config = agent_config.get("logging_config")
    if logging_config is not None:
        env["OPENJIUWEN_SPAWN_LOGGING_CONFIG"] = json.dumps(logging_config)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    handle = SpawnedProcessHandle(
        process_id=process_id,
        process=process,
        config=config,
    )

    init_message = Message(
        type=MessageType.INPUT,
        payload={
            "agent_config": agent_config,
            "inputs": inputs,
        },
        message_id=str(uuid.uuid4()),
    )

    await handle.send_message(init_message)

    logger.info(
        f"Successfully spawned process {process_id}",
        process_id=process_id,
        pid=process.pid,
    )

    return handle
