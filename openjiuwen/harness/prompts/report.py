# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt diagnostic report."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import SystemPromptBuilder

# Rough estimate: 1 token ≈ 2.5 Chinese chars or 4 English chars.
_CN_CHARS_PER_TOKEN = 2.5
_EN_CHARS_PER_TOKEN = 4.0

# Fallback category for sections that do not declare one.
_DEFAULT_CATEGORY = "system_prompt"

# Fixed display order for grouped categories.
_CATEGORY_ORDER = ("system_prompt", "tools", "skills", "memory")

_CATEGORY_DISPLAY_NAMES = {
    "cn": {
        "system_prompt": "系统提示词",
        "tools": "工具及 MCP",
        "skills": "技能",
        "memory": "记忆",
    },
    "en": {
        "system_prompt": "System Prompt",
        "tools": "Tools & MCP",
        "skills": "Skills",
        "memory": "Memory",
    },
}


@dataclass
class SectionInfo:
    """Lightweight snapshot of a single section."""
    name: str
    priority: int
    char_count: int
    category: str = _DEFAULT_CATEGORY


@dataclass
class CategoryInfo:
    """Sections grouped under one category."""
    category: str
    display_name: str
    sections: List[SectionInfo] = field(default_factory=list)
    char_count: int = 0
    estimated_tokens: int = 0


@dataclass
class PromptReport:
    """Diagnostic report for a built system prompt."""
    total_chars: int
    estimated_tokens: int
    section_count: int
    sections: List[SectionInfo] = field(default_factory=list)
    mode: str = "full"
    language: str = "cn"
    categories: List[CategoryInfo] = field(default_factory=list)

    @classmethod
    def from_builder(cls, builder: "SystemPromptBuilder") -> "PromptReport":
        """Create a report from the current state of a builder."""
        language = builder.language
        mode = builder.mode.value

        section_infos: List[SectionInfo] = []
        total_chars = 0
        for s in sorted(builder.get_all_sections().values(), key=lambda x: x.priority):
            chars = s.char_count(language)
            section_infos.append(SectionInfo(
                name=s.name,
                priority=s.priority,
                char_count=chars,
                category=s.category or _DEFAULT_CATEGORY,
            ))
            total_chars += chars

        chars_per_token = (
            _CN_CHARS_PER_TOKEN if language == "cn" else _EN_CHARS_PER_TOKEN
        )
        estimated_tokens = int(total_chars / chars_per_token) if total_chars else 0

        return cls(
            total_chars=total_chars,
            estimated_tokens=estimated_tokens,
            section_count=len(section_infos),
            sections=section_infos,
            mode=mode,
            language=language,
            categories=cls._group_by_category(section_infos, chars_per_token, language=language),
        )

    @staticmethod
    def _group_by_category(
        section_infos: List[SectionInfo],
        chars_per_token: float,
        *,
        language: str = "cn",
    ) -> List[CategoryInfo]:
        """Group sections by category in a fixed order; empty groups are omitted.

        Custom categories are retained after the built-in groups so the
        grouped totals remain lossless for extension-provided sections.
        """
        grouped: Dict[str, List[SectionInfo]] = {}
        for info in section_infos:
            category = getattr(info.category, "value", info.category)
            info.category = str(category or _DEFAULT_CATEGORY)
            grouped.setdefault(info.category, []).append(info)

        categories: List[CategoryInfo] = []
        ordered_categories = list(_CATEGORY_ORDER) + sorted(set(grouped) - set(_CATEGORY_ORDER))
        display_names = _CATEGORY_DISPLAY_NAMES.get(language, _CATEGORY_DISPLAY_NAMES["cn"])
        for category in ordered_categories:
            infos = grouped.get(category)
            if not infos:
                continue
            infos.sort(key=lambda x: x.priority)
            char_count = sum(info.char_count for info in infos)
            categories.append(CategoryInfo(
                category=category,
                display_name=display_names.get(category, category),
                sections=infos,
                char_count=char_count,
                estimated_tokens=int(char_count / chars_per_token) if char_count else 0,
            ))
        return categories

    def to_dict(self) -> Dict:
        """Serialize to a plain dict."""
        return {
            "total_chars": self.total_chars,
            "estimated_tokens": self.estimated_tokens,
            "section_count": self.section_count,
            "sections": [
                {
                    "name": s.name,
                    "priority": s.priority,
                    "char_count": s.char_count,
                    "category": s.category,
                }
                for s in self.sections
            ],
            "mode": self.mode,
            "language": self.language,
            "categories": [
                {
                    "category": c.category,
                    "display_name": c.display_name,
                    "char_count": c.char_count,
                    "estimated_tokens": c.estimated_tokens,
                    "sections": [
                        {
                            "name": s.name,
                            "priority": s.priority,
                            "char_count": s.char_count,
                            "category": s.category,
                        }
                        for s in c.sections
                    ],
                }
                for c in self.categories
            ],
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"[PromptReport] mode={self.mode} lang={self.language} "
            f"sections={self.section_count} chars={self.total_chars} "
            f"est_tokens≈{self.estimated_tokens}"
        )
