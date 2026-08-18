# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Managed subprocess lifecycle for online training backends."""

from __future__ import annotations

import gc
import importlib
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

logger = logging.getLogger("online_rl.scheduler.training_process")


def release_accelerator_memory() -> None:
    """Best-effort cleanup for parent-process CUDA/NPU allocator state."""

    gc.collect()
    try:
        torch = importlib.import_module("torch")
    except (ImportError, OSError, RuntimeError):
        torch = None
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except (AttributeError, RuntimeError) as exc:
            logger.debug("CUDA memory cleanup skipped: %s", exc)

    try:
        importlib.import_module("torch_npu")
        torch = torch or importlib.import_module("torch")
        npu = getattr(torch, "npu", None)
        if npu is not None:
            npu.empty_cache()
    except (AttributeError, ImportError, OSError, RuntimeError) as exc:
        logger.debug("NPU memory cleanup skipped: %s", exc)


def _signal_from_name(value: str, default: signal.Signals) -> signal.Signals:
    normalized = (value or "").strip().upper()
    if not normalized:
        return default
    if not normalized.startswith("SIG"):
        normalized = f"SIG{normalized}"
    return getattr(signal, normalized, default)


class ManagedTrainingProcess:
    """Run one training command and allow scheduler stop requests to reach it."""

    def __init__(
        self,
        name: str,
        *,
        stop_signal: signal.Signals | None = None,
        stop_grace_seconds: float | None = None,
        kill_after_seconds: float | None = None,
    ) -> None:
        self.name = name
        self.stop_signal = stop_signal or _signal_from_name(
            os.getenv("ONLINE_TRAIN_STOP_SIGNAL", "INT"),
            signal.SIGINT,
        )
        self.stop_grace_seconds = (
            float(stop_grace_seconds)
            if stop_grace_seconds is not None
            else float(os.getenv("ONLINE_TRAIN_STOP_GRACE_SECONDS", "30"))
        )
        self.kill_after_seconds = (
            float(kill_after_seconds)
            if kill_after_seconds is not None
            else float(os.getenv("ONLINE_TRAIN_STOP_KILL_AFTER_SECONDS", "10"))
        )
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._command: Sequence[str] | str | None = None
        self._stop_requested_at: float | None = None
        self._term_sent_at: float | None = None
        self._kill_sent = False

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested_at is not None

    def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | Path,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
    ) -> int:
        """Run a training command in its own process group."""

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError(f"{self.name} training process is already running")
            self._command = command
            self._stop_requested_at = None
            self._term_sent_at = None
            self._kill_sent = False
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=dict(env) if env is not None else None,
                shell=shell,
                start_new_session=True,
            )
            self._process = process

        try:
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, command)
            return return_code
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._command = None
            release_accelerator_memory()

    def request_stop(self) -> dict[str, object]:
        """Signal the active process, escalating to terminate/kill after grace windows."""

        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return {"active": False, "action": "none", "name": self.name}

            now = time.monotonic()
            if self._stop_requested_at is None:
                self._stop_requested_at = now
                self._send_signal(process, self.stop_signal)
                return {
                    "active": True,
                    "action": f"signal:{self.stop_signal.name}",
                    "pid": process.pid,
                    "name": self.name,
                }

            elapsed = now - self._stop_requested_at
            if self._term_sent_at is None and elapsed >= self.stop_grace_seconds:
                self._term_sent_at = now
                self._send_signal(process, signal.SIGTERM)
                return {
                    "active": True,
                    "action": "signal:SIGTERM",
                    "pid": process.pid,
                    "elapsed_seconds": round(elapsed, 3),
                    "name": self.name,
                }

            if (
                self._term_sent_at is not None
                and not self._kill_sent
                and now - self._term_sent_at >= self.kill_after_seconds
            ):
                self._kill_sent = True
                kill_signal = self._kill_signal()
                self._send_signal(process, kill_signal)
                return {
                    "active": True,
                    "action": f"signal:{getattr(kill_signal, 'name', str(kill_signal))}",
                    "pid": process.pid,
                    "elapsed_seconds": round(elapsed, 3),
                    "name": self.name,
                }

            return {
                "active": True,
                "action": "waiting",
                "pid": process.pid,
                "elapsed_seconds": round(elapsed, 3),
                "name": self.name,
            }

    def force_kill(self) -> None:
        """Immediately kill the active process group, if any."""

        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                release_accelerator_memory()
                return
            self._kill_sent = True
            self._send_signal(process, self._kill_signal())
        release_accelerator_memory()

    @staticmethod
    def _kill_signal() -> signal.Signals:
        """Return the strongest available portable termination signal."""

        return getattr(signal, "SIGKILL", signal.SIGTERM)

    def _send_signal(self, process: subprocess.Popen, sig: signal.Signals) -> None:
        try:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                os.killpg(os.getpgid(process.pid), sig)
            else:
                process.send_signal(sig)
            logger.info(
                "Sent %s to %s training process group pid=%s",
                getattr(sig, "name", str(sig)),
                self.name,
                process.pid,
            )
        except ProcessLookupError:
            logger.debug(
                "%s training process already exited before %s",
                self.name,
                getattr(sig, "name", str(sig)),
            )
        except OSError as exc:
            logger.warning("Failed to signal %s training process pid=%s: %s", self.name, process.pid, exc)
