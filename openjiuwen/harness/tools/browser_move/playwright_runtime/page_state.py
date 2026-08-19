#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compact browser page state and generation-scoped target registry."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional


_AX_REF_LINE_RE = re.compile(
    r"^\s*-\s+(?P<role>[A-Za-z][\w-]*)"
    r'(?:\s+"(?P<name>[^"]*)")?.*?\[ref=(?P<ref>[A-Za-z0-9_.:-]+)\]',
    re.MULTILINE,
)
_ANY_REF_RE = re.compile(r"\bref\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_BLOCKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("captcha", re.compile(r"captcha|验证码|人机验证", re.IGNORECASE)),
    (
        "login_required",
        re.compile(
            r"login required|sign[ -]?in required|please (?:log|sign) in|请先?登录|需要登录",
            re.IGNORECASE,
        ),
    ),
    ("access_denied", re.compile(r"access denied|forbidden|访问被拒绝", re.IGNORECASE)),
)
_CARD_FIELD_NAMES = (
    "title",
    "price",
    "rating",
    "review_count",
    "availability",
    "author",
    "source",
    "summary",
    "primary_link",
    "href",
)
_MAX_PUBLIC_INTERACTIVES = 40
_MAX_PUBLIC_CARDS = 20
_MAX_TARGET_HISTORY = 1024


def _compact_text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _generation_number(generation_id: str) -> int:
    normalized = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"g\d+", normalized):
        raise ValueError("generation_id must use the PageState generation format, for example g3")
    return int(normalized[1:])


@dataclass
class BrowserTarget:
    """One model-visible target bound to a single page generation."""

    target_id: str
    page_id: str
    generation: int
    source: str
    locator: Dict[str, str] = field(default_factory=dict)
    ref: str = ""
    selector: str = ""
    href: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    visible: bool = True
    enabled: bool = True
    actionable: bool = False
    clickable: bool = False

    @property
    def generation_id(self) -> str:
        """Return the target's generation in the public PageState format."""
        return f"g{self.generation}"

    def compact_index(self) -> Dict[str, Any]:
        """Return the small PageState index entry shown to the model."""
        result: Dict[str, Any] = {
            "target_id": self.target_id,
            "generation_id": self.generation_id,
        }
        if self.ref:
            result["ref"] = self.ref
        if self.href:
            result["href"] = self.href
        if self.role:
            result["role"] = self.role
        if self.text:
            result["text"] = _compact_text(self.text, 80)
        if self.clickable:
            result["clickable"] = True
        return result


