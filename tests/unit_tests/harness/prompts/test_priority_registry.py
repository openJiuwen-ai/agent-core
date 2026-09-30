# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for opt-in prompt priority policies."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent.prompts.builder import (
    PromptSection,
    SystemPromptBuilder,
)


class _PriorityRegistry:
    def __init__(self) -> None:
        self._priorities = {"known": 10}

    def priority_for(self, section_name: str, fallback_priority: int) -> int:
        return self._priorities.get(section_name, fallback_priority)

    def names_for_priority(self, priority: int) -> tuple[str, ...]:
        return tuple(
            name for name, value in self._priorities.items() if value == priority
        )


class _MutablePriorityRegistry(_PriorityRegistry):
    """Small host-style registry used to exercise builder lifecycle hooks."""

    def __init__(self) -> None:
        super().__init__()
        self._runtime_priorities: dict[str, int] = {}

    def priority_for(self, section_name: str, fallback_priority: int) -> int:
        return self._runtime_priorities.get(
            section_name,
            super().priority_for(section_name, fallback_priority),
        )

    def names_for_priority(self, priority: int) -> tuple[str, ...]:
        priorities = dict(self._priorities)
        priorities.update(self._runtime_priorities)
        return tuple(
            sorted(name for name, value in priorities.items() if value == priority)
        )

    def register_section(self, section_name: str, priority: int) -> None:
        if section_name in self._priorities:
            return
        normalized_priority = int(priority)
        previous_priority = self._runtime_priorities.get(section_name)
        if previous_priority is not None and previous_priority != normalized_priority:
            raise ValueError("section priority changed")
        self._runtime_priorities[section_name] = normalized_priority

    def unregister_section(self, section_name: str) -> None:
        if section_name not in self._priorities:
            self._runtime_priorities.pop(section_name, None)


def _section(name: str, priority: int, content: str) -> PromptSection:
    return PromptSection(name=name, content={"cn": content}, priority=priority)


def test_priority_registry_remaps_known_sections_and_breaks_ties_by_name() -> None:
    builder = SystemPromptBuilder()
    builder.set_priority_registry(_PriorityRegistry())

    with patch(
        "openjiuwen.core.single_agent.prompts.builder.logger.warning"
    ) as warning:
        builder.add_section(_section("known", 999, "known"))
        builder.add_section(_section("aaa", 10, "aaa"))

    assert builder.get_effective_priority(builder.get_section("known")) == 10
    assert builder.build() == "aaa\n\nknown"
    warning.assert_called_once()


def test_priority_registry_warning_does_not_reject_duplicate_priority() -> None:
    builder = SystemPromptBuilder()
    builder.set_priority_registry(_PriorityRegistry())

    with patch(
        "openjiuwen.core.single_agent.prompts.builder.logger.warning"
    ) as warning:
        builder.add_section(_section("known", 10, "known"))
        builder.add_section(_section("other", 10, "other"))

    assert builder.has_section("known")
    assert builder.has_section("other")
    assert builder.build() == "known\n\nother"
    warning.assert_called_once()


def test_without_registry_historical_insertion_order_is_preserved() -> None:
    builder = SystemPromptBuilder()
    builder.add_section(_section("z", 10, "z"))
    builder.add_section(_section("a", 10, "a"))

    assert builder.build() == "z\n\na"


def test_builder_registers_and_unregisters_runtime_sections() -> None:
    builder = SystemPromptBuilder()
    registry = _MutablePriorityRegistry()
    builder.set_priority_registry(registry)

    builder.add_section(_section("runtime_only", 120, "runtime"))
    assert registry.priority_for("runtime_only", 999) == 120

    builder.remove_section("runtime_only")
    assert registry.priority_for("runtime_only", 999) == 999


def test_setting_registry_registers_sections_already_in_the_builder() -> None:
    builder = SystemPromptBuilder()
    builder.add_section(_section("already_present", 120, "present"))
    registry = _MutablePriorityRegistry()

    builder.set_priority_registry(registry)

    assert registry.priority_for("already_present", 999) == 120


def test_builder_rejects_a_changed_priority_for_an_existing_section_name() -> None:
    builder = SystemPromptBuilder()
    builder.set_priority_registry(_MutablePriorityRegistry())
    builder.add_section(_section("stable_name", 120, "first"))

    with pytest.raises(ValueError):
        builder.add_section(_section("stable_name", 121, "second"))

    assert builder.get_section("stable_name").render() == "first"


def test_runtime_priority_collision_warns_but_keeps_both_sections() -> None:
    builder = SystemPromptBuilder()
    builder.set_priority_registry(_MutablePriorityRegistry())

    with patch(
        "openjiuwen.core.single_agent.prompts.builder.logger.warning"
    ) as warning:
        builder.add_section(_section("runtime_a", 120, "a"))
        builder.add_section(_section("runtime_b", 120, "b"))

    assert builder.build() == "a\n\nb"
    warning.assert_called_once()
