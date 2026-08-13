#!/usr/bin/env python3
# coding: utf-8

"""Skill-local entrypoint for direct supervisor SFT rollouts."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_CORE_ROOT = Path(__file__).resolve().parents[5]
if str(AGENT_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE_ROOT))

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.optimize_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
