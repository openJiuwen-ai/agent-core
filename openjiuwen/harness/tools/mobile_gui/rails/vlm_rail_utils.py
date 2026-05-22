# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared ctx.extra keys and compact observation footers for VLM grounding rails."""

from __future__ import annotations

import json
from typing import Any

GOAL_ANCHOR_KEY = "_ephemeral_goal_anchor"
GOAL_ANCHOR_INJECTOR_STATE_KEY = "_goal_anchor_injector_state"

VLM_OBSERVATION_META_EXTRA_KEY = "_vlm_observation_meta"

_VLM_OPEN = "[vlm_meta]"
_VLM_CLOSE = "[/vlm_meta]"


def append_vlm_observation_meta_footer(
    *,
    base_text: str,
    foreground_app: str,
) -> str:
    """Append a compact JSON footer for downstream parsing (archived turns)."""
    meta: dict[str, Any] = {
        "foreground_app": foreground_app,
    }
    return (
        f"{base_text.rstrip()}\n"
        f"{_VLM_OPEN}{json.dumps(meta, ensure_ascii=False)}{_VLM_CLOSE}"
    )
