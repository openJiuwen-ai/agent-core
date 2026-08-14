# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize one LLM interaction into a token-in/token-out record."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs

from .llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids


def _model_dump(value: Any) -> dict[str, Any] | None:
    if not hasattr(value, "model_dump"):
        return None
    try:
        dumped = value.model_dump()
    except Exception:
        return None
    return dumped if isinstance(dumped, dict) else None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    dumped = _model_dump(value)
    if dumped is not None:
        return _json_value(dumped)
    return str(value)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        normalized = _json_value(message)
        return normalized if isinstance(normalized, dict) else {"role": "unknown", "content": str(message)}
    dumped = _model_dump(message)
    if dumped is not None:
        normalized = _json_value(dumped)
        return normalized if isinstance(normalized, dict) else {"role": "unknown", "content": str(message)}
    role = getattr(message, "role", None)
    if role is not None:
        item: dict[str, Any] = {
            "role": str(role),
            "content": _json_value(getattr(message, "content", "")),
        }
        name = getattr(message, "name", None)
        if name is not None:
            item["name"] = str(name)
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = _json_value(tool_calls)
        return item
    return {"role": "unknown", "content": str(message)}


def _response_to_dict(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    if isinstance(response, dict):
        normalized = _json_value(response)
        return normalized if isinstance(normalized, dict) else {"content": str(response)}
    dumped = _model_dump(response)
    if dumped is not None:
        normalized = _json_value(dumped)
        return normalized if isinstance(normalized, dict) else {"content": str(response)}
    out: dict[str, Any] = {
        "role": getattr(response, "role", "assistant"),
        "content": _json_value(getattr(response, "content", "")),
    }
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls is not None:
        out["tool_calls"] = _json_value(tool_calls)
    usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
    if usage is not None:
        out["usage"] = _json_value(usage)
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason is not None:
        out["finish_reason"] = finish_reason
    reasoning_content = getattr(response, "reasoning_content", None)
    if reasoning_content is not None:
        out["reasoning_content"] = reasoning_content
    return out


def _text_from_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(value)


def _messages_to_prompt_str(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        content = _text_from_content(message.get("content"))
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _resolve_model_id(ctx: AgentCallbackContext) -> str:
    agent = getattr(ctx, "agent", None)
    react_agent = getattr(agent, "react_agent", None) or agent
    config = getattr(react_agent, "config", None) or getattr(react_agent, "_config", None)
    if config is None:
        return ""
    return str(getattr(config, "model_name", None) or getattr(config, "model", None) or "")


@dataclass
class TokenInTokenOutRecord:
    """One normalized LLM exchange for RL/SFT trajectory collectors."""

    prompt_str: str
    prompt_ids: Optional[list[int]]
    llm_str: str
    llm_ids: Optional[list[int]]
    messages: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    tools: Any = None
    model_id: str = ""
    logprobs: Optional[list[float]] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_str": self.prompt_str,
            "prompt_ids": self.prompt_ids,
            "llm_str": self.llm_str,
            "llm_ids": self.llm_ids,
            "messages": self.messages,
            "response": self.response,
            "tools": _json_value(self.tools),
            "model_id": self.model_id,
            "logprobs": self.logprobs,
            "meta": _json_value(self.meta),
        }


class TokenInTokenOutForwarder:
    """Build token-in/token-out records from either an existing hook or a direct string call.

    Online rails use ``from_model_call_context`` so they do not replace the
    agent's model-call path. ``forward`` is provided for future rollout code
    that intentionally wants this object to call a simple LLM interface.
    """

    @staticmethod
    def from_model_call_context(
        ctx: AgentCallbackContext,
        *,
        prompt_ids: Optional[list[int]] = None,
        llm_ids: Optional[list[int]] = None,
        logprobs: Optional[list[float]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> TokenInTokenOutRecord:
        inputs = getattr(ctx, "inputs", None)
        if not isinstance(inputs, ModelCallInputs):
            return TokenInTokenOutRecord(prompt_str="", prompt_ids=None, llm_str="", llm_ids=None)

        messages = [_message_to_dict(message) for message in (inputs.messages or [])]
        response = _response_to_dict(getattr(inputs, "response", None))
        token_source = response or getattr(inputs, "response", None)
        normalized_prompt_ids = prompt_ids or extract_prompt_ids(token_source)
        normalized_llm_ids = llm_ids or extract_token_ids(token_source)
        normalized_logprobs = logprobs or extract_logprobs(token_source)
        return TokenInTokenOutRecord(
            prompt_str=_messages_to_prompt_str(messages),
            prompt_ids=normalized_prompt_ids,
            llm_str=_text_from_content(response.get("content")),
            llm_ids=normalized_llm_ids,
            messages=messages,
            response=response,
            tools=_json_value(inputs.tools),
            model_id=_resolve_model_id(ctx),
            logprobs=normalized_logprobs,
            meta=meta or {},
        )

    async def forward(
        self,
        msg: str,
        *,
        llm: Any,
        model: str,
        tokenizer: Any = None,
        tools: Any = None,
        **kwargs: Any,
    ) -> TokenInTokenOutRecord:
        messages = [{"role": "user", "content": msg}]
        prompt_ids = self._encode(tokenizer, msg)
        response = await llm.invoke(model=model, messages=messages, tools=tools, **kwargs)
        response_dict = _response_to_dict(response)
        llm_str = _text_from_content(response_dict.get("content"))
        return TokenInTokenOutRecord(
            prompt_str=msg,
            prompt_ids=prompt_ids or extract_prompt_ids(response),
            llm_str=llm_str,
            llm_ids=extract_token_ids(response) or self._encode(tokenizer, llm_str),
            messages=messages,
            response=response_dict,
            tools=_json_value(tools),
            model_id=model,
            logprobs=extract_logprobs(response),
        )

    @staticmethod
    def _encode(tokenizer: Any, text: str) -> Optional[list[int]]:
        if tokenizer is None:
            return None
        encode = getattr(tokenizer, "encode", None)
        if not callable(encode):
            return None
        try:
            ids = encode(text)
        except Exception:
            return None
        if isinstance(ids, list):
            return [int(item) for item in ids]
        return None
