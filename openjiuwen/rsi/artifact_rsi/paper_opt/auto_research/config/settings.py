"""Load pipeline configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv


def load_config(path: str | Path) -> dict[str, Any]:
    load_project_dotenv()
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
