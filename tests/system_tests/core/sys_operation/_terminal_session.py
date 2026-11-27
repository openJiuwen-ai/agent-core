# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise shell launches under a private controlling terminal, never the user's tty."""

import asyncio
import errno
import json
import os
import shlex
import signal
import sys
from pathlib import Path


def probe(report: Path) -> None:
    result = {"pid": os.getpid(), "pgid": os.getpgrp(), "sid": os.getsid(0)}
    try:
        terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        result["tty_errno"] = exc.errno
    else:
        os.close(terminal)
        result["tty_errno"] = None
    report.write_text(json.dumps(result), encoding="utf-8")


async def exercise(directory: Path) -> None:
    from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
    from openjiuwen.core.sys_operation import shell_process_registry as registry_module
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation

    shell = ShellOperation("shell", OperationMode.LOCAL, "terminal test", LocalWorkConfig(shell_allowlist=None))
    token = registry_module.set_shell_session_id("terminal-regression")
    try:
        for mode in ("invoke", "stream", "background"):
            report = directory / (mode + ".json")
            command = shlex.join([sys.executable, __file__, "probe", str(report)])
            if mode == "stream":
                # Use an explicit shell: macOS's automatic stream wrapper creates
                # its own PTY by design, independently of the host's terminal.
                stream = shell.execute_cmd_stream(command, shell_type="bash", timeout=5)
                chunks = [item async for item in stream]
                assert chunks[-1].data.exit_code == 0
            elif mode == "background":
                result = await shell.execute_cmd_background(command, grace=0.5)
                assert result.code == 0
            else:
                result = await shell.execute_cmd(command, timeout=5)
                assert result.data.exit_code == 0
            for _ in range(100):
                if report.exists():
                    break
                await asyncio.sleep(0.05)
            data = json.loads(report.read_text(encoding="utf-8"))
            assert data["tty_errno"] in {errno.ENXIO, errno.ENODEV, errno.ENOENT}, data
            assert data["sid"] == os.getsid(0), data
            assert data["pgid"] != os.getpgrp(), data
            assert data["pgid"] != data["pid"], data
            # Detaching a command must not detach the SDK session's terminal.
            terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            os.close(terminal)
    finally:
        registry_module.SHELL_PROCESS_REGISTRY.close()
        registry_module.reset_shell_session_id(token)


def main() -> None:
    import fcntl
    import pty
    import termios

    directory = Path(sys.argv[1])
    assert os.getsid(0) == os.getpid(), "test host must start in its own session"
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(slave, os.getpgrp())
        asyncio.run(exercise(directory))
    finally:
        # Closing our PTY master must not terminate the test host with SIGHUP.
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        os.close(slave)
        os.close(master)


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        probe(Path(sys.argv[2]))
    else:
        main()
