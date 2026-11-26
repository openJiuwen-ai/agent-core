# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real disposable worker injection, retaining the production JSON protocol."""

import asyncio
import subprocess
from pathlib import Path

from openjiuwen.harness.personal_context import context_capacity as capacity


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def inject_worker(monkeypatch, tmp_path, body):
    script = tmp_path / "injected_capacity_worker.py"
    module = Path(capacity.__file__).with_name("_capacity_worker.py")
    script.write_text(
        "import sys, time, os, runpy, json\nfrom pathlib import Path\n"
        "sys.path[:] = json.loads(sys.argv[2])\n"
        "protocol = sys.stdout\nsys.stdout = sys.stderr\n"
        f"sys.path.insert(0, {str(module.parents[3])!r})\n"
        "from openjiuwen.harness.personal_context import context_capacity as capacity\n"
        + body + "\n"
        f"sys.stdout = protocol\nrunpy.run_path({str(module)!r})['serve'](float(sys.argv[1]))\n",
        encoding="utf-8",
    )
    real_launch = subprocess.Popen

    def launch(args, **kwargs):
        # Replace only the script argument, not process creation or protocol IO.
        return real_launch([*args[:3], str(script), *args[4:]], **kwargs)

    monkeypatch.setattr(capacity.subprocess, "Popen", launch)


async def ready_worker(home):
    worker = capacity._warm_capacity_worker(home)
    assert worker is not None
    ready = await asyncio.wait_for(asyncio.shield(worker["ready"]), 20)
    assert ready == {"ready": True}, ready
    return worker
