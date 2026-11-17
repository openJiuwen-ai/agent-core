# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows security API contract tests plus Windows-only publication smoke tests.

The mocked cases pin the ctypes contract. The ``skipif`` cases publish inside a
normal temporary directory and never touch restricted ACLs; protected-DACL
verification stays in the manual test plan.
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.sys_operation.local import _atomic_file
from openjiuwen.core.sys_operation.local._atomic_file import (
    _DACL_SECURITY_INFORMATION,
    _FILE_SECURITY_INFORMATION,
    _copy_windows_security,
    _copy_windows_security_or_dacl,
    _normalized_security_sddl,
    _replace_windows_target,
    _windows_error,
    _windows_security_sddl,
)


def _install_windows_dlls(monkeypatch, api, kernel):
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: api if name == "advapi32" else kernel, raising=False)


@pytest.mark.parametrize("control", [0, 0x1000, 0x0400, 0x1400])
@pytest.mark.parametrize("failure", [None, "get", "control", "put"])
def test_windows_security_copy_flags_and_cleanup(monkeypatch, control, failure):
    owner, group, dacl, descriptor = 11, 22, 33, 44
    named_path = bool(control & 0x0400)

    def get_security(_path, _kind, _flags, owner_out, group_out, dacl_out, _sacl, descriptor_out):
        for pointer, value in zip((owner_out, group_out, dacl_out, descriptor_out), (owner, group, dacl, descriptor)):
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
    _install_windows_dlls(monkeypatch, api, kernel)
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


@pytest.mark.parametrize("information", [_FILE_SECURITY_INFORMATION, _DACL_SECURITY_INFORMATION])
def test_windows_security_copy_applies_requested_information(monkeypatch, information):
    """DACL-only copies request and set the DACL alone (the #6217 fallback)."""
    owner, group, dacl, descriptor = 11, 22, 33, 44

    def get_security(_path, _kind, flags, owner_out, group_out, dacl_out, _sacl, descriptor_out):
        assert flags == information
        for pointer, value in zip((owner_out, group_out, dacl_out, descriptor_out), (owner, group, dacl, descriptor)):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = value
        return 0

    def get_control(_descriptor, control_out, _revision):
        ctypes.cast(control_out, ctypes.POINTER(wintypes.WORD))[0] = 0x1400
        return 1

    api = SimpleNamespace(
        GetNamedSecurityInfoW=Mock(side_effect=get_security),
        GetSecurityDescriptorControl=Mock(side_effect=get_control),
        SetNamedSecurityInfoW=Mock(return_value=0),
    )
    kernel = SimpleNamespace(LocalFree=Mock())
    _install_windows_dlls(monkeypatch, api, kernel)

    _copy_windows_security(Path("source"), Path("destination"), information)

    args = api.SetNamedSecurityInfoW.call_args.args
    assert args[:3] == ("destination", 1, information | 0x80000000)


@pytest.mark.parametrize("information", [_FILE_SECURITY_INFORMATION, _DACL_SECURITY_INFORMATION])
def test_windows_security_copy_legacy_raw_descriptor_respects_information(monkeypatch, information):
    """Raw descriptor reads must not leak DACL control bits into the getter flags."""

    def get_security(_path, _kind, _flags, _owner_out, _group_out, _dacl_out, _sacl, descriptor_out):
        ctypes.cast(descriptor_out, ctypes.POINTER(ctypes.c_void_p))[0] = 44
        return 0

    def get_control(_descriptor, control_out, _revision):
        ctypes.cast(control_out, ctypes.POINTER(wintypes.WORD))[0] = 0
        return 1

    def get_raw(_path, _flags, buffer, size, needed):
        ctypes.cast(needed, ctypes.POINTER(wintypes.DWORD))[0] = 4
        if size:
            ctypes.memmove(buffer, b"DACL", 4)
        return bool(size)

    api = SimpleNamespace(
        GetNamedSecurityInfoW=Mock(side_effect=get_security),
        GetSecurityDescriptorControl=Mock(side_effect=get_control),
        SetNamedSecurityInfoW=Mock(return_value=0),
        GetFileSecurityW=Mock(side_effect=get_raw),
        SetFileSecurityW=Mock(return_value=1),
    )
    kernel = SimpleNamespace(LocalFree=Mock())
    _install_windows_dlls(monkeypatch, api, kernel)

    _copy_windows_security(Path("source"), Path("destination"), information)

    assert all(call.args[:2] == ("source", information) for call in api.GetFileSecurityW.call_args_list)
    assert api.SetFileSecurityW.call_args.args[:2] == ("destination", information | 0x20000000)