class BrowserPageState:
    """Own page identity, compact representation, and target generations."""

    def __init__(
        self,
        *,
        page_id: Optional[str] = None,
        generation: int = 0,
    ) -> None:
        self.page_id = page_id or f"page-{uuid.uuid4().hex[:12]}"
        self.generation = max(0, int(generation))
        self.url = ""
        self.title = ""
        self.field_coverage: set[str] = set()
        self.blockers: set[str] = set()
        self.reference_generations: Dict[str, int] = {}
        self.selector_generations: Dict[str, int] = {}
        self.selector_primary_links: Dict[str, tuple[int, str]] = {}
        self._targets: Dict[str, BrowserTarget] = {}
        self._ref_targets: Dict[str, str] = {}
        self._selector_targets: Dict[tuple[int, str], str] = {}
        self._interactive_target_ids: list[str] = []
        self._cards: list[Dict[str, Any]] = []
        self._target_counter = 0

    @property
    def generation_id(self) -> str:
        """Return the current generation in the public PageState format."""
        return f"g{self.generation}"

    def advance(self, *, url: str = "", title: str = "") -> None:
        """Start a new document generation while retaining stale-target history."""
        self.generation += 1
        self.url = str(url or "").strip()
        self.title = _compact_text(title, 300)
        self.field_coverage.clear()
        self.blockers.clear()
        self._interactive_target_ids.clear()
        self._cards.clear()
        self._trim_target_history()

    def observe(self, *, url: Any = "", title: Any = "") -> None:
        """Update non-empty document metadata without advancing generation."""
        normalized_url = str(url or "").strip()
        normalized_title = _compact_text(title, 300)
        if normalized_url:
            self.url = normalized_url
        if normalized_title:
            self.title = normalized_title

    def validate_generation(self, generation_id: str) -> None:
        """Reject callers that are not using the current PageState generation."""
        requested = _generation_number(generation_id)
        if requested != self.generation:
            raise ValueError(
                f"Stale PageState generation {generation_id}; current generation is "
                f"{self.generation_id}. Probe or snapshot the current page again."
            )

    def register_interactives(self, payload: Dict[str, Any]) -> None:
        """Attach safe target IDs to a Probe result and refresh its compact index."""
        self.observe(url=payload.get("url"), title=payload.get("title"))
        elements = payload.get("elements")
        if not isinstance(elements, list):
            elements = []
        self._interactive_target_ids = []
        for item in elements:
            if not isinstance(item, dict):
                continue
            target = self._register_probe_target(item, source="interactive")
            if target is not None:
                item["target_id"] = target.target_id
                item["generation_id"] = self.generation_id
                self._interactive_target_ids.append(target.target_id)
        self._update_blockers(payload, elements)

    def register_cards(self, payload: Dict[str, Any]) -> None:
        """Attach target IDs and build compact card records for PageState."""
        self.observe(url=payload.get("url"), title=payload.get("title"))
        cards = payload.get("cards")
        if not isinstance(cards, list):
            cards = []
        self._cards = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            compact_card: Dict[str, Any] = {"generation_id": self.generation_id}
            target = self._register_probe_target(card, source="card")
            if target is not None:
                card["target_id"] = target.target_id
                card["generation_id"] = self.generation_id
                compact_card["target_id"] = target.target_id

            primary_link_target = self._register_primary_link_target(card)
            if primary_link_target is not None:
                card["primary_link_target_id"] = primary_link_target.target_id
                compact_card["primary_link_target_id"] = primary_link_target.target_id

            compact_controls: list[Dict[str, Any]] = []
            for child_key in ("buttons", "controls", "actions"):
                children = card.get(child_key)
                if not isinstance(children, list):
                    continue
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_target = self._register_probe_target(
                        child,
                        source=f"card_{child_key}",
                    )
                    if child_target is not None:
                        child["target_id"] = child_target.target_id
                        child["generation_id"] = self.generation_id
                        compact_controls.append(child_target.compact_index())

            for field_name in _CARD_FIELD_NAMES:
                field_value = card.get(field_name)
                if field_value not in (None, "", [], {}):
                    self.field_coverage.add(field_name)
                    compact_card[field_name] = _compact_text(field_value)
            if compact_card.get("primary_link") and not compact_card.get("href"):
                compact_card["href"] = compact_card["primary_link"]
            if compact_controls:
                compact_card["controls"] = compact_controls
            if len(compact_card) > 1:
                self._cards.append(compact_card)
        self._update_blockers(payload, cards)

    def register_ax_snapshot(self, value: Any) -> tuple[str, ...]:
        """Register native Playwright refs without translating them in the model."""
        text = value if isinstance(value, str) else str(value)
        registered: list[str] = []
        for match in _AX_REF_LINE_RE.finditer(text):
            ref_value = match.group("ref")
            role = str(match.group("role") or "").strip()
            name = str(match.group("name") or "").strip()
            self.reference_generations[ref_value] = self.generation
            existing_id = self._ref_targets.get(ref_value)
            existing_target = self._targets.get(existing_id or "")
            if existing_target is not None and existing_target.generation == self.generation:
                target = existing_target
            else:
                target = self._new_target(
                    source="ax",
                    locator={"ref": ref_value},
                    ref=ref_value,
                    role=role,
                    name=name,
                    text=name,
                )
                self._ref_targets[ref_value] = target.target_id
            if target.target_id not in self._interactive_target_ids:
                self._interactive_target_ids.append(target.target_id)
            registered.append(ref_value)

        for ref_value in _ANY_REF_RE.findall(text):
            if self.reference_generations.get(ref_value) == self.generation:
                continue
            self.reference_generations[ref_value] = self.generation
            target = self._new_target(
                source="ax",
                locator={"ref": ref_value},
                ref=ref_value,
            )
            self._ref_targets[ref_value] = target.target_id
            self._interactive_target_ids.append(target.target_id)
            registered.append(ref_value)

        self._update_blockers({}, [text[:4000]])
        return tuple(registered)

    def replace_ax_snapshot(self, value: Any) -> tuple[str, ...]:
        """Replace current-generation AX refs with refs from one complete snapshot."""
        text = value if isinstance(value, str) else str(value)
        snapshot_refs = set(_ANY_REF_RE.findall(text))
        missing_refs = [
            ref_value
            for ref_value, generation in self.reference_generations.items()
            if generation == self.generation and ref_value not in snapshot_refs
        ]
        for ref_value in missing_refs:
            self._remove_current_ax_ref(ref_value)
        return self.register_ax_snapshot(text)

    def add_field_coverage(self, fields: Iterable[str]) -> None:
        """Record structured fields extracted from the current document."""
        for field_name in fields:
            normalized = str(field_name or "").strip()
            if normalized:
                self.field_coverage.add(normalized)

    def resolve_target(
        self,
        *,
        generation_id: str,
        target_id: str = "",
        ref: str = "",
        selector: str = "",
        require_registered_selector: bool = True,
    ) -> Optional[BrowserTarget]:
        """Resolve one target and reject every stale generation before execution."""
        self.validate_generation(generation_id)
        normalized_target_id = str(target_id or "").strip()
        normalized_ref = str(ref or "").strip()
        normalized_selector = str(selector or "").strip()

        if normalized_target_id:
            target = self._targets.get(normalized_target_id)
            if target is None:
                raise ValueError(f"Unknown PageState target_id: {normalized_target_id}")
            self._validate_target_generation(target)
            return target

        if normalized_ref:
            ref_generation = self.reference_generations.get(normalized_ref)
            if ref_generation is None:
                raise ValueError(f"Unknown AX ref: {normalized_ref}. Capture the current page snapshot again.")
            if ref_generation != self.generation:
                raise ValueError(
                    f"Stale AX ref {normalized_ref} belongs to g{ref_generation}; "
                    f"current generation is {self.generation_id}."
                )
            resolved_id = self._ref_targets.get(normalized_ref)
            target = self._targets.get(resolved_id or "")
            if target is None:
                raise ValueError(f"AX ref is not registered as a target: {normalized_ref}")
            self._validate_target_generation(target)
            return target

        if normalized_selector:
            selector_generation = self.selector_generations.get(normalized_selector)
            if selector_generation is not None and selector_generation != self.generation:
                raise ValueError(
                    f"Stale selector belongs to g{selector_generation}; current generation is "
                    f"{self.generation_id}: {normalized_selector}"
                )
            resolved_id = self._selector_targets.get((self.generation, normalized_selector))
            target = self._targets.get(resolved_id or "")
            if target is not None:
                return target
            if require_registered_selector:
                raise ValueError(
                    "Unregistered action selector. Use target_id/ref from the current "
                    f"PageState instead of constructing CSS: {normalized_selector}"
                )
            self.selector_generations[normalized_selector] = self.generation
        return None

    def update_target_locator(self, target_id: str, locator: Mapping[str, str]) -> None:
        """Replace a current target's internal locator after runtime resolution."""
        target = self._targets.get(str(target_id or "").strip())
        if target is None:
            raise ValueError(f"Unknown PageState target_id: {target_id}")
        self._validate_target_generation(target)
        target.locator = {str(key): str(value) for key, value in locator.items() if str(value or "").strip()}
        selector = str(target.locator.get("selector") or "").strip()
        if selector:
            target.selector = selector
            self.selector_generations[selector] = self.generation
            self._selector_targets[(self.generation, selector)] = target.target_id

    def export(self) -> Dict[str, Any]:
        """Return a bounded PageState index suitable for every browser result."""
        interactives = self._export_targets(
            self._interactive_target_ids,
            _MAX_PUBLIC_INTERACTIVES,
        )
        return {
            "page_id": self.page_id,
            "generation_id": self.generation_id,
            "url": self.url,
            "title": self.title,
            "interactives": interactives,
            "cards": self._cards[:_MAX_PUBLIC_CARDS],
            "field_coverage": sorted(self.field_coverage),
            "blockers": sorted(self.blockers),
        }

    def _register_probe_target(
        self,
        item: Dict[str, Any],
        *,
        source: str,
    ) -> Optional[BrowserTarget]:
        selector = str(item.get("selector_hint") or "").strip()
        selector_validated = bool(item.get("selector_hint_validated"))
        match_count = item.get("match_count")
        if selector and (not selector_validated or match_count != 1):
            selector = ""

        # Probe targets are executable only when the probe proved that its selector
        # uniquely identifies the element. AX refs use their own native registry.
        locator: Dict[str, str] = {"selector": selector} if selector else {}

        href = str(item.get("primary_link") or item.get("href") or "").strip()
        if not locator and not href:
            return None

        if selector:
            existing_id = self._selector_targets.get((self.generation, selector))
            if existing_id and existing_id in self._targets:
                return self._targets[existing_id]

        target = self._new_target(
            source=source,
            locator=locator,
            selector=selector,
            href=href,
            role=str(item.get("role") or "").strip(),
            name=str(item.get("accessible_name") or item.get("name") or "").strip(),
            text=str(item.get("text") or item.get("title") or "").strip(),
            visible=bool(item.get("visible", True)),
            enabled=bool(item.get("enabled", not item.get("disabled", False))),
            actionable=bool(item.get("actionable", False)),
            clickable=bool(item.get("clickable", False)),
        )
        if selector:
            self.selector_generations[selector] = self.generation
            self._selector_targets[(self.generation, selector)] = target.target_id
        return target

    def _register_primary_link_target(
        self,
        card: Dict[str, Any],
    ) -> Optional[BrowserTarget]:
        href = str(card.get("primary_link") or card.get("href") or "").strip()
        selector = str(card.get("primary_link_selector_hint") or "").strip()
        match_count = card.get("primary_link_match_count")
        if not href:
            return None
        selector_validated = bool(card.get("primary_link_selector_hint_validated"))
        if selector and (not selector_validated or match_count != 1):
            selector = ""
        target = self._register_probe_target(
            {
                "selector_hint": selector,
                "selector_hint_validated": bool(selector),
                "match_count": 1 if selector else None,
                "primary_link": href,
                "role": "link",
                "text": card.get("title") or "",
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            },
            source="card_primary_link",
        )
        if selector:
            self.selector_primary_links[selector] = (self.generation, href)
        return target

    def _new_target(self, *, source: str, locator: Dict[str, str], **kwargs: Any) -> BrowserTarget:
        self._target_counter += 1
        target_id = f"t_g{self.generation}_{self._target_counter}"
        target = BrowserTarget(
            target_id=target_id,
            page_id=self.page_id,
            generation=self.generation,
            source=source,
            locator=dict(locator),
            **kwargs,
        )
        self._targets[target_id] = target
        return target

    def _validate_target_generation(self, target: BrowserTarget) -> None:
        if target.page_id != self.page_id or target.generation != self.generation:
            raise ValueError(
                f"Stale target_id {target.target_id} belongs to {target.generation_id}; "
                f"current generation is {self.generation_id}."
            )

    def _remove_current_ax_ref(self, ref_value: str) -> None:
        target_id = self._ref_targets.get(ref_value)
        target = self._targets.get(target_id or "")
        if target is not None and target.generation == self.generation and target.source == "ax":
            self._targets.pop(target.target_id, None)
            self._interactive_target_ids = [
                current_target_id
                for current_target_id in self._interactive_target_ids
                if current_target_id != target.target_id
            ]
            if self._ref_targets.get(ref_value) == target.target_id:
                self._ref_targets.pop(ref_value, None)
        if self.reference_generations.get(ref_value) == self.generation:
            self.reference_generations.pop(ref_value, None)

    def _export_targets(self, target_ids: Iterable[str], limit: int) -> list[Dict[str, Any]]:
        exported: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for target_id in target_ids:
            if target_id in seen:
                continue
            seen.add(target_id)
            target = self._targets.get(target_id)
            if target is None or target.generation != self.generation:
                continue
            exported.append(target.compact_index())
            if len(exported) >= limit:
                break
        return exported

    def _update_blockers(self, payload: Mapping[str, Any], values: Iterable[Any]) -> None:
        chunks = [
            str(payload.get("error") or ""),
            str(payload.get("title") or ""),
            str(payload.get("url") or ""),
        ]
        for value in values:
            if isinstance(value, Mapping):
                chunks.extend(str(value.get(key) or "") for key in ("text", "title", "accessible_name", "aria_label"))
            else:
                chunks.append(str(value or ""))
            if sum(len(chunk) for chunk in chunks) >= 6000:
                break
        searchable = " ".join(chunks)[:6000]
        for blocker, pattern in _BLOCKER_PATTERNS:
            if pattern.search(searchable):
                self.blockers.add(blocker)

    def _trim_target_history(self) -> None:
        overflow = len(self._targets) - _MAX_TARGET_HISTORY
        if overflow <= 0:
            return
        stale_ids = [target_id for target_id, target in self._targets.items() if target.generation != self.generation][
            :overflow
        ]
        for target_id in stale_ids:
            target = self._targets.pop(target_id, None)
            if target is None:
                continue
            if target.ref and self._ref_targets.get(target.ref) == target_id:
                self._ref_targets.pop(target.ref, None)
                if self.reference_generations.get(target.ref) == target.generation:
                    self.reference_generations.pop(target.ref, None)
            if target.selector:
                selector_key = (target.generation, target.selector)
                if self._selector_targets.get(selector_key) == target_id:
                    self._selector_targets.pop(selector_key, None)
                if self.selector_generations.get(target.selector) == target.generation:
                    self.selector_generations.pop(target.selector, None)
                primary_link = self.selector_primary_links.get(target.selector)
                if primary_link and primary_link[0] == target.generation:
                    self.selector_primary_links.pop(target.selector, None)


__all__ = ["BrowserPageState", "BrowserTarget"]
