# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Terminal detachment contracts, without requiring a POSIX host or a real tty."""

import errno
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.sys_operation import _posix_command as module


@pytest.fixture
def terminal(monkeypatch):
    ioctl = Mock()
    monkeypatch.setitem(module.sys.modules, "fcntl", SimpleNamespace(ioctl=ioctl))
    monkeypatch.setitem(module.sys.modules, "termios", SimpleNamespace(TIOCNOTTY=123))
    monkeypatch.setattr(module.os, "O_NOCTTY", 0x100, raising=False)
    monkeypatch.setattr(module.os, "O_NONBLOCK", 0x800, raising=False)
    monkeypatch.setattr(module.os, "open", Mock(return_value=7))
    monkeypatch.setattr(module.os, "close", Mock())
    monkeypatch.setattr(module.os, "getsid", Mock(return_value=10), raising=False)
    monkeypatch.setattr(module.os, "getpid", Mock(return_value=20))
    monkeypatch.setattr(module.os, "execv", Mock())
    monkeypatch.setattr(module.signal, "signal", Mock())
    return ioctl


def test_detaches_only_the_command_and_closes_the_terminal(terminal):
    module.detach_terminal()
    module.os.open.assert_called_once_with("/dev/tty", module.os.O_RDWR | 0x100 | 0x800)
    terminal.assert_called_once_with(7, 123)
    module.os.close.assert_called_once_with(7)


@pytest.mark.parametrize("code", [errno.ENXIO, errno.ENODEV, errno.ENOENT])
def test_no_controlling_terminal_needs_no_detach(terminal, code):
    module.os.open.side_effect = OSError(code, "no terminal")
    module.detach_terminal()
    terminal.assert_not_called()
    module.os.close.assert_not_called()


def test_unexpected_open_failure_does_not_execute_the_command(terminal):
    module.os.open.side_effect = PermissionError(errno.EACCES, "denied")
    with pytest.raises(PermissionError):
        module.main()
    module.os.execv.assert_not_called()


def test_failed_detach_closes_descriptor_without_executing(terminal):
    terminal.side_effect = OSError("detach failed")
    with pytest.raises(OSError, match="detach failed"):
        module.main()
    module.os.close.assert_called_once_with(7)
    module.os.execv.assert_not_called()


def test_session_leader_must_not_detach_the_hosts_terminal(terminal):
    module.os.getsid.return_value = 20
    with pytest.raises(OSError, match="session leader"):
        module.main()
    terminal.assert_not_called()
    module.os.close.assert_called_once_with(7)
    module.os.execv.assert_not_called()


def test_exec_replaces_helper_with_the_original_arguments(terminal, monkeypatch):
    arguments = ["/bin/sh", "-c", "echo 'literal $HOME ; text'"]
    monkeypatch.setattr(module.sys, "argv", ["helper.py", *arguments])
    order = Mock()
    order.attach_mock(terminal, "detach")
    order.attach_mock(module.os.close, "close")
    order.attach_mock(module.os.execv, "exec")
    module.main()
    assert [item[0] for item in order.mock_calls] == ["detach", "close", "exec"]
    module.os.execv.assert_called_once_with(arguments[0], arguments)


def test_exec_restores_signals_ignored_by_python_startup(terminal, monkeypatch):
    monkeypatch.setattr(module.sys, "argv", ["helper.py", "/bin/sh", "-c", "echo hi"])
    monkeypatch.setattr(module.signal, "SIGPIPE", 1234, raising=False)
    module.main()
    module.signal.signal.assert_any_call(1234, module.signal.SIG_DFL)
