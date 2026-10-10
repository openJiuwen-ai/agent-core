# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budget-notice prompt section for BudgetNoticeRail.

Renders a short "wind down" notice listing the task-loop resources that are
close to their hard limit (rounds / tokens / wall-clock). The notice never
invents a budget: callers feed it the limits read from the loop's actual
stop-condition evaluators (see ``LoopCoordinator.budget_limits``).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


BUDGET_NOTICE_HEADER: Dict[str, str] = {
    "cn": "**预算提醒**：本任务的资源即将用尽。",
    "en": "**Budget notice**: this task is running low on its resources.",
}

BUDGET_NOTICE_LINE: Dict[str, str] = {
    "cn": "- {label}：已用 {used}/{limit}，剩余约 {remaining}。",
    "en": "- {label}: used {used} of {limit}, about {remaining} left.",
}

BUDGET_NOTICE_FOOTER: Dict[str, str] = {
    "cn": (
        "请优先完成或清晰总结当前任务，不要开启新的长耗时子任务。"
        "如果无法全部完成，给出当前最佳的部分结果，并明确说明剩余工作。"
    ),
    "en": (
        "Prioritise completing or cleanly summarising the current task. "
        "Do not start new long subtasks. If you cannot finish, produce the best "
        "partial result you can and clearly state what remains."
    ),
}

BUDGET_NOTICE_LABELS: Dict[str, Dict[str, str]] = {
    "rounds": {"cn": "迭代轮次", "en": "Iterations"},
    "tokens": {"cn": "Token 预算", "en": "Token budget"},
    "seconds": {"cn": "时间预算", "en": "Time budget"},
}


def _pick(mapping: Mapping[str, str], language: str) -> str:
    """Return the language's text, falling back to ``en`` then any value."""
    return (
        mapping.get(language)
        or mapping.get("en")
        or next(iter(mapping.values()), "")
    )


def _fmt(value: Any) -> str:
    """Format a numeric budget value without a trailing ``.0`` for integers."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def build_budget_notice_section(
    language: str = "cn",
    notices: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Optional[PromptSection]:
    """Build the budget-notice section, or ``None`` when nothing is low.

    Args:
        language: Prompt language (``"cn"`` or ``"en"``).
        notices: One mapping per near-limit resource, each with ``kind``
            (``"rounds"``/``"tokens"``/``"seconds"``), ``used``, ``limit``,
            and ``remaining``.

    Returns:
        A :class:`PromptSection`, or ``None`` when ``notices`` is empty so the
        caller can remove any stale section.
    """
    if not notices:
        return None

    lang = language if language in BUDGET_NOTICE_HEADER else "cn"
    lines = [_pick(BUDGET_NOTICE_HEADER, lang)]
    for notice in notices:
        kind = str(notice.get("kind", ""))
        labels = BUDGET_NOTICE_LABELS.get(kind, {})
        label = _pick(labels, lang) if labels else kind
        lines.append(
            _pick(BUDGET_NOTICE_LINE, lang).format(
                label=label,
                used=_fmt(notice.get("used")),
                limit=_fmt(notice.get("limit")),
                remaining=_fmt(notice.get("remaining")),
            )
        )
    lines.append(_pick(BUDGET_NOTICE_FOOTER, lang))

    return PromptSection(
        name=SectionName.BUDGET_NOTICE,
        content={lang: "\n".join(lines)},
        priority=95,
    )


__all__ = [
    "build_budget_notice_section",
]
