# coding: utf-8

from __future__ import annotations

import os
import sys
from pathlib import Path


ST_DIR = Path(__file__).resolve().parent
AGENT_CORE_ROOT = ST_DIR.parents[2]
WORKSPACE_ROOT = AGENT_CORE_ROOT.parent
JIUWENCLAW_ROOT = WORKSPACE_ROOT / "jiuwenclaw"

for path in (AGENT_CORE_ROOT, JIUWENCLAW_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def pytest_configure(config):
    os.environ.setdefault("PYTHONHASHSEED", "20260713")
    config.addinivalue_line("markers", "a5_precision: A5/GPU online RL precision ST")
    config.addinivalue_line("markers", "training: expensive direct PPO training ST")
