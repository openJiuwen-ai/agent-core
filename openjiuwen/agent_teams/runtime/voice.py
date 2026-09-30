# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Voice barge-in switches for the team runtime.

A voice barge-in pauses the team the moment the user starts speaking and the
frontend holds the transcribed command until that pause finishes, so the pause
must be fast and must never drop a run cycle that has not started yet. These
behaviors are opt-in per call (``voice=True``); every other caller keeps the
default pause / interact / teardown semantics.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Iterator

_voice_pause: ContextVar[bool] = ContextVar("agent_teams_voice_pause", default=False)


def is_voice_pause() -> bool:
    """Whether the current task is running a voice barge-in pause."""
    return _voice_pause.get()


@contextlib.contextmanager
def voice_pause_scope() -> Iterator[None]:
    """Mark the awaited pause chain as a voice barge-in pause."""
    token = _voice_pause.set(True)
    try:
        yield
    finally:
        _voice_pause.reset(token)
