"""No native imports or model calls during ordinary/headless startup."""

import json
import subprocess
import sys
from pathlib import Path


def detect_desktop_capability(settings):
    """Probe availability in a child process without loading native frameworks in the host."""
    if not settings.enabled:
        return {"available": False, "reason": "disabled"}
    if sys.platform != "darwin":
        return {"available": False, "reason": "macOS is required"}
    try:
        command = [sys.executable, str(Path(__file__).with_name("worker.py")), "--probe"]
        if settings.display_id is not None:
            command.extend(["--display", str(settings.display_id)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode:
            return {"available": False, "reason": "desktop dependency/preflight subprocess failed"}
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "desktop preflight unavailable or timed out"}
