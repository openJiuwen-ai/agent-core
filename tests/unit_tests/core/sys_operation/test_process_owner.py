# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""POSIX group ownership contracts, also runnable on Windows without processes."""

import signal
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
    monkeypatch.setattr(module.Path, "read_text", Mock(side_effect=[
        "10 (name with ) parens) S 1 45 10", "11 (zombie) Z 1 45 10",
        "12 (other group) S 1 46 10", FileNotFoundError(), "14 (live) R 1 45 10",
    ]))
    assert module._posix_members(45) == {10, 14}


def test_macos_group_enumeration_uses_only_numeric_fields(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    query = Mock(return_value="10 45 S\n11 45 Z\n12 46 S\n14 45 R\n")
    monkeypatch.setattr(module.subprocess, "check_output", query)
    assert module._posix_members(45) == {10, 14}
    query.assert_called_once_with(["ps", "-axo", "pid=,pgid=,stat="], text=True, timeout=2)


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_posix_explicit_isolation_opt_out_remains_available(monkeypatch, value):
    monkeypatch.setenv("JW_START_NEW_SESSION", value)
    factory = Mock()
    monkeypatch.setattr(module, "PosixProcessOwner", factory)
    with pytest.MonkeyPatch.context() as local:
        local.setattr(module.os, "name", "posix")
        assert module.create_process_owner() is None
    factory.assert_not_called()
