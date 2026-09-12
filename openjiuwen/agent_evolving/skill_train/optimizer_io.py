# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared optimizer round-trip plumbing for the ReflACT stages.

Every ReflACT stage (reflect, aggregate, select, slow update, meta skill)
follows the same three beats: assemble a sectioned user message, hand it to
the optimizer role, then decode a JSON object out of the reply.  This module
owns those three beats so each stage only has to describe *what* it wants.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, NamedTuple, Optional

from openjiuwen.agent_evolving.skill_train.llm_client import chat_optimizer
from openjiuwen.agent_evolving.skill_train.utils import extract_json

#: Completion budget for the ordinary edit/patch shaped stages.
COMPACT_TOKEN_BUDGET = 16384
#: Completion budget for stages that emit whole replacement skill documents.
VERBOSE_TOKEN_BUDGET = 64000

SECTION_GAP = "\n\n"


def token_budget(whole_document: bool) -> int:
    """Pick the completion budget matching the size of the expected reply."""
    return VERBOSE_TOKEN_BUDGET if whole_document else COMPACT_TOKEN_BUDGET


@dataclass
class UserMessage:
    """Incremental builder for ``## Section`` shaped optimizer user messages."""

    blocks: list[str] = field(default_factory=list)

    def section(self, heading: str, body: str) -> "UserMessage":
        """Append a ``## heading`` block whose payload is *body*."""
        self.blocks.append(f"## {heading}\n{body}")
        return self

    def verbatim(self, text: str) -> "UserMessage":
        """Append an already-formatted block, skipping empty ones."""
        if text:
            self.blocks.append(text)
        return self

    def prepend(self, text: str) -> "UserMessage":
        """Insert an already-formatted block ahead of everything else."""
        if text:
            self.blocks.insert(0, text)
        return self

    def render(self) -> str:
        """Join the accumulated blocks with a blank line between them."""
        return SECTION_GAP.join(self.blocks)


@dataclass(frozen=True)
class OptimizerCall:
    """One optimizer round trip, fully described."""

    stage: str
    system: str
    user: str
    max_tokens: int = COMPACT_TOKEN_BUDGET
    retries: int = 3


class OptimizerReply(NamedTuple):
    """Decoded optimizer answer plus whether the round trip itself blew up."""

    body: Optional[Dict[str, Any]]
    crashed: bool

    def field_list(self, key: str) -> Optional[list]:
        """Return ``body[key]`` when it is a list of dicts, else ``None``."""
        if not isinstance(self.body, dict):
            return None
        value = self.body.get(key)
        if isinstance(value, list) and all(isinstance(entry, dict) for entry in value):
            return value
        return None


def ask_optimizer(call: OptimizerCall, *, trace: bool = False) -> OptimizerReply:
    """Issue *call* and decode its JSON body.

    Provider errors and undecodable replies are both non-fatal: the caller
    inspects :attr:`OptimizerReply.body` and falls back on its own terms.
    ``crashed`` distinguishes a transport/parse explosion from a well-formed
    but unusable answer, which some stages report differently.
    """
    try:
        reply, _usage = chat_optimizer(
            system=call.system,
            user=call.user,
            max_completion_tokens=call.max_tokens,
            retries=call.retries,
            stage=call.stage,
        )
        return OptimizerReply(extract_json(reply), False)
    except Exception:  # noqa: BLE001 - stages degrade instead of aborting a run
        if trace:
            traceback.print_exc()
        return OptimizerReply(None, True)
