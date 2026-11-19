# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mocked Win32 Job contracts; no operating system handles are created."""

from unittest.mock import Mock, call

import pytest

from openjiuwen.core.sys_operation import _windows_process_job as module


@pytest.fixture
def kernel(monkeypatch):
    api = Mock()
    api.CreateJobObjectW.return_value = 100
    api.OpenProcess.return_value = 200
    api.CreateToolhelp32Snapshot.return_value = 300
    api.OpenThread.return_value = 400
    api.ResumeThread.return_value = 1
    monkeypatch.setattr(module, "_kernel32", Mock(return_value=api))
    monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(module.ctypes, "get_last_error", Mock(return_value=5), raising=False)
    monkeypatch.setattr(module.ctypes, "WinError", lambda code: OSError(f"Win32 error {code}"), raising=False)

    def first_thread(snapshot, pointer):
        pointer._obj.th32OwnerProcessID = 123
        pointer._obj.th32ThreadID = 456
        return True

    api.Thread32First.side_effect = first_thread
    return api


def test_windows_command_is_suspended_hidden_and_resumed_after_assignment(kernel):
    job = module.WindowsProcessJob()
    assert job.spawn_options() == {"creationflags": 0x08000004}
    limits = kernel.SetInformationJobObject.call_args.args[2]._obj
    assert limits.BasicLimitInformation.LimitFlags == 0x2000
    assert limits.ProcessMemoryLimit == 0
    assert limits.JobMemoryLimit == 0
    job.attach(123)
    job.start()
    calls = [item[0] for item in kernel.mock_calls]
    assert calls.index("AssignProcessToJobObject") < calls.index("ResumeThread")
    kernel.AssignProcessToJobObject.assert_called_once_with(100, 200)
    kernel.ResumeThread.assert_called_once_with(400)
    assert call(200) in kernel.CloseHandle.call_args_list
    assert call(300) in kernel.CloseHandle.call_args_list
    assert call(400) in kernel.CloseHandle.call_args_list
    assert call(100) not in kernel.CloseHandle.call_args_list


def test_job_checks_all_members_not_just_original_parent(kernel):
    job = module.WindowsProcessJob()

    def account(handle, kind, pointer, size, returned):
        pointer._obj.ActiveProcesses = 1
        return True

    kernel.QueryInformationJobObject.side_effect = account
    assert not job.is_empty()
    kernel.CloseHandle.assert_not_called()
    kernel.QueryInformationJobObject.side_effect = None
    assert job.is_empty()


def test_explicit_stop_has_nonzero_exit_code_and_closes_handle_once(kernel):
    job = module.WindowsProcessJob()
    job.close()
    job.close()
    kernel.TerminateJobObject.assert_called_once_with(100, 1)
    kernel.CloseHandle.assert_called_once_with(100)
    assert job.is_empty()
    with pytest.raises(OSError, match="closed"):
        job.attach(123)


def test_job_assignment_error_closes_process_handle_without_resuming(kernel):
    job = module.WindowsProcessJob()
    kernel.AssignProcessToJobObject.return_value = False
    with pytest.raises(OSError):
        job.attach(123)
    kernel.CloseHandle.assert_called_once_with(200)
    kernel.ResumeThread.assert_not_called()


def test_failed_termination_keeps_job_handle_for_retry(kernel):
    job = module.WindowsProcessJob()
    kernel.TerminateJobObject.return_value = False
    with pytest.raises(OSError):
        job.close()
    kernel.CloseHandle.assert_not_called()
    kernel.TerminateJobObject.return_value = True
    job.close()
    kernel.CloseHandle.assert_called_once_with(100)


def test_failed_accounting_query_does_not_claim_empty_job(kernel):
    job = module.WindowsProcessJob()
    kernel.QueryInformationJobObject.return_value = False
    with pytest.raises(OSError):
        job.is_empty()
    kernel.CloseHandle.assert_not_called()
