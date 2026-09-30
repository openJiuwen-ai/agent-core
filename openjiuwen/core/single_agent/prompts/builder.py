# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Section-based system prompt builder."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger


SUPPORTED_LANGUAGES: tuple[str, ...] = ("cn", "en")
"""All languages the prompt system supports.

Add new language codes here to extend multilingual coverage.
Every ToolMetadataProvider, PromptSection, and i18n dict
must provide content for each language in this tuple.
"""

DEFAULT_LANGUAGE: str = "cn"
"""Fallback language when no explicit choice is made."""


class PromptSection:
    """A single prompt section with multilingual content."""

    def __init__(
        self,
        name: str,
        content: Dict[str, str],
        priority: int = 100,
        category: Optional[str] = None,
        carrier: str = "system_message",
    ):
        self.name = name
        self.content: Dict[str, str] = dict(content)
        self.priority = priority
        self.category = category
        self.carrier = carrier

    def render(self, language: str = "cn") -> str:
        if language in self.content:
            return self.content[language]
        return self.content.get(
            DEFAULT_LANGUAGE,
            next(iter(self.content.values()), ""),
        )

    def char_count(self, language: str = "cn") -> int:
        return len(self.render(language))


class SystemPromptBuilder:
    """Section-based system prompt builder base class.

    This class only provides generic section registration and rendering.
    Agent-family-specific prompt policies, such as mode switching or prompt
    diagnostics, should live in subclasses outside the single-agent layer.
    """

    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
    ):
        self.language = language
        self._sections: Dict[str, PromptSection] = {}
        # Optional host-supplied priority policy.  It is deliberately opt-in
        # so applications such as JiuwenSwarm can migrate one execution mode
        # without changing the ordering semantics of other modes.
        self._priority_registry: Any = None
        self._reported_priority_collisions: set[tuple[int, tuple[str, ...]]] = set()

    @property
    def priority_registry(self) -> Any:
        """Return the optional host-supplied prompt priority registry."""
        return self._priority_registry

    def set_priority_registry(self, registry: Any = None) -> "SystemPromptBuilder":
        """Set an optional priority registry used by this builder.

        A registry is expected to provide ``priority_for(name, fallback)`` and
        may provide ``names_for_priority(priority)`` for collision diagnostics.
        It may also provide ``register_section(name, priority)`` and
        ``unregister_section(name)`` so runtime section lifecycles can update
        the host-owned registry automatically.
        The interface is intentionally duck-typed so the core builder does not
        depend on a particular host application's policy module.
        """
        self._priority_registry = registry
        self._reported_priority_collisions.clear()
        if registry is not None:
            for section in self._sections.values():
                self._register_section_priority(section)
        self._warn_priority_collisions()
        return self

    def add_section(self, section: PromptSection) -> "SystemPromptBuilder":
        """Add or replace a section.

        When a priority registry is active, replacing an existing section
        name with a different priority is rejected.
        """
        previous_section = self._sections.get(section.name)
        if (
            self._priority_registry is not None
            and previous_section is not None
            and int(previous_section.priority) != int(section.priority)
        ):
            raise ValueError(
                "Prompt section %r is already registered with priority %s; "
                "cannot replace it with priority %s"
                % (section.name, previous_section.priority, section.priority)
            )
        self._register_section_priority(section)
        self._sections[section.name] = section
        self._warn_priority_collisions()
        return self

    def remove_section(self, name: str) -> "SystemPromptBuilder":
        """Remove a section by name."""
        removed_section = self._sections.pop(name, None)
        if removed_section is None:
            return self

        registry = self._priority_registry
        unregister = (
            getattr(registry, "unregister_section", None)
            if registry is not None
            else None
        )
        if callable(unregister):
            unregister(name)

        # A removed section must not suppress a future warning if it is
        # registered again with the same priority/name combination.
        section_name = str(name)
        self._reported_priority_collisions = {
            collision
            for collision in self._reported_priority_collisions
            if section_name not in collision[1]
        }
        return self

    def get_all_sections(self) -> Dict[str, "PromptSection"]:
        """Return a copy of all registered sections."""
        return dict(self._sections)

    def has_section(self, name: str) -> bool:
        return name in self._sections

    def get_section(self, name: str) -> Optional[PromptSection]:
        return self._sections.get(name)

    def _register_section_priority(self, section: PromptSection) -> None:
        """Register a section with the optional host priority policy."""
        registry = self._priority_registry
        register = (
            getattr(registry, "register_section", None)
            if registry is not None
            else None
        )
        if callable(register):
            register(section.name, section.priority)

    def get_effective_priority(self, section: PromptSection) -> int:
        """Return the priority used for ordering this section."""
        registry = self._priority_registry
        if registry is None:
            return int(section.priority)

        resolver = getattr(registry, "priority_for", None)
        if callable(resolver):
            return int(resolver(section.name, section.priority))
        return int(section.priority)

    def get_section_sort_key(self, section: PromptSection) -> tuple:
        """Return the deterministic sort key for a registered section.

        The section name is used as a tie-breaker only when a priority registry
        is active.  Without a registry, preserve the historical stable-sort
        behavior for callers that have not opted into the policy.
        """
        effective_priority = self.get_effective_priority(section)
        if self._priority_registry is None:
            return (effective_priority,)
        return (effective_priority, str(section.name))

    def _warn_priority_collisions(self) -> None:
        """Warn about duplicate effective priorities without rejecting them."""
        registry = self._priority_registry
        if registry is None or not self._sections:
            return

        names_for_priority = getattr(registry, "names_for_priority", None)
        by_priority: Dict[int, set[str]] = {}
        for section in self._sections.values():
            priority = self.get_effective_priority(section)
            names = by_priority.setdefault(priority, set())
            names.add(str(section.name))
            if callable(names_for_priority):
                names.update(
                    str(name) for name in (names_for_priority(priority) or ())
                )

        for priority, names in by_priority.items():
            if len(names) < 2:
                continue
            collision_key = (priority, tuple(sorted(names)))
            if collision_key in self._reported_priority_collisions:
                continue
            self._reported_priority_collisions.add(collision_key)
            logger.warning(
                "[PromptPriority] duplicate system-prompt priority=%s for sections=%s; "
                "execution continues and section name is used as the deterministic tie-breaker",
                priority,
                ", ".join(collision_key[1]),
            )

    def build(self) -> str:
        """Sort current sections by priority and join them into one prompt.

        Safe to call multiple times. Each call produces a complete
        prompt from the current state of all registered sections.
        """
        sections = self._get_sections_for_build()
        sorted_sections = sorted(sections, key=self.get_section_sort_key)
        parts = [s.render(self.language) for s in sorted_sections]
        return "\n\n".join(part for part in parts if part.strip())

    def _get_sections_for_build(self) -> List[PromptSection]:
        """Return the sections that should participate in the final build."""
        return list(self._sections.values())
