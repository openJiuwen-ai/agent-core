# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import importlib
import os
import sys
from typing import Any, Optional

_IS_SPAWNED_PROCESS = False
_original_stdout = sys.stdout

if __name__ == "__main__":
    import json as _json
    import os
    if os.environ.get("OPENJIUWEN_SPAWN_PROCESS") != "1":
        os.environ["OPENJIUWEN_SPAWN_PROCESS"] = "1"
        _IS_SPAWNED_PROCESS = True
        _original_stdout = sys.stdout
        sys.stdout = sys.stderr

    # Apply logging config from env BEFORE any logger import to avoid writing to the parent's log files.
    _logging_config_json = os.environ.pop("OPENJIUWEN_SPAWN_LOGGING_CONFIG", None)
    if _logging_config_json:
        from openjiuwen.core.common.logging.log_config import configure_log_config

        configure_log_config(_json.loads(_logging_config_json))

from openjiuwen.core.common.logging import runner_logger as logger
from openjiuwen.core.runner.spawn.agent_config import (
    ClassAgentSpawnConfig,
    parse_spawn_agent_config,
    SpawnAgentConfig,
    SpawnAgentKind,
    deserialize_runner_config,
)
from openjiuwen.core.runner.spawn.protocol import (
    Message,
    MessageType,
    deserialize_message_from_stream,
    serialize_message_to_stream,
)


async def read_input_from_stdin(reader: asyncio.StreamReader) -> Optional[Message]:
    """
    Read a message from stdin asynchronously.

    Args:
        reader: Async stream reader connected to stdin

    Returns:
        Deserialized Message object, or None if EOF
    """
    try:
        message = await deserialize_message_from_stream(reader)
        if message is not None:
            logger.debug(f"Received message from stdin: {message.type}")
        return message
    except Exception as e:
        logger.error(f"Error reading from stdin: {e}")
        return None


async def write_output_to_stdout(message: Message, writer: asyncio.StreamWriter) -> None:
    """
    Write a message to stdout asynchronously.

    Args:
        message: Message object to write
        writer: Async stream writer connected to stdout
    """
    try:
        await serialize_message_to_stream(message, writer)
        logger.debug(f"Sent message to stdout: {message.type}")
    except Exception as e:
        logger.error(f"Error writing to stdout: {e}")


async def handle_health_check(message: Message, writer: asyncio.StreamWriter) -> None:
    """
    Handle health check request from parent process.

    Args:
        message: Health check message from parent
        writer: Async stream writer for response
    """
    response = Message(
        type=MessageType.HEALTH_CHECK_RESPONSE,
        payload={"status": "healthy"},
        message_id=message.message_id,
    )
    await write_output_to_stdout(response, writer)


async def handle_shutdown(message: Message, writer: asyncio.StreamWriter) -> bool:
    """
    Handle graceful shutdown request from parent process.

    Args:
        message: Shutdown message from parent
        writer: Async stream writer for acknowledgment

    Returns:
        True to indicate shutdown should proceed
    """
    logger.info("Received shutdown request from parent process")
    ack = Message(
        type=MessageType.SHUTDOWN_ACK,
        payload={"status": "acknowledged"},
        message_id=message.message_id,
    )
    await write_output_to_stdout(ack, writer)
    return True


def _prepare_spawn_agent_config(agent_config: dict[str, Any]) -> Optional[SpawnAgentConfig]:
    spawn_agent_config = parse_spawn_agent_config(agent_config) if agent_config else None
    if spawn_agent_config is not None and spawn_agent_config.logging_config is not None:
        from openjiuwen.core.common.logging.log_config import configure_log_config

        configure_log_config(spawn_agent_config.logging_config)
    return spawn_agent_config


