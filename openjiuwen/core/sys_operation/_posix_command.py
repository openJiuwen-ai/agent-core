# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Standalone POSIX exec helper; must not import or initialize the SDK."""

import errno
import os
import signal
import sys


def detach_terminal() -> None:
    """Drop only this non-session-leader's controlling terminal."""
    import fcntl
    import termios

    try:
        terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK, mode=0o600)
    except OSError as exc:
        if exc.errno not in {errno.ENXIO, errno.ENODEV, errno.ENOENT}:
            raise
        return  # The process has no usable controlling terminal.
    try:
        # TIOCNOTTY on a session leader would also detach the host's terminal.
        if os.getsid(0) == os.getpid():
            raise OSError("Refusing to detach a session leader's controlling terminal")
        fcntl.ioctl(terminal, termios.TIOCNOTTY)
    finally:
        os.close(terminal)


def main() -> None:
    detach_terminal()
    # Match Popen(restore_signals=True) after this interpreter's startup.
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, signal.SIG_DFL)
    arguments = sys.argv[1:]
    os.execv(arguments[0], arguments)


if __name__ == "__main__":
    main()
