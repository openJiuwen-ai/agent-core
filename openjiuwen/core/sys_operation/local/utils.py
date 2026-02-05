# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
import tempfile
from datetime import (
    datetime,
    timezone,
)
from enum import Enum
from typing import (
    AsyncGenerator,
    Dict,
    Optional,
    Union,
)

from pydantic import (
    BaseModel,
    Field,
)


class StreamEventType(str, Enum):
    """Enumeration of stream event types for async process output monitoring."""
    STDOUT = "stdout"
    STDERR = "stderr"
    EXIT = "exit"
    ERROR = "error"


class StreamEvent(BaseModel):
    """Data model for async process stream events."""
    type: StreamEventType = Field(
        ...,
        description="Type of the stream event, must be one of StreamEventType values"
    )
    data: Union[str, int] = Field(
        ...,
        description="Event payload data with type dependent on event type: "
                    "stdout/stderr = text output string, exit = integer exit code, "
                    "error = error message string"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC datetime timestamp when the event was created (auto-generated)"
    )


class InvokeData(BaseModel):
    """Structured return model for one-time async subprocess execution via invoke() method."""
    stdout: str = Field(
        ...,
        description="Complete standard output string captured from the subprocess execution"
    )
    stderr: str = Field(
        ...,
        description="Complete standard error string captured from the subprocess execution"
    )
    exit_code: int = Field(
        ...,
        description="Exit code returned by the subprocess (0 for successful execution, non-zero for errors)"
    )
    exception: Optional[Exception] = Field(
        default=None,
        description="Record exception during subprocess execution"
    )

    class Config:
        arbitrary_types_allowed = True


