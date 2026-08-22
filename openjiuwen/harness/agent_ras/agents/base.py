# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared Agent RAS foundation: AgentAdapter SPI, constants, skill registry.

Split conventions for this package:
- SPI / replaceable platform surface → ``Protocol`` + one adapter class per platform.
- Orchestration (timeout, fail-open, verdict) → ``RASAgents`` (not adapters).
- Fault-domain registry / path I/O / query framing → module-level functions.
- Private helpers only when the name carries intent or seals a stable boundary;
  do not add pure aliases or single-call-site path-layer wrappers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

# Fault domain ids align with detector.name / config detector keys.
FAULT_DOMAIN_LLM_THINKING_LOOP = "llm_thinking_loop"

AGENT_RAS_SKILL_ROLES: tuple[str, ...] = ("detection", "recovery")

# Internal member / async-recovery knobs (not host-configurable).
# Bare ReActAgent judge path (no rails / SysOperation) usually emits the skill
# JSON within a few turns; 10 caps waste on non-converging L3 invokes.
MEMBER_MAX_ITERATIONS: int = 10
ASYNC_RECOVERY_TIMEOUT_SECONDS: float = 60.0
SKILL_TIMEOUT_SECONDS: float = 30.0

_FAULT_DOMAIN_SKILLS: dict[str, dict[str, str]] = {
    FAULT_DOMAIN_LLM_THINKING_LOOP: {
        "detection": "llm-loop-detection",
        "recovery": "llm-loop-review",
    },
}

_AGENT_RAS_ROOT = Path(__file__).resolve().parent.parent

_ROLE_SKILL_DIRS: dict[str, Path] = {
    "detection": _AGENT_RAS_ROOT / "detectors" / "skills",
    "recovery": _AGENT_RAS_ROOT / "recovery" / "skills",
}


@runtime_checkable
class AgentAdapter(Protocol):
    """SPI: run a semantic skill query on one agent platform."""

    async def run(
        self,
        *,
        role: str,
        skill_name: str,
        query: str,
    ) -> str | dict:
        """Execute ``query`` and return raw model/agent output (not verdict dict)."""
        ...

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        """Optional pre-create of platform members."""
        ...


class NoOpAgentAdapter:
    """Disabled / missing-model platform: raw empty payload for fail-open."""

    async def run(
        self,
        *,
        role: str,
        skill_name: str,
        query: str,
    ) -> str | dict:
        return "{}"

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        return None


def resolve_skill(fault_domain: str, role: str) -> str:
    """Resolve skill name for ``fault_domain`` × ``role``.

    Raises:
        ValueError: unknown domain or role for that domain.
    """
    domain = str(fault_domain or "").strip()
    role_key = str(role or "").strip()
    if not domain or domain not in _FAULT_DOMAIN_SKILLS:
        raise ValueError(f"unknown fault domain: {fault_domain!r}")
    skills = _FAULT_DOMAIN_SKILLS[domain]
    if role_key not in skills:
        raise ValueError(f"unknown role {role!r} for fault domain {domain!r}; known={sorted(skills)}")
    return skills[role_key]


def _load_skill_body(role: str, skill_name: str) -> str:
    """Load SKILL.md via sync pathlib (bypass SysOperation ``fs.read_file``)."""
    skills_dir = _ROLE_SKILL_DIRS.get(role, _AGENT_RAS_ROOT / "detectors" / "skills")
    skill_path = skills_dir / skill_name / "SKILL.md"
    if not skill_path.is_file():
        return ""
    return skill_path.read_text(encoding="utf-8")


def build_inline_skill_query(*, role: str, skill_name: str, task_block: str) -> str:
    """Build member query with SKILL.md inlined so platforms need no skill_tool."""
    body = _load_skill_body(role, skill_name).strip()
    if not body:
        body = f"(SKILL `{skill_name}` 未能从本地包路径加载)"
    return (
        f"## Skill `{skill_name}`（已内联，禁止调用 skill_tool / skill_complete / 任何工具）\n"
        f"{body}\n\n"
        f"## 任务\n"
        f"{task_block}\n\n"
        f"按上述 Skill 要求，最终回复只输出 JSON 对象。"
    )


__all__ = [
    "AGENT_RAS_SKILL_ROLES",
    "ASYNC_RECOVERY_TIMEOUT_SECONDS",
    "FAULT_DOMAIN_LLM_THINKING_LOOP",
    "MEMBER_MAX_ITERATIONS",
    "SKILL_TIMEOUT_SECONDS",
    "AgentAdapter",
    "NoOpAgentAdapter",
    "build_inline_skill_query",
    "resolve_skill",
]
