# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReActAgentAdapter — openjiuwen bare ReActAgent platform."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.agent_ras.agents.base import MEMBER_MAX_ITERATIONS
from openjiuwen.harness.agent_ras.detectors.skill_verdicts import (
    extract_invoke_output_payload,
    extract_json_object_from_text,
)

_ROLE_PROMPTS: dict[str, str] = {
    "detection": (
        "你是可靠性语义检测器。SKILL 正文已由宿主内联提供，禁止调用 skill_tool "
        "或任何工具；最终回复只输出 SKILL 规定的 JSON 对象，禁止其他文字，"
        "禁止调用 skill_complete。"
    ),
    "recovery": (
        "你是可靠性恢复侧成员。SKILL 正文已由宿主内联提供，禁止调用 skill_tool "
        "或任何工具；最终回复只输出 SKILL 规定的 JSON 对象，禁止其他文字，"
        "禁止调用 skill_complete。"
    ),
}


def _ras_conversation_id(role: str) -> str:
    return f"ras-{role}-{uuid.uuid4().hex}"


class ReliabilityJudgeAgent(ReActAgent):
    """Agent RAS semantic judge agent.

    A bare ReActAgent with no rails, workspace, or SysOperation.
    Built exclusively for detection / recovery LLM skill invocation.
    """

    def __init__(
        self,
        role: str,
        model: Model,
        max_iterations: int = MEMBER_MAX_ITERATIONS,
    ) -> None:
        role_key = str(role or "").strip() or "detection"
        card = AgentCard(
            name=f"reliability_{role_key}",
            description=f"Agent RAS {role_key} semantic member",
        )
        super().__init__(card)

        cfg = ReActAgentConfig()
        cfg.max_iterations = max_iterations
        # Short-task semantic members only need a bounded window (inline SKILL +
        # excerpt); 32 messages / 4 rounds is enough for the judge path defaults.
        cfg.context_engine_config = ContextEngineConfig(
            max_context_message_num=32,
            default_window_round_num=4,
        )
        cfg.prompt_template = [
            {
                "role": "system",
                "content": _ROLE_PROMPTS.get(role_key, _ROLE_PROMPTS["detection"]),
            },
        ]
        client_cfg = getattr(model, "model_client_config", None)
        model_cfg = getattr(model, "model_config", None)
        if client_cfg is not None:
            cfg.model_client_config = client_cfg
        if model_cfg is not None:
            cfg.model_config_obj = model_cfg
            model_name = getattr(model_cfg, "model_name", None)
            if model_name:
                cfg.model_name = model_name

        self.configure(cfg)
        self.set_llm(model)


def _extract_invoke_payload(result: Any) -> str | dict[str, Any]:
    payload = extract_invoke_output_payload(result)
    if payload is not None:
        return payload
    if isinstance(result, str):
        parsed = extract_json_object_from_text(result)
        return parsed if parsed is not None else result
    logger.warning(
        "[ReActAgentAdapter] invoke output not parseable as JSON payload: %r",
        type(result).__name__,
    )
    return "{}"


class ReActAgentAdapter:
    """openjiuwen platform: lazy bare ReActAgent members.

    Receives an already-inlined ``query`` from ``RASAgents``; does not own
    timeout / verdict parsing / fail-open.
    """

    def __init__(self, model: Optional[Model] = None) -> None:
        self._model = model
        self._members: dict[str, Any] = {}

    async def _get_or_create_member(self, role: str) -> Any | None:
        cached = self._members.get(role)
        if cached is not None:
            return cached
        if self._model is None:
            return None
        agent = ReliabilityJudgeAgent(
            role=role,
            model=self._model,
            max_iterations=MEMBER_MAX_ITERATIONS,
        )
        self._members[role] = agent
        return agent

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        for role in roles:
            try:
                await self._get_or_create_member(role)
            except Exception:
                logger.warning(
                    "Agent RAS warmup failed for role=%s",
                    role,
                    exc_info=True,
                )

    async def run(
        self,
        *,
        role: str,
        skill_name: str,
        query: str,
    ) -> str | dict:
        _ = skill_name
        agent = await self._get_or_create_member(role)
        if agent is None:
            return "{}"
        result = await agent.invoke(
            {
                "query": query,
                "conversation_id": _ras_conversation_id(role),
            }
        )
        return _extract_invoke_payload(result)


__all__ = ["ReActAgentAdapter", "ReliabilityJudgeAgent"]
