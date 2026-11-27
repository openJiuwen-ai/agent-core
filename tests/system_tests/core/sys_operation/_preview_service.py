# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded local preview service for process ownership regression tests."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def serve(lease: Path) -> None:
    timer = threading.Timer(60, lambda: os._exit(0))
    timer.daemon = True
    timer.start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"preview is running")
            if self.path == "/stop/" + lease.stem:
                self.server.stopped = True

        def log_message(self, format, *args):
            return

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.1
        server.stopped = False
        lease.write_text(json.dumps({"port": server.server_port, "pid": os.getpid()}), encoding="utf-8")
        while not server.stopped:
            server.handle_request()


async def sdk_host(lease: Path, mode: str) -> None:
    from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation

    argv = [sys.executable, __file__, "launch", str(lease)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    shell = ShellOperation("shell", OperationMode.LOCAL, "SDK exit test", LocalWorkConfig(shell_allowlist=None))
    result = await shell.execute_cmd(command)
    if result.code != 0 or result.data.exit_code != 0:
        raise RuntimeError("preview launch failed")
    if mode == "sdk-hold":
        await asyncio.sleep(60)


if __name__ == "__main__":
    mode, filename = sys.argv[1:3]
    lease_path = Path(filename)
    if mode == "server":
        serve(lease_path)
    elif mode.startswith("sdk-"):
        asyncio.run(sdk_host(lease_path, mode))
    else:
        subprocess.Popen(
            [sys.executable, __file__, "server", filename],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while not lease_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not lease_path.exists():
            raise RuntimeError("preview was not ready")
        print("preview launched", flush=True)
        if mode == "hold":
            time.sleep(60)
        if mode == "fail":
            raise SystemExit(7)