def test_windows_security_sddl_frees_descriptor_and_string(monkeypatch):
    sddl = "O:S-1-5-21G:S-1-5-21D:AI(A;;FA;;;WD)"
    # Keep the wide buffer alive: assigning a bare str through a casted pointer
    # would leave the production code holding a dangling address.
    text_holder = ctypes.c_wchar_p(sddl)

    def get_security(_path, _kind, _flags, _owner_out, _group_out, _dacl_out, _sacl, descriptor_out):
        ctypes.cast(descriptor_out, ctypes.POINTER(ctypes.c_void_p))[0] = 44
        return 0

    def convert(_descriptor, _revision, flags, text_out, size_out):
        assert flags == _FILE_SECURITY_INFORMATION
        ctypes.cast(text_out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(text_holder, ctypes.c_void_p).value
        ctypes.cast(size_out, ctypes.POINTER(wintypes.DWORD))[0] = len(sddl) + 1
        return 1

    api = SimpleNamespace(
        GetNamedSecurityInfoW=Mock(side_effect=get_security),
        ConvertSecurityDescriptorToStringSecurityDescriptorW=Mock(side_effect=convert),
    )
    kernel = SimpleNamespace(LocalFree=Mock())
    _install_windows_dlls(monkeypatch, api, kernel)

    assert _windows_security_sddl(Path("target")) == "O:S-1-5-21G:S-1-5-21D:(A;;FA;;;WD)"
    assert kernel.LocalFree.call_count == 2
    freed = [call.args[0] for call in kernel.LocalFree.call_args_list]
    assert freed[0].value == sddl
    assert freed[1].value == 44


@pytest.mark.parametrize(
    ("sddl", "expected"),
    [
        ("O:S-1G:S-1D:AI(A;;FA;;;WD)", "O:S-1G:S-1D:(A;;FA;;;WD)"),
        ("O:S-1G:S-1D:PAI(A;;FA;;;WD)", "O:S-1G:S-1D:P(A;;FA;;;WD)"),
        ("O:S-1G:S-1D:P(A;;FA;;;WD)", "O:S-1G:S-1D:P(A;;FA;;;WD)"),
        ("unexpected", "unexpected"),
    ],
)
def test_normalized_security_sddl_drops_only_the_inheritance_bookmark(sddl, expected):
    assert _normalized_security_sddl(sddl) == expected


def test_copy_windows_security_degrades_to_dacl(monkeypatch):
    copy = Mock(side_effect=[_windows_error(5, "denied"), None])
    matches = Mock(return_value=True)
    monkeypatch.setattr(_atomic_file, "_copy_windows_security", copy)
    monkeypatch.setattr(_atomic_file, "_windows_security_matches", matches)

    _copy_windows_security_or_dacl(Path("source"), Path("destination"))

    assert copy.call_count == 2
    assert copy.call_args_list[0].args == (Path("source"), Path("destination"))
    assert copy.call_args_list[1].args == (Path("source"), Path("destination"), _DACL_SECURITY_INFORMATION)
    matches.assert_called_once_with(Path("source"), Path("destination"), _DACL_SECURITY_INFORMATION)


def test_copy_windows_security_refuses_unverified_dacl_copy(monkeypatch):
    copy = Mock(side_effect=[_windows_error(5, "denied"), None])
    matches = Mock(return_value=False)
    monkeypatch.setattr(_atomic_file, "_copy_windows_security", copy)
    monkeypatch.setattr(_atomic_file, "_windows_security_matches", matches)

    with pytest.raises(OSError, match="must not be published"):
        _copy_windows_security_or_dacl(Path("source"), Path("destination"))


def test_replace_windows_target_prefers_native_replace(monkeypatch, tmp_path):
    native = Mock(return_value=0)
    copy = Mock()
    replace = Mock()
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", native)
    monkeypatch.setattr(_atomic_file, "_copy_windows_security_or_dacl", copy)
    monkeypatch.setattr(_atomic_file.os, "replace", replace)

    _replace_windows_target(tmp_path / "target", tmp_path / "temporary")

    native.assert_called_once_with(tmp_path / "target", tmp_path / "temporary")
    copy.assert_not_called()
    replace.assert_not_called()


def test_replace_windows_target_falls_back_to_security_copy(monkeypatch, tmp_path):
    target, temporary = tmp_path / "target", tmp_path / "temporary"
    target.write_bytes(b"old")
    temporary.write_bytes(b"new")
    copy = Mock(return_value=None)
    replace = Mock()
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", Mock(return_value=1176))
    monkeypatch.setattr(_atomic_file, "_copy_windows_security_or_dacl", copy)
    monkeypatch.setattr(_atomic_file.os, "replace", replace)

    _replace_windows_target(target, temporary)

    copy.assert_called_once_with(target, temporary)
    replace.assert_called_once_with(temporary, target)


def test_replace_windows_target_publishes_when_descriptor_already_matches(monkeypatch, tmp_path):
    target, temporary = tmp_path / "target", tmp_path / "temporary"
    target.write_bytes(b"old")
    temporary.write_bytes(b"new")
    replace = Mock()
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", Mock(return_value=5))
    monkeypatch.setattr(_atomic_file, "_copy_windows_security_or_dacl", Mock(side_effect=_windows_error(5, "denied")))
    monkeypatch.setattr(_atomic_file, "_windows_security_matches", Mock(return_value=True))
    monkeypatch.setattr(_atomic_file.os, "replace", replace)

    _replace_windows_target(target, temporary)

    replace.assert_called_once_with(temporary, target)


def test_replace_windows_target_refuses_to_relax_security(monkeypatch, tmp_path):
    target, temporary = tmp_path / "target", tmp_path / "temporary"
    target.write_bytes(b"old")
    temporary.write_bytes(b"new")
    replace = Mock()
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", Mock(return_value=5))
    monkeypatch.setattr(_atomic_file, "_copy_windows_security_or_dacl", Mock(side_effect=_windows_error(5, "denied")))
    monkeypatch.setattr(_atomic_file, "_windows_security_matches", Mock(return_value=False))
    monkeypatch.setattr(_atomic_file.os, "replace", replace)

    with pytest.raises(OSError) as captured:
        _replace_windows_target(target, temporary)

    assert captured.value.winerror == 5
    assert str(target) in str(captured.value)
    assert "WinError 5" in str(captured.value)
    replace.assert_not_called()


def test_replace_windows_target_publishes_create_when_target_vanished(monkeypatch, tmp_path):
    target, temporary = tmp_path / "target", tmp_path / "temporary"
    temporary.write_bytes(b"new")
    copy = Mock()
    replace = Mock()
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", Mock(return_value=2))
    monkeypatch.setattr(_atomic_file, "_copy_windows_security_or_dacl", copy)
    monkeypatch.setattr(_atomic_file.os, "replace", replace)

    _replace_windows_target(target, temporary)

    copy.assert_not_called()
    replace.assert_called_once_with(temporary, target)


@pytest.mark.skipif(os.name != "nt", reason="Windows ReplaceFileW semantics")
def test_windows_publish_preserves_target_descriptor(tmp_path):
    target = tmp_path / "todo.json"
    target.write_bytes(b"old")
    before = _windows_security_sddl(target)
    temporary = tmp_path / ".todo.json-deadbeef.tmp"
    temporary.write_bytes(b"new")

    _replace_windows_target(target, temporary)

    assert target.read_bytes() == b"new"
    assert not temporary.exists()
    assert _windows_security_sddl(target) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic publication semantics")
def test_windows_atomic_publish_preserves_target_descriptor(tmp_path):
    target = tmp_path / "todo.json"
    target.write_bytes(b"old")
    before = _windows_security_sddl(target)

    _atomic_file._replace_file(target, b"new", "644")

    assert target.read_bytes() == b"new"
    assert _windows_security_sddl(target) == before
    assert not list(tmp_path.glob(".todo.json-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic publication semantics")
def test_windows_atomic_publish_survives_denied_owner_copy(monkeypatch, tmp_path):
    """Regression for #6217: no WRITE_OWNER, identical descriptor, publish anyway."""
    target = tmp_path / "todo.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(_atomic_file, "_replace_file_windows", Mock(return_value=5))
    monkeypatch.setattr(_atomic_file, "_copy_windows_security", Mock(side_effect=_windows_error(5, "denied")))

    _atomic_file._replace_file(target, b"new", "644")

    assert target.read_bytes() == b"new"
    assert not list(tmp_path.glob(".todo.json-*.tmp"))