async def execute_agent(
    agent_config: SpawnAgentConfig,
    inputs: dict[str, Any],
    writer: asyncio.StreamWriter,
    streaming: bool = False,
    stream_modes: Optional[list[Any]] = None,
) -> Any:
    """
    Execute the agent within the spawned process.

    Args:
        agent_config: JSON-safe agent bootstrap configuration.
        inputs: Input data for the agent
        writer: Async stream writer for output messages
        streaming: Whether to use streaming execution

    Returns:
        Agent execution result
    """
    from openjiuwen.core.runner.runner import Runner

    session = agent_config.session_id
    if agent_config.agent_kind == SpawnAgentKind.CLASS_AGENT:
        class_config = ClassAgentSpawnConfig.model_validate(agent_config.model_dump(mode="json"))
        module = importlib.import_module(class_config.agent_module)
        agent_cls = getattr(module, class_config.agent_class)
        agent = agent_cls(**class_config.init_kwargs)
        if streaming:
            result_chunks = []
            async for chunk in Runner.run_agent_streaming(
                agent=agent,
                inputs=inputs,
                session=session,
                stream_modes=stream_modes,
            ):
                stream_message = Message(
                    type=MessageType.STREAM_CHUNK,
                    payload=chunk,
                )
                await write_output_to_stdout(stream_message, writer)
                result_chunks.append(chunk)
            return result_chunks
        else:
            return await Runner.run_agent(agent=agent, inputs=inputs, session=session)
    elif agent_config.agent_kind == SpawnAgentKind.TEAM_AGENT:
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent

        agent = await TeamAgent.from_spawn_payload(agent_config.payload)
        if streaming:
            result_chunks = []
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=agent,
                inputs=inputs,
                session=session,
                stream_modes=stream_modes,
            ):
                stream_message = Message(
                    type=MessageType.STREAM_CHUNK,
                    payload=chunk,
                )
                await write_output_to_stdout(stream_message, writer)
                result_chunks.append(chunk)
            return result_chunks
        else:
            return await Runner.run_agent_team(agent_team=agent, inputs=inputs, session=session)
    else:
        raise ValueError(f"Unsupported spawned agent kind: {agent_config.agent_kind}")


async def _run_agent_task(
    agent_config: SpawnAgentConfig,
    inputs: dict[str, Any],
    writer: asyncio.StreamWriter,
    message_id: str,
    *,
    streaming: bool = False,
    stream_modes: Any = None,
) -> None:
    """Run agent execution in a background task and write DONE/ERROR on completion."""
    try:
        result = await execute_agent(
            agent_config=agent_config,
            inputs=inputs,
            writer=writer,
            streaming=streaming,
            stream_modes=stream_modes,
        )
        done_message = Message(
            type=MessageType.DONE,
            payload={"result": result},
            message_id=message_id,
        )
        await write_output_to_stdout(done_message, writer)
        logger.info("Agent execution completed")
    except Exception as e:
        logger.error(f"Error executing agent: {e}", exc_info=True)
        error_message = Message(
            type=MessageType.ERROR,
            payload={"error": str(e), "error_type": type(e).__name__},
            message_id=message_id,
        )
        await write_output_to_stdout(error_message, writer)


async def process_message_loop(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    agent_config: Optional[SpawnAgentConfig],
    inputs: dict[str, Any],
) -> None:
    """Main message processing loop for the spawned process.

    Agent execution runs in a separate task so the message loop
    remains responsive to HEALTH_CHECK and SHUTDOWN messages.
    The loop races stdin reads against agent task completion so it
    exits promptly once the agent finishes.

    Args:
        reader: Async stream reader for stdin
        writer: Async stream writer for stdout
        agent_config: Agent bootstrap configuration
        inputs: Initial inputs for agent execution
    """
    shutdown_requested = False
    agent_task: Optional[asyncio.Task] = None
    current_agent_config = agent_config
    current_inputs = inputs

    while not shutdown_requested:
        read_task = asyncio.create_task(read_input_from_stdin(reader))

        wait_set: set[asyncio.Task] = {read_task}
        if agent_task is not None and not agent_task.done():
            wait_set.add(agent_task)

        done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

        if agent_task is not None and agent_task in done:
            if read_task not in done:
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
            break

        message = read_task.result()

        if message is None:
            logger.info("stdin closed, exiting message loop")
            break

        if message.type == MessageType.HEALTH_CHECK:
            await handle_health_check(message, writer)

        elif message.type == MessageType.SHUTDOWN:
            if agent_task is not None and not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except asyncio.CancelledError:
                    pass
            shutdown_requested = await handle_shutdown(message, writer)

        elif message.type == MessageType.INPUT:
            if agent_task is None:
                payload = message.payload

                if "agent_config" in payload:
                    current_agent_config = _prepare_spawn_agent_config(payload["agent_config"])
                if "inputs" in payload:
                    current_inputs = {**current_inputs, **payload["inputs"]}

                streaming = payload.get("streaming", False)
                stream_modes = payload.get("stream_modes")
                if current_agent_config is None:
                    error_message = Message(
                        type=MessageType.ERROR,
                        payload={"error": "Missing agent_config in child process input message.",
                                 "error_type": "ValueError"},
                        message_id=message.message_id,
                    )
                    await write_output_to_stdout(error_message, writer)
                    break

                agent_task = asyncio.create_task(
                    _run_agent_task(
                        agent_config=current_agent_config,
                        inputs=current_inputs,
                        writer=writer,
                        message_id=message.message_id,
                        streaming=streaming,
                        stream_modes=stream_modes,
                    )
                )

        else:
            logger.warning(f"Unknown message type: {message.type}")