class AsyncProcessHandler:
    """Handler for monitoring asyncio subprocess output and state."""

    def __init__(self,
                 process: asyncio.subprocess.Process,
                 chunk_size: int = 1024,
                 encoding: str = "utf-8",
                 timeout: int = 300):
        self._process = process
        self._chunk_size = chunk_size
        self._encoding = encoding
        self._overall_timeout = timeout
        self._queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._is_executed = False

    async def invoke(self) -> InvokeData:
        """One-time execution to get structured subprocess result by wrapping stream().

        Returns:
            InvokeData - Structured result containing stdout, stderr and exit code

        Raises:
            RuntimeError: If invoke() or stream() has already been executed
            Exception: If any ERROR event is captured from the stream (timeout/reader/loop error)
        """
        if self._is_executed:
            raise RuntimeError(
                "AsyncProcessHandler: invoke() and stream() are mutually exclusive, only one can be executed once")

        self._is_executed = True
        try:
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(),
                timeout=self._overall_timeout
            )
            exit_code = self._process.returncode
        except asyncio.TimeoutError as ori_ex:
            try:
                self._process.kill()
                timeout_stdout, timeout_stderr = await asyncio.wait_for(
                    self._process.communicate(),
                    timeout=30
                )
                return InvokeData(
                    stdout=timeout_stdout.decode(self._encoding, errors='replace') if timeout_stdout else "",
                    stderr=timeout_stderr.decode(self._encoding, errors='replace') if timeout_stderr else "",
                    exit_code=self._process.returncode if self._process.returncode is not None else -1,
                    exception=ori_ex
                )
            except Exception as ex:
                return InvokeData(
                    stdout="",
                    stderr="kill process failed, error: " + str(ex),
                    exit_code=self._process.returncode if self._process.returncode is not None else -1,
                    exception=ori_ex
                )

        # Decode output
        stdout_text = stdout.decode(self._encoding, errors='replace') if stdout else ""
        stderr_text = stderr.decode(self._encoding, errors='replace') if stderr else ""
        # Defensive handling to ensure non-None exit code
        final_exit_code = exit_code if exit_code is not None else -1
        return InvokeData(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=final_exit_code
        )

    async def stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Async generator for emitting process stream events in order.

        Yields:
            StreamEvent: Sequential stream events (STDOUT/STDERR/ERROR/EXIT)
        """
        if self._is_executed:
            raise RuntimeError(
                "AsyncProcessHandler: invoke() and stream() are mutually exclusive, only one can be executed once")

        self._is_executed = True
        tasks = [
            asyncio.create_task(self._reader(self._process.stdout, StreamEventType.STDOUT)),
            asyncio.create_task(self._reader(self._process.stderr, StreamEventType.STDERR))
        ]

        try:
            start_time = asyncio.get_event_loop().time()
            while self._process.returncode is None or not self._queue.empty():
                if self._overall_timeout > 0:
                    elapsed_time = asyncio.get_event_loop().time() - start_time
                    if elapsed_time >= self._overall_timeout:
                        raise asyncio.TimeoutError
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                    yield event
                    self._queue.task_done()
                except asyncio.TimeoutError:
                    continue
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"execution timeout after {self._overall_timeout} seconds"
            )
        except Exception as e:
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"stream loop error: {str(e)}"
            )

        # Cancel any unfinished reader tasks to prevent orphaned coroutines
        for task in tasks:
            if not task.done():
                task.cancel()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Emit ERROR events for any reader task exceptions
        for result in results:
            if isinstance(result, Exception):
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data=f"reader task error: {str(result)}"
                )

        try:
            await self._queue.join()
            await self._process.wait()
            yield StreamEvent(
                type=StreamEventType.EXIT,
                data=self._process.returncode if self._process.returncode is not None else -1
            )
        except Exception as e:
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"process wait error: {str(e)}"
            )

    async def _reader(self, stream: asyncio.StreamReader, stream_type: StreamEventType):
        """Background stream reader coroutine for stdout/stderr.

        Args:
            stream: Asyncio StreamReader instance to read from (stdout/stderr)
            stream_type: Corresponding StreamEventType (STDOUT/STDERR) for the stream
        """
        try:
            while True:
                chunk = await stream.read(self._chunk_size)
                # Terminate loop when stream has no more data
                if not chunk:
                    # Terminate reader only when stream is EOF AND subprocess has exited (avoid false exit)
                    if stream.at_eof() and self._process.returncode is not None:
                        break
                    # No data temporarily, sleep to avoid CPU spinning
                    await asyncio.sleep(0.01)
                    continue
                data = chunk.decode(self._encoding, errors="replace")
                event = StreamEvent(type=stream_type, data=data)
                await self._queue.put(event)
        except Exception as e:
            await self._queue.put(StreamEvent(
                type=StreamEventType.ERROR,
                data=f"{stream_type.value} reader error: {str(e)}"
            ))


class OperationUtils:
    """Utility class for common subprocess operation helper methods."""

    @staticmethod
    async def create_tmp_file(file_content: str, file_suffix: str) -> str:
        """Asynchronously creates a unique temporary file and writes content to it.

        Args:
            file_content: Content to be written into the temporary file (UTF-8 encoded)
            file_suffix: Suffix of the temporary file, must start with dot (e.g. '.py', '.sh', '.txt')

        Returns:
            str: Absolute and unique path of the created temporary file
        """

        def _sync_create_tmp():
            try:
                with tempfile.NamedTemporaryFile(suffix=file_suffix, delete=False,
                                                 mode='w', encoding='utf-8') as tmp_file:
                    tmp_file.write(file_content)
                    return tmp_file.name
            except Exception:
                return None

        return await asyncio.to_thread(_sync_create_tmp)

    @staticmethod
    async def delete_tmp_file(file_path: str) -> bool:
        """Asynchronously deletes the specified temporary file (auxiliary method).

        Args:
            file_path: Absolute path of the temporary file to be deleted

        Returns:
            bool: True if the file is deleted successfully, False if deletion fails
        """

        def _sync_delete_tmp():
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                return False
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_sync_delete_tmp)

    @staticmethod
    def prepare_environment(custom_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Create a merged environment dictionary for subprocess execution.

        Args:
            custom_env: Optional custom environment variables to add/override

        Returns:
            Merged environment dictionary (OS env + custom env)
        """
        env = os.environ.copy()
        if custom_env:
            env.update(custom_env)
        return env

    @staticmethod
    def create_handler(process: asyncio.subprocess.Process,
                       chunk_size: int = 1024,
                       encoding: str = "utf-8",
                       timeout: int = 300) -> AsyncProcessHandler:
        """Factory method to create an AsyncProcessHandler instance.

        Args:
            process: asyncio subprocess process instance to monitor and handle
            chunk_size: Max byte size for each stream read operation (default: 1024)
            encoding: Text encoding for decoding stream binary data to string,
                common values: utf-8, gbk, latin-1 (default: utf-8)
            timeout: Overall timeout duration (in seconds) for the entire stream processing loop,
                prevents infinite blocking of the handler (default: 300)

        Returns:
            Initialized AsyncProcessHandler instance
        """
        return AsyncProcessHandler(process, chunk_size, encoding, timeout)
