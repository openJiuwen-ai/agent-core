# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

_STATE_ATTR = "_qa_artifact_assembly_state"
_WHOLE_COMPACT_KEY = "whole_compact_applied"


def _get_state(context: Any) -> dict[str, Any]:
    state = getattr(context, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {}
        setattr(context, _STATE_ATTR, state)
    return state


def clear_assembly_qa_artifact_state(context: Any) -> None:
    """Reset per-invoke assembly markers. Host assembly rail must call each new QA round."""
    setattr(context, _STATE_ATTR, {})


def mark_assembly_whole_compact_applied(context: Any) -> None:
    """Set whole-window compact flag for this invoke only (cleared on next assembly)."""
    _get_state(context)[_WHOLE_COMPACT_KEY] = True


def is_assembly_whole_compact_applied(context: Any) -> bool:
    return bool(_get_state(context).get(_WHOLE_COMPACT_KEY))