async def run_spawned_process(
    agent_config: dict[str, Any],
    inputs: dict[str, Any],
) -> None:
    """
    Main entry point for the spawned child process.

    Sets up async stdin/stdout communication and runs the message processing loop.

    Args:
        agent_config: JSON-safe agent bootstrap configuration
        inputs: Initial input data for the agent
    """
    from openjiuwen.core.runner.runner import Runner

    spawn_agent_config = _prepare_spawn_agent_config(agent_config)
    logger.info("Starting spawned process")

    if _IS_SPAWNED_PROCESS:
        stdout_for_writer = _original_stdout
    else:
        stdout_for_writer = sys.stdout
        sys.stdout = sys.stderr

    try:
        loop = asyncio.get_event_loop()
        logger.debug(f"Event loop type: {type(loop).__name__}")

        if sys.platform == 'win32':
            logger.debug("Using Windows custom stdio reader/writer")
            # Windows: Use custom reader/writer that wraps synchronous IO in executor
            # asyncio.pipe connections have issues with stdio on Windows

            class _WindowsStdinReader:
                def __init__(self, stdin_buffer, reader_loop):
                    self._stdin_fd = stdin_buffer.fileno()
                    self._loop = reader_loop
                    self._buffer = b''
                    self._eof = False
                    self._read_event = asyncio.Event()
                    self._read_task = None
                    self._exception: Exception | None = None

                def _read_loop(self):
                    """Background thread that continuously reads from stdin."""
                    while not self._eof:
                        try:
                            # Use os.read() instead of sys.stdin.buffer.read() to avoid buffering issues
                            chunk = os.read(self._stdin_fd, 4096)
                            if not chunk:
                                self._eof = True
                                break
                            self._buffer += chunk
                            self._loop.call_soon_threadsafe(self._read_event.set)
                        except Exception as read_exception:
                            self._loop.call_soon_threadsafe(self._set_exception, read_exception)
                            break

                def _set_exception(self, exc):
                    self._exception = exc

                def _start_reading(self):
                    """Start the background read thread."""
                    if self._read_task is None:
                        self._read_task = self._loop.run_in_executor(None, self._read_loop)

                async def readline(self):
                    """Read until newline, returns bytes."""
                    self._start_reading()
                    while True:
                        if self._exception is not None:
                            raise self._exception
                        newline_pos = self._buffer.find(b"\n")
                        if newline_pos >= 0:
                            line = self._buffer[:newline_pos + 1]
                            self._buffer = self._buffer[newline_pos + 1:]
                            return line
                        if self._eof:
                            if self._buffer:
                                line = self._buffer
                                self._buffer = b""
                                return line
                            return b""
                        # Wait for more data
                        self._read_event.clear()
                        await self._read_event.wait()

            class _WindowsStdoutWriter:
                def __init__(self, stdout_buffer, writer_loop):
                    self._stdout = stdout_buffer
                    self._loop = writer_loop
                    self._write_fut = None
                    self._closed = False

                def write(self, data):
                    if self._closed:
                        raise RuntimeError('Cannot write to closed writer')
                    self._stdout.write(data)

                async def drain(self):
                    await self._loop.run_in_executor(None, self._stdout.flush)

                def close(self):
                    self._closed = True

                async def wait_closed(self):
                    pass

            reader = _WindowsStdinReader(sys.stdin.buffer, loop)
            writer = _WindowsStdoutWriter(stdout_for_writer.buffer, loop)
        else:
            # Unix: use standard pipe connections
            reader = asyncio.StreamReader()
            reader_protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)

            writer_transport, writer_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin,
                stdout_for_writer
            )
            writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)
    except Exception as e:
        import traceback
        logger.error(f"Error in spawned process: {e}, trace_back: {traceback.format_exc()}", exc_info=True)
        raise e

    try:
        if spawn_agent_config is not None and spawn_agent_config.runner_config is not None:
            Runner.set_config(deserialize_runner_config(spawn_agent_config.runner_config))
        await Runner.start()
        await process_message_loop(reader, writer, spawn_agent_config, inputs)
    except Exception as e:
        logger.error(f"Error in spawned process: {e}", exc_info=True)
        error_message = Message(
            type=MessageType.ERROR,
            payload={"error": str(e), "error_type": type(e).__name__},
        )
        await write_output_to_stdout(error_message, writer)
    finally:
        await Runner.stop()
        writer.close()
        await writer.wait_closed()
        logger.info("Spawned process exiting")


def main():
    """
    Synchronous entry point for the spawned process.

    Supports two modes:
    1. With command line arguments: python -m ... <agent_config> <inputs>
    2. Without arguments: receives initial config via stdin message
    """
    import json

    if len(sys.argv) >= 3:
        agent_config = json.loads(sys.argv[1])
        inputs = json.loads(sys.argv[2])
    else:
        agent_config = {}
        inputs = {}

    asyncio.run(run_spawned_process(agent_config, inputs))


if __name__ == "__main__":
    main()
