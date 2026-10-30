# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared tool-call exemption rule for RAS repeat detection and context compact."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

WRAPPER_TOOL_NAMES = frozenset({"invoke_tool"})


class IgnoredToolCallRule(BaseModel):
    """Match an outer tool and selected top-level argument values exactly.

    Empty ``argument_equals`` means name-only match. Forbidden for wrapper
    tools such as ``invoke_tool`` (would exempt every lazy-loaded target).
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    argument_equals: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_empty_equals_on_wrappers(self) -> "IgnoredToolCallRule":
        if not self.argument_equals and self.tool_name in WRAPPER_TOOL_NAMES:
            raise ValueError(
                f"argument_equals cannot be empty for wrapper tool "
                f"{self.tool_name!r}; name-only exemption would skip all "
                f"lazy-loaded tools"
            )
        return self


def matches_ignored_tool_call(
    tool_name: str,
    args: Mapping[str, Any],
    rules: Sequence[IgnoredToolCallRule],
) -> bool:
    """Return True when ``tool_name``+``args`` match any exemption rule.

    Missing keys never match a rule value (including ``None``): matching uses
    ``key in args and args[key] == value``, not ``args.get(key) == value``.
    Empty ``argument_equals`` matches by tool name only.
    """
    for rule in rules:
        if rule.tool_name != tool_name:
            continue
        if not rule.argument_equals:
            return True
        if all(
            key in args and args[key] == value
            for key, value in rule.argument_equals.items()
        ):
            return True
    return False
