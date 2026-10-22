"""Run configuration: models, time limits, rubric budget, guides and evidence output.

Looked up in this order: an explicit path, $REWARD_CONFIG, ~/.reward/config.json (or $REWARD_HOME),
else DEFAULT_CONFIG. "session" model entries are resolved to the model the JiuwenSwarm session runs on.
"""

import json
import os
from pathlib import Path

from .models import resolve_models

DEFAULT_CONFIG = {
    "producer": {"model": "session", "timeout": 360},   # writes criteria and places notes/signals
    "judge": {"model": "session", "timeout": 360},      # judges claims, writes the comments
    "criteria_budget": 3,                               # terminal criteria besides "quality"
}


def config_path(path=None):
    home = Path(os.environ.get("REWARD_HOME") or Path.home() / ".reward")
    return Path(path or os.environ.get("REWARD_CONFIG") or home / "config.json")


def load_config(path=None):
    """The config dict with model entries resolved; DEFAULT_CONFIG when no file exists and none was named."""
    file = config_path(path)
    if file.is_file():
        config = json.loads(file.read_text(encoding="utf-8"))
    elif path or os.environ.get("REWARD_CONFIG"):
        raise FileNotFoundError(f"config file not found: {file}")
    else:
        config = DEFAULT_CONFIG
    return resolve_models(config)
