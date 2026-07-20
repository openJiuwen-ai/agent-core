# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared Agent RAS agent port, fault-domain skill registry, and skill layout."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from openjiuwen.harness.agent_ras.models import AnomalyKind

# Fault domain ids align with detector.name / config detector keys.
FAULT_DOMAIN_LLM_THINKING_LOOP = "llm_thinking_loop"

AGENT_RAS_SKILL_ROLES: tuple[str, ...] = ("detection", "recovery")

# Internal member / async-recovery knobs (not host-configurable).
MEMBER_MAX_ITERATIONS: int = 30
ASYNC_RECOVERY_TIMEOUT_SECONDS: float = 60.0
SKILL_TIMEOUT_SECONDS: float = 30.0

FAULT_DOMAIN_SKILLS: dict[str, dict[str, str]] = {
    FAULT_DOMAIN_LLM_THINKING_LOOP: {
        "detection": "llm-loop-detection",
        "recovery": "llm-loop-review",
    },
}

_KIND_TO_FAULT_DOMAIN: dict[str, str] = {
    AnomalyKind.LLM_THINKING_LOOP.value: FAULT_DOMAIN_LLM_THINKING_LOOP,
    AnomalyKind.LLM_THINKING_DEAD_LOOP.value: FAULT_DOMAIN_LLM_THINKING_LOOP,
}


def fault_domain_for_kind(kind: AnomalyKind | str) -> str | None:
    """Map an anomaly kind to its fault domain, or None if unregistered."""
    if isinstance(kind, AnomalyKind):
        key = kind.value
    else:
        key = str(kind or "").strip()
    return _KIND_TO_FAULT_DOMAIN.get(key)


def skill_for(fault_domain: str, role: str) -> str:
    """Resolve skill name for ``fault_domain`` × ``role``.

    Raises:
        ValueError: unknown domain or role for that domain.
    """
    domain = str(fault_domain or "").strip()
    role_key = str(role or "").strip()
    if not domain or domain not in FAULT_DOMAIN_SKILLS:
        raise ValueError(f"unknown fault domain: {fault_domain!r}")
    skills = FAULT_DOMAIN_SKILLS[domain]
    if role_key not in skills:
        raise ValueError(
            f"unknown role {role!r} for fault domain {domain!r}; "
            f"known={sorted(skills)}"
        )
    return skills[role_key]


_AGENT_RAS_ROOT = Path(__file__).resolve().parent.parent

ROLE_SKILL_DIRS: dict[str, Path] = {
    "detection": _AGENT_RAS_ROOT / "detectors" / "skills",
    "recovery": _AGENT_RAS_ROOT / "recovery" / "skills",
}

ROLE_PROMPTS: dict[str, str] = {
    "detection": (
        "你是可靠性语义检测器。必须经 skill_tool 加载指定 SKILL，"
        "最终回复只输出 SKILL 规定的 JSON 对象，禁止其他文字，禁止调用 skill_complete。"
    ),
    "recovery": (
        "你是可靠性恢复侧成员。必须经 skill_tool 加载指定 SKILL，"
        "最终回复只输出 SKILL 规定的 JSON 对象，禁止其他文字，禁止调用 skill_complete。"
    ),
}


@runtime_checkable
class AgentAdapter(Protocol):
    """Invoke an Agent RAS skill via a backing agent implementation."""

    async def invoke_skill(
        self,
        *,
        role: str,
        skill_name: str,
        payload: str,
        timeout: float,
    ) -> dict:
        ...

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        ...


class NoOpAgentAdapter:
    """Fail-open adapter used when semantic skills are disabled."""

    async def invoke_skill(
        self,
        *,
        role: str,
        skill_name: str,
        payload: str,
        timeout: float,
    ) -> dict:
        return {}

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        return None


def skills_dir_for_role(role: str) -> Path:
    return ROLE_SKILL_DIRS.get(role, _AGENT_RAS_ROOT / "detectors" / "skills")


def _skills_path(role: str, skill_name: str) -> Path:
    return skills_dir_for_role(role) / skill_name / "SKILL.md"


def load_skill_body(role: str, skill_name: str) -> str:
    """Load SKILL.md body for tests and lightweight invoke paths."""
    skill_path = _skills_path(role, skill_name)
    if not skill_path.is_file():
        return ""
    return skill_path.read_text(encoding="utf-8")


__all__ = [
    "AGENT_RAS_SKILL_ROLES",
    "ASYNC_RECOVERY_TIMEOUT_SECONDS",
    "FAULT_DOMAIN_LLM_THINKING_LOOP",
    "FAULT_DOMAIN_SKILLS",
    "MEMBER_MAX_ITERATIONS",
    "ROLE_PROMPTS",
    "ROLE_SKILL_DIRS",
    "SKILL_TIMEOUT_SECONDS",
    "AgentAdapter",
    "NoOpAgentAdapter",
    "fault_domain_for_kind",
    "load_skill_body",
    "skill_for",
    "skills_dir_for_role",
]
