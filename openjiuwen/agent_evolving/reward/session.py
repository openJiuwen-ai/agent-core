"""Where JiuwenSwarm keeps its sessions: used to find the session's model ("session" in the config) and where the
progress card file goes. Reward reads no trajectory.
"""

import os
from pathlib import Path


def sessions_root():
    home = Path(os.environ.get("JIUWENSWARM_HOME") or Path.home() / ".jiuwenswarm")
    return home / "agent" / "sessions"


def latest_session_history():
    """The JiuwenSwarm session written most recently: the one running this evaluation."""
    files = sorted(sessions_root().glob("*/history.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"no JiuwenSwarm session history under {sessions_root()}")
    return files[0]
