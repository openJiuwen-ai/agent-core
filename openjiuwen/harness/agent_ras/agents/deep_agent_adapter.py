# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgentAdapter — AgentAdapter backed by short-lived DeepAgent members."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation.cwd import init_cwd
from openjiuwen.harness.agent_ras.agents.base import (
    FAULT_DOMAIN_LLM_THINKING_LOOP,
    MEMBER_MAX_ITERATIONS,
    ROLE_PROMPTS,
    build_inline_skill_query,
    skill_for,
    skills_dir_for_role,
)
from openjiuwen.harness.agent_ras.config import AgentRASConfig
from openjiuwen.harness.agent_ras.detectors.skill_verdicts import (
    extract_invoke_output_payload,
    extract_json_object_from_text,
    parse_skill_verdict,
    verdict_to_dict,
)

logger = logging.getLogger(__name__)

InvokeFn = Callable[[str, str, str], Awaitable[str]]


def _ras_conversation_id(role: str) -> str:
    """Unique session key per semantic invoke (avoid ReAct ``default_session``)."""
    return f"ras-{role}-{uuid.uuid4().hex}"


def _rebind_member_cwd(role: str) -> str:
    """Rebind ContextVar CWD to the member skill root for the current Task.

    L3 runs via ``asyncio.create_task`` and inherits the parent agent's CWD.
    Member ``_ensure_initialized`` only calls ``init_cwd`` once, so later
    evals would keep the host workspace unless we rebind here.
    """
    skills_root = str(skills_dir_for_role(role).resolve())
    init_cwd(skills_root, workspace=skills_root)
    return skills_root


@dataclass
class AdapterConfig:
    """Runtime knobs for DeepAgentAdapter (skill names from fault-domain registry)."""

    max_iterations: int = MEMBER_MAX_ITERATIONS
    default_fault_domain: str = FAULT_DOMAIN_LLM_THINKING_LOOP

    def skill_for_role(
        self,
        role: str,
        fault_domain: str | None = None,
    ) -> str:
        domain = fault_domain or self.default_fault_domain
        return skill_for(domain, role)


def adapter_config_from_agent_ras(config: AgentRASConfig) -> AdapterConfig:
    """Build adapter knobs from ``AgentRASConfig`` (member limits are internal)."""
    _ = config
    return AdapterConfig()


def _describe_invoke_failure(result: Any) -> str:
    if not isinstance(result, dict):
        return "invoke result is not a dict"
    result_type = str(result.get("result_type") or "")
    output = str(result.get("output") or "")
    if result_type == "error" and "Max iterations reached" in output:
        return (
            "member exhausted max_iterations before a JSON-only final answer; "
            "check skill contract (no skill_complete on verdict turn)"
        )
    if result_type == "answer" and output.strip():
        preview = output.strip().replace("\n", " ")[:120]
        return f"invoke output is not valid verdict JSON: {preview!r}"
    return f"invoke result_type={result_type!r} has no parseable output"


def _extract_invoke_payload(result: Any) -> str | dict[str, Any]:
    """Extract structured JSON from invoke result output (auto_harness style)."""
    payload = extract_invoke_output_payload(result)
    if payload is not None:
        return payload
    if isinstance(result, str):
        parsed = extract_json_object_from_text(result)
        return parsed if parsed is not None else result
    logger.warning(
        "[DeepAgentAdapter] skill verdict extract failed: %s",
        _describe_invoke_failure(result),
    )
    return "{}"


