# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent backend interface.

A backend is the *only* place real non-determinism / IO lives. The engine
hands it a fully-rendered prompt, the call's ``opts``, and (when the call
requested structured output) the JSON-Schema dict; it returns an
:class:`AgentResult`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentResult:
    """What a backend returns for one ``agent()`` call.

    * ``text``       - free text, when no schema was requested.
    * ``structured`` - a JSON-able object conforming to the schema, when one was.
    * ``tokens``     - tokens consumed (drives ``budget.spent()``).
    * ``skipped``    - the backend declined to answer; ``agent()`` returns ``None``.
    """

    text: str | None = None
    structured: Any = None
    tokens: int = 0
    skipped: bool = False


class AgentBackend(abc.ABC):
    """Pluggable agent executor."""

    @abc.abstractmethod
    async def run(
        self, prompt: str, opts: dict, schema_json: dict | None
    ) -> AgentResult:
        """Execute one agent call.

        ``schema_json`` is the JSON-Schema dict when structured output was
        requested (pydantic models are already lowered to JSON Schema by the
        engine), else ``None``.
        """
        raise NotImplementedError
