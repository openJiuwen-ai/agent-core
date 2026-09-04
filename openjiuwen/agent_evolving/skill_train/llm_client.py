# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM client wrappers for ReflACT target and optimizer roles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy, invoke_text_with_retry
from openjiuwen.agent_evolving.skill_train.model_compat import get_reasoning_effort
from openjiuwen.core.foundation.llm.model import Model


def _response_to_text(response: Any) -> str:
    if hasattr(response, "content"):
        return str(response.content or "")
    if isinstance(response, dict):
        return str(response.get("content", "") or response.get("text", "") or "")
    return str(response or "")


def _effort_kwargs(reasoning_effort: str | None = None) -> Dict[str, Any]:
    effort = reasoning_effort if reasoning_effort is not None else get_reasoning_effort()
    if not effort:
        return {}
    # OpenAI-compatible chat APIs often accept top-level reasoning_effort;
    # also nest under extra_body for providers that only read extra_body.
    return {
        "reasoning_effort": effort,
        "extra_body": {"reasoning_effort": effort},
    }


def make_llm_invoke_policy(
    *,
    attempt_timeout_secs: float = 120.0,
    total_budget_secs: float = 600.0,
    max_attempts: int = 3,
) -> LLMInvokePolicy:
    """Build resilience policy for skill_train LLM calls."""
    attempt = max(1.0, float(attempt_timeout_secs))
    total = max(attempt, float(total_budget_secs))
    return LLMInvokePolicy(
        attempt_timeout_secs=attempt,
        total_budget_secs=total,
        max_attempts=max(1, int(max_attempts)),
    )


@dataclass
class ChatLLMClient:
    """Sync-friendly wrapper over agent-core Model + llm_resilience."""

    llm: Model
    model: str
    policy: LLMInvokePolicy = field(default_factory=make_llm_invoke_policy)

    def _policy(
        self,
        *,
        retries: int | None = None,
        timeout: float | int | None = None,
    ) -> LLMInvokePolicy:
        max_attempts = retries if retries is not None else self.policy.max_attempts
        attempt = self.policy.attempt_timeout_secs
        if timeout is not None and float(timeout) > 0:
            attempt = max(attempt, float(timeout))
        total = max(self.policy.total_budget_secs, attempt * max(1, int(max_attempts)))
        return LLMInvokePolicy(
            attempt_timeout_secs=attempt,
            total_budget_secs=total,
            max_attempts=max_attempts,
            backoff_base_secs=self.policy.backoff_base_secs,
            retry_empty_response=self.policy.retry_empty_response,
        )

    def chat(
        self,
        *,
        system: str,
        user: str,
        max_completion_tokens: int = 16384,
        retries: int = 3,
        stage: str = "",
        timeout: int | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[str, Dict[str, Any]]:
        del max_completion_tokens
        prompt = f"{system.strip()}\n\n{user.strip()}"
        text = asyncio.run(
            invoke_text_with_retry(
                self.llm,
                self.model,
                prompt,
                policy=self._policy(retries=retries, timeout=timeout),
                **_effort_kwargs(reasoning_effort),
            )
        )
        return text, {"stage": stage}

    def chat_messages(
        self,
        messages: List[dict],
        *,
        max_completion_tokens: int = 16384,
        retries: int = 5,
        stage: str = "target",
        tools: List[dict] | None = None,
        tool_choice: Union[str, dict, None] = None,
        return_message: bool = False,
        timeout: int | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[Any, Dict[str, Any]]:
        del retries  # chat_messages uses a single invoke; retries belong to chat()/resilience
        invoke_kwargs: Dict[str, Any] = {"max_tokens": max_completion_tokens}
        if tools is not None:
            invoke_kwargs["tools"] = tools
        if tool_choice is not None:
            invoke_kwargs["tool_choice"] = tool_choice
        invoke_kwargs.update(_effort_kwargs(reasoning_effort))
        effective_timeout = None
        if timeout is not None and float(timeout) > 0:
            effective_timeout = max(float(timeout), self.policy.attempt_timeout_secs)

        async def _invoke() -> Any:
            coro = self.llm.invoke(messages, **invoke_kwargs)
            if effective_timeout is not None:
                return await asyncio.wait_for(coro, timeout=effective_timeout)
            return await coro

        response = asyncio.run(_invoke())
        if return_message:
            return response, {"stage": stage}
        return _response_to_text(response), {"stage": stage}


_optimizer_client: Optional[ChatLLMClient] = None
_target_client: Optional[ChatLLMClient] = None


def set_optimizer_client(client: ChatLLMClient | None) -> None:
    global _optimizer_client
    _optimizer_client = client


def set_target_client(client: ChatLLMClient | None) -> None:
    global _target_client
    _target_client = client


def get_optimizer_client() -> ChatLLMClient:
    if _optimizer_client is None:
        raise RuntimeError("Optimizer LLM client is not configured")
    return _optimizer_client


def get_target_client() -> ChatLLMClient:
    if _target_client is None:
        raise RuntimeError("Target LLM client is not configured")
    return _target_client


def chat_optimizer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    return get_optimizer_client().chat(**kwargs)


def chat_target(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    return get_target_client().chat(**kwargs)


def chat_target_messages(
    messages: list[dict],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict]:
    return get_target_client().chat_messages(
        messages,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
        reasoning_effort=reasoning_effort,
    )
