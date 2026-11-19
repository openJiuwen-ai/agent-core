# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""POSIX group ownership contracts, also runnable on Windows without processes."""

import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from openjiuwen.core.sys_operation import _process_owner as module


@pytest.fixture
def guardian(monkeypatch):
    child = Mock(pid=4567)
    monkeypatch.setattr(module.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(module.os, "killpg", Mock(), raising=False)
    monkeypatch.setattr(module.signal, "SIGKILL", 9, raising=False)
    return child


def test_posix_group_is_anchored_until_after_signalling(guardian):
    order = Mock()
    order.attach_mock(module.os.killpg, "signal")
    order.attach_mock(guardian.wait, "reap")
    owner = module.PosixProcessOwner()
    assert owner.spawn_options() == {"process_group": guardian.pid}
    owner.attach(6789)
    owner.start()
    owner.close()
    owner.close()
    assert [call[0] for call in order.mock_calls] == ["signal", "reap"]
    module.os.killpg.assert_called_once_with(guardian.pid, signal.SIGKILL)
    guardian.poll.assert_not_called()
    guardian.stdin.close.assert_called_once()


def test_empty_posix_group_requires_stable_observation(guardian, monkeypatch):
    members = Mock(side_effect=[{4567}, {4567, 6789}, {4567}, {4567}])
    monkeypatch.setattr(module, "_posix_members", members)
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 2, 3.1]))
    owner = module.PosixProcessOwner()
    assert not owner.is_empty()
    assert not owner.is_empty()
    assert not owner.is_empty()
    assert owner.is_empty()
    module.os.killpg.assert_not_called()


def test_missing_group_is_reaped_without_signalling_another_pid(guardian):
    owner = module.PosixProcessOwner()
    module.os.killpg.side_effect = ProcessLookupError
    owner.close()
    guardian.wait.assert_called_once_with(timeout=3)
    guardian.poll.assert_not_called()


def test_posix_enumeration_failure_is_not_treated_as_empty(guardian, monkeypatch):
    monkeypatch.setattr(module, "_posix_members", Mock(side_effect=PermissionError("unavailable")))
    with pytest.raises(PermissionError):
        module.PosixProcessOwner().is_empty()
    module.os.killpg.assert_not_called()


def test_linux_procfs_ignores_zombies_and_handles_parentheses(monkeypatch):
    entries = [SimpleNamespace(name=str(pid), path=f"/proc/{pid}") for pid in range(10, 15)]
    scan = MagicMock()
    scan.__enter__.return_value = entries
    monkeypatch.setattr(module.os, "scandir", Mock(return_value=scan))
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.Path, "read_bytes", Mock(side_effect=[
        b"10 (name with ) parens\xff) S 1 45 10", b"11 (zombie) Z 1 45 10",
        b"12 (other group) S 1 46 10", FileNotFoundError(), b"14 (live) R 1 45 10",
    ]))
    assert module._posix_members(45) == {10, 14}


def test_macos_group_enumeration_uses_only_numeric_fields(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    query = Mock(return_value="10 45 S\n11 45 Z\n12 46 S\n14 45 R\n")
    monkeypatch.setattr(module.subprocess, "check_output", query)
    assert module._posix_members(45) == {10, 14}
    query.assert_called_once_with(
        ["/bin/ps", "-axo", "pid=,pgid=,stat="], text=True, encoding="ascii", timeout=2,
    )


@pytest.mark.parametrize("stat", [b"truncated", b"10 (name) S 1 invalid", PermissionError("denied")])
def test_linux_unknown_membership_does_not_produce_an_empty_snapshot(monkeypatch, stat):
    scan = MagicMock()
    scan.__enter__.return_value = [SimpleNamespace(name="10", path="/proc/10")]
    monkeypatch.setattr(module.os, "scandir", Mock(return_value=scan))
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.Path, "read_bytes", Mock(side_effect=[stat]))
    with pytest.raises(OSError):
        module._posix_members(45)


@pytest.mark.parametrize("output", ["", "10 45", "10 invalid S", "invalid 45 S", "10 45 S extra"])
def test_macos_malformed_rows_invalidate_the_snapshot(monkeypatch, output):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "check_output", Mock(return_value=output))
    with pytest.raises(OSError, match="invalid ps output"):
        module._posix_members(45)


def test_macos_decode_failure_invalidates_the_snapshot(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    error = UnicodeDecodeError("ascii", b"\xff", 0, 1, "invalid byte")
    monkeypatch.setattr(module.subprocess, "check_output", Mock(side_effect=error))
    with pytest.raises(OSError, match="invalid ps output"):
        module._posix_members(45)


def test_failed_observation_restarts_the_empty_group_quiet_interval(guardian, monkeypatch):
    members = Mock(side_effect=[{4567}, OSError("scan failed"), {4567}, {4567}])
    monkeypatch.setattr(module, "_posix_members", members)
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 5, 6.1]))
    owner = module.PosixProcessOwner()
    assert not owner.is_empty()
    with pytest.raises(OSError, match="scan failed"):
        owner.is_empty()
    assert not owner.is_empty()
    assert owner.is_empty()


def test_posix_exec_wrapper_preserves_argv_and_uses_a_standalone_helper():
    arguments = ["/bin/sh", "-c", "printf '%s' 'spaces; $HOME'"]
    wrapped = module.wrap_posix_command(arguments)
    assert wrapped[:3] == [module.sys.executable, "-I", "-S"]
    assert Path(wrapped[3]).is_absolute()
    assert Path(wrapped[3]).name == "_posix_command.py"
    assert wrapped[4:] == arguments


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_posix_explicit_isolation_opt_out_remains_available(monkeypatch, value):
    monkeypatch.setenv("JW_START_NEW_SESSION", value)
    factory = Mock()
    monkeypatch.setattr(module, "PosixProcessOwner", factory)
    with pytest.MonkeyPatch.context() as local:
        local.setattr(module.os, "name", "posix")
        assert module.create_process_owner() is None
    factory.assert_not_called()
