# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve runtime turn identities without creating user-message boundaries."""

from collections.abc import Mapping, Sequence
from typing import Any

from openjiuwen.agent_evolving.trajectory.spans import span_attributes, span_identity


class TurnResolver:
    """Index one subject's spans; explicit identity and ancestors beat trace hints."""

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self.spans = {}
        self.trace_turns: dict[str, set[tuple[str, int | None]]] = {}
        self.numbers: dict[str, set[int]] = {}
        self.turn_numbers: dict[str, set[int]] = {}
        for span in records:
            identity = span_identity(span)
            if identity is None:
                continue
            self.spans[identity] = span
            attrs = span_attributes(span)
            turn_id = attrs.get("openjiuwen.turn.id")
            raw_number = attrs.get("openjiuwen.turn.number")
            try:
                number = int(raw_number) if raw_number is not None else None
            except (ValueError, TypeError):
                number = None
            if isinstance(raw_number, bool) or (number is not None and number < 1):
                number = None
            if turn_id:
                self.trace_turns.setdefault(identity[0], set()).add((str(turn_id), number))
                if number is not None:
                    self.turn_numbers.setdefault(str(turn_id), set()).add(number)
            elif number is not None:
                self.numbers.setdefault(identity[0], set()).add(number)

    def resolve(self, trace_id: str | None, span_id: str | None) -> tuple[str | None, int | None]:
        """Return a known turn, or None for ambiguous/unlabelled historical data."""
        current = span_id
        visited = set()
        while current and current not in visited:
            visited.add(current)
            span = self.spans.get((trace_id, current))
            if span is None:
                break
            attrs = span_attributes(span)
            turn_id = attrs.get("openjiuwen.turn.id")
            if turn_id:
                numbers = self.turn_numbers.get(str(turn_id), set())
                return str(turn_id), next(iter(numbers)) if len(numbers) == 1 else None
            current = span.get("parentSpanId")
        turns = self.trace_turns.get(trace_id, set())
        ids = {tid for tid, _ in turns}
        if len(ids) == 1:
            turn_id = next(iter(ids))
            numbers = self.turn_numbers.get(turn_id, set())
            return turn_id, next(iter(numbers)) if len(numbers) == 1 else None
        if not ids and len(self.numbers.get(trace_id, ())) == 1:
            number = next(iter(self.numbers[trace_id]))
            return f"legacy:{number}", number
        return None, None
