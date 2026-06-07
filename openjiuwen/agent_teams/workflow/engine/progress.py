# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured progress events emitted by the engine during a run.

This is the engine's only observability seam beyond the plain-text ``log_sink``.
The ``phase()`` / ``log()`` primitives and the ``agent()`` start/end hooks emit
:class:`WorkflowProgressEvent` to ``Runtime.progress_sink``; an embedder
(``workflow/observer.py``) consumes them to (a) drive the leader's spectator
broadcast and (b) accumulate the 4-layer ``WorkflowRun`` structure.

The event is deliberately **business-agnostic and timestamp-free**: the engine
forbids wall-clock reads (they break deterministic resume — see ``loader``'s
determinism lint), so the embedder stamps time when it consumes an event, not
the engine when it emits one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class ProgressKind:
    """The ``kind`` discriminator on :class:`WorkflowProgressEvent`."""

    WORKFLOW_STARTED = "workflow_started"
    PHASE = "phase"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    LOG = "log"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"


@dataclass(frozen=True, slots=True)
class WorkflowProgressEvent:
    """One structured progress signal from a running workflow.

    Fields are populated per ``kind``:

    * ``phase``               — set by ``PHASE`` (the new phase title) and echoed
      on ``AGENT_STARTED`` / ``AGENT_COMPLETED`` / ``AGENT_FAILED`` so a consumer
      can group agents under their phase without tracking state.
    * ``label``               — the ``agent()`` call's label (``AGENT_*``).
    * ``prompt``              — the agent's rendered prompt (``AGENT_STARTED``).
    * ``model``               — the ``agent(model=...)`` name hint (``AGENT_STARTED``).
    * ``outcome``             — a short preview of the agent's result
      (``AGENT_COMPLETED``); absent on ``AGENT_FAILED`` (use ``message`` instead).
    * ``message``             — free narration text (``LOG``); also carries the
      workflow name on ``WORKFLOW_STARTED`` / ``WORKFLOW_COMPLETED``, and the
      error description on ``WORKFLOW_FAILED`` / ``AGENT_FAILED``.
    * ``phases``              — the static phase plan from the script's ``META``
      dict (``WORKFLOW_STARTED``); ``None`` on all other kinds.
    """

    kind: str
    phase: str | None = None
    label: str | None = None
    prompt: str | None = None
    model: str | None = None
    outcome: str | None = None
    message: str | None = None
    phases: list[Any] | None = None


#: Signature of ``Runtime.progress_sink``. Default is a no-op so the engine has
#: zero observability dependency; embedders inject a real sink.
ProgressSink = Callable[[WorkflowProgressEvent], None]


def noop_progress_sink(event: WorkflowProgressEvent) -> None:
    """Default ``progress_sink``: drop the event. Embedders override this."""
    return None
