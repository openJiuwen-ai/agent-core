# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime-owned helper with terminable startup and whole maintenance jobs."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path


def serve(startup_seconds: float) -> None:
    output = sys.stdout
    # Agent imports may log to stdout. Keep them off this private JSON stream.
    sys.stdout = sys.stderr
    deadline = [time.monotonic() + startup_seconds]

    def watchdog():
        while True:
            current = deadline[0]
            if current is not None and time.monotonic() >= current:
                # A stuck filesystem operation cannot defer its own deadline.
                os.kill(os.getpid(), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            time.sleep(0.005 if current is not None else 0.05)

    threading.Thread(target=watchdog, name="pcs-capacity-deadline", daemon=True).start()
    # Editable installs can live in a meta_path finder absent from sys.path.
    # Append this script's package root without shadowing the parent's paths.
    package_root = str(Path(__file__).resolve().parents[3])
    if package_root not in sys.path:
        sys.path.append(package_root)
    from openjiuwen.harness.personal_context import context_capacity as capacity
    from openjiuwen.harness.personal_context._capacity_commit import commit

    def send(message):
        output.write(json.dumps(message) + "\n")
        output.flush()

    def commit_in_process(manifest, *, deadline: float, cancelled):
        if cancelled() or time.monotonic() >= deadline:
            return {"status": "failed", "reason": "time_budget", "removed": []}
        send({"event": "commit_started"})
        overall_deadline = deadline
        # Independently enforce the write window even if the parent's event
        # loop is delayed. The same process owns preparation and both locks.
        job_deadline[0] = min(deadline, time.monotonic() + capacity.COMMIT_LOCK_SECONDS)
        try:
            # No grandchild may retain a publication lock after we are killed.
            plan = json.loads(manifest.read_text(encoding="utf-8"))
            return commit(plan, seconds=min(capacity.COMMIT_LOCK_SECONDS, deadline - time.monotonic()))
        finally:
            job_deadline[0] = overall_deadline
            send({"event": "commit_finished"})

    job_deadline = deadline
    deadline[0] = None  # Idle stdin owns no filesystem or usage/publication lock.
    send({"ready": True})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            deadline[0] = request["deadline"]
            result = capacity.maintain_capacity(
                Path(request["home"]), limit_bytes=request["limit_bytes"], deadline=request["deadline"],
                commit_candidate=commit_in_process,
            )
        except Exception as exc:
            result = {"status": "failed", "reason": type(exc).__name__}
        deadline[0] = None
        send({"result": result})


if __name__ == "__main__":
    sys.path[:] = json.loads(sys.argv[2])
    serve(float(sys.argv[1]))
