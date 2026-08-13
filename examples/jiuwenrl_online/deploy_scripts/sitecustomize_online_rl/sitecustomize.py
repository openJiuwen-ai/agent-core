# coding: utf-8
"""Auto-apply agent-core online-RL JiuwenSwarm runtime patches."""

from __future__ import annotations

import os

if os.getenv("JIUWENSWARM_LIGHT_PROFILE", "").strip().lower() in {"1", "true", "yes", "on"}:
    try:
        import openjiuwen.agent_evolving.agent_rl.online.jiuwenswarm_light_patch  # noqa: F401
    except Exception:
        pass