class DeepAgentAdapter:
    """Default AgentAdapter using lazy DeepAgent members with inlined SKILL bodies."""

    def __init__(
        self,
        cfg: Optional[AdapterConfig] = None,
        model: Optional[Model] = None,
        invoke_fn: Optional[InvokeFn] = None,
    ) -> None:
        self._cfg = cfg or AdapterConfig()
        self._model = model
        self._invoke_fn = invoke_fn
        self._members: dict[str, Any] = {}

    @staticmethod
    def register_skills(role: str) -> Path:
        """Return the skill root directory for ``role``."""
        return skills_dir_for_role(role)

    async def get_or_create_member(self, role: str) -> Any | None:
        """Lazy-create a short-lived DeepAgent judge for ``role``.

        No ``SkillUseRail`` / ``skill_tool``: SKILL.md is inlined via pathlib on
        each invoke (see ``build_inline_skill_query``) to avoid mid-stream
        ``SysOperation.fs.read_file`` stalls. Workspace still points at the
        package skill root for sandbox alignment; ``agent_ras=False`` prevents
        nesting another AgentRASRail on judges.
        """
        if self._invoke_fn is not None:
            return None
        cached = self._members.get(role)
        if cached is not None:
            return cached
        if self._model is None:
            return None
        from openjiuwen.harness.factory import create_deep_agent
        from openjiuwen.harness.workspace.workspace import Workspace

        skills_root = skills_dir_for_role(role).resolve()
        if not skills_root.is_dir():
            logger.warning("Agent RAS skill directory missing: %s", skills_root)
            return None
        skills_dir = str(skills_root)
        agent = create_deep_agent(
            model=self._model,
            card=AgentCard(
                name=f"reliability_{role}",
                description=f"Reliability {role} semantic member",
            ),
            system_prompt=ROLE_PROMPTS.get(role, ROLE_PROMPTS["detection"]),
            rails=[],
            workspace=Workspace(root_path=skills_dir),
            enable_task_loop=False,
            max_iterations=self._cfg.max_iterations,
            auto_create_workspace=False,
            agent_ras=False,
        )
        self._members[role] = agent
        return agent

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        for role in roles:
            try:
                await self.get_or_create_member(role)
            except Exception:
                logger.warning(
                    "Agent RAS warmup failed for role=%s",
                    role,
                    exc_info=True,
                )

    async def _invoke_member(
        self,
        role: str,
        *,
        skill_name: str,
        user_content: str,
    ) -> str | dict[str, Any]:
        """Invoke one reliability member with SKILL body inlined into the query."""
        if self._invoke_fn is not None:
            raw = await self._invoke_fn(role, skill_name, user_content)
            if isinstance(raw, dict) and "output" in raw:
                return _extract_invoke_payload(raw)
            return raw
        agent = await self.get_or_create_member(role)
        if agent is None:
            return "{}"
        _rebind_member_cwd(role)
        conversation_id = _ras_conversation_id(role)
        query = build_inline_skill_query(
            role=role,
            skill_name=skill_name,
            task_block=user_content,
        )
        # Unique conversation_id avoids ReAct falling back to "default_session",
        # which can collide with the parent stream / other nested invokes.
        result = await agent.invoke(
            {"query": query, "conversation_id": conversation_id}
        )
        return _extract_invoke_payload(result)

    async def invoke_skill(
        self,
        *,
        role: str,
        skill_name: str,
        payload: str,
        timeout: float,
    ) -> dict:
        if role == "recovery":
            user_msg = f"恢复材料:\n{payload}"
        else:
            user_msg = f"待判定 excerpt:\n{payload}"
        if timeout <= 0:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=non_positive_timeout",
                role,
                skill_name,
            )
            return {}
        try:
            raw = await asyncio.wait_for(
                self._invoke_member(
                    role,
                    skill_name=skill_name,
                    user_content=user_msg,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Agent RAS semantic %s skill=%s timed out fail_open=True",
                role,
                skill_name,
            )
            return {}
        except Exception:
            logger.warning(
                "Agent RAS semantic %s skill=%s failed fail_open=True",
                role,
                skill_name,
                exc_info=True,
            )
            return {}
        if raw in ("{}", "", None) or raw == {}:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=empty_result",
                role,
                skill_name,
            )
            return {}
        verdict = parse_skill_verdict(skill_name, raw)
        if verdict.fail_open_reason:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=%s",
                role,
                skill_name,
                verdict.fail_open_reason,
            )
        if role in ("detection", "recovery"):
            return verdict_to_dict(verdict)
        return verdict.raw or {}
