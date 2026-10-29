# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows security API contract tests; these do not exercise native Windows ACLs."""

import ctypes
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.sys_operation.local._atomic_file import _copy_windows_security


@pytest.mark.parametrize("control", [0, 0x1000, 0x0400, 0x1400])
@pytest.mark.parametrize("failure", [None, "get", "control", "put"])
def test_windows_security_copy_flags_and_cleanup(monkeypatch, control, failure):
    owner, group, dacl, descriptor = 11, 22, 33, 44
    named_path = bool(control & 0x0400)

    def get_security(_path, _kind, _flags, owner_out, group_out, dacl_out, _sacl, descriptor_out):
        for pointer, value in zip(
            (owner_out, group_out, dacl_out, descriptor_out), (owner, group, dacl, descriptor)
        ):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = value
        return 5 if failure == "get" else 0

    def get_control(_descriptor, control_out, _revision):
        ctypes.cast(control_out, ctypes.POINTER(wintypes.WORD))[0] = control
        return failure != "control"

    def get_raw(_path, _flags, buffer, size, needed):
        ctypes.cast(needed, ctypes.POINTER(wintypes.DWORD))[0] = 4
        if size:
            ctypes.memmove(buffer, b"DACL", 4)
        return bool(size)

    api = SimpleNamespace(
        GetNamedSecurityInfoW=Mock(side_effect=get_security),
        GetSecurityDescriptorControl=Mock(side_effect=get_control),
        SetNamedSecurityInfoW=Mock(return_value=5 if failure == "put" else 0),
        GetFileSecurityW=Mock(side_effect=get_raw),
        SetFileSecurityW=Mock(return_value=failure != "put"),
    )
    kernel = SimpleNamespace(LocalFree=Mock())
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: api if name == "advapi32" else kernel, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "Windows API failure"), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    if failure:
        with pytest.raises(OSError, match="Windows API failure"):
            _copy_windows_security(Path("source"), Path("destination"))
    else:
        _copy_windows_security(Path("source"), Path("destination"))

    get_args = api.GetNamedSecurityInfoW.call_args.args
    assert get_args[:3] == ("source", 1, 7)
    assert get_args[6] is None  # SACL is not requested.
    if failure == "get":
        kernel.LocalFree.assert_not_called()
        api.GetSecurityDescriptorControl.assert_not_called()
        return
    assert kernel.LocalFree.call_count == 1
    assert kernel.LocalFree.call_args.args[0].value == descriptor
    if failure == "control":
        api.SetNamedSecurityInfoW.assert_not_called()
        api.SetFileSecurityW.assert_not_called()
        return

    expected_flags = 7 | (0x80000000 if control & 0x1000 else 0x20000000)
    if named_path:
        args = api.SetNamedSecurityInfoW.call_args.args
        assert args[:3] == ("destination", 1, expected_flags)
        assert tuple(value.value for value in args[3:6]) == (owner, group, dacl)
        assert args[6] is None
        api.GetFileSecurityW.assert_not_called()
        api.SetFileSecurityW.assert_not_called()
    else:
        assert api.GetFileSecurityW.call_count == 2
        assert all(call.args[:2] == ("source", 7) for call in api.GetFileSecurityW.call_args_list)
        args = api.SetFileSecurityW.call_args.args
        assert args[:2] == ("destination", expected_flags)
        assert args[2].raw[:4] == b"DACL"
        api.SetNamedSecurityInfoW.assert_not_called()
