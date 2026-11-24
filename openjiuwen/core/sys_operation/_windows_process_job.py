# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows Job ownership, assigned before the suspended command can spawn children."""

from __future__ import annotations

import ctypes
import subprocess
import threading
from ctypes import wintypes
from typing import Any


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    # THREADENTRY32 layout; Python field names do not affect its Win32 ABI.
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("owner_process_id", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("delta_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


class WindowsProcessJob:
    """Retain the job after its initial process exits; close only on cleanup."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._kernel = _kernel32()
        self._pid: int | None = None
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    @staticmethod
    def spawn_options() -> dict[str, Any]:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4}  # CREATE_SUSPENDED

    def attach(self, pid: int) -> None:
        with self._lock:
            if not self._handle:
                raise OSError("Shell job was closed before command assignment")
            process = self._kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not self._kernel.AssignProcessToJobObject(self._handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._pid = pid
            finally:
                self._kernel.CloseHandle(process)

    def start(self) -> None:
        with self._lock:
            if not self._handle or self._pid is None:
                raise OSError("Shell job was closed before command start")
            snapshot = self._kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
            if snapshot == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                entry = _ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                more = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
                while more:
                    if entry.owner_process_id == self._pid:
                        self._resume(entry.thread_id)
                        return
                    more = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
                raise OSError("Suspended shell command has no primary thread")
            finally:
                self._kernel.CloseHandle(snapshot)

    def _resume(self, thread_id: int) -> None:
        thread = self._kernel.OpenThread(0x0002, False, thread_id)  # THREAD_SUSPEND_RESUME
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(thread)

    def is_empty(self) -> bool:
        with self._lock:
            if not self._handle:
                return True
            info = _Accounting()
            if not self._kernel.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return info.ActiveProcesses == 0

    def close(self) -> None:
        with self._lock:
            if self._handle:
                # KILL_ON_JOB_CLOSE can report exit code zero. Explicit stops
                # must not look like successfully completed commands to callers.
                if not self._kernel.TerminateJobObject(self._handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not self._kernel.CloseHandle(self._handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle = None
