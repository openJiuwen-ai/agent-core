# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native web-search (Azure-style) single-shot OfficeQA dialogue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.officeqa.chat_support import (
    append_assistant_event,
    invoke_chat,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.outcome import _DialogueOutcome
from openjiuwen.agent_evolving.skill_train.envs.officeqa.prompt_builders import (
    MODE_AZURE,
    build_system_prompt,
    build_user_prompt,
    extract_answer,
    has_answer_tag,
)
from openjiuwen.agent_evolving.skill_train.model_compat import get_target_backend


@dataclass(frozen=True)
class _AzureTurnRecord:
    system: str
    user: str
    reply: str
    transcript: list[dict]
    metadata: dict

    def to_outcome(self) -> _DialogueOutcome:
        tagged = has_answer_tag(self.reply)
        return _DialogueOutcome(
            system=self.system,
            user=self.user,
            response=self.reply,
            answer=extract_answer(self.reply) if tagged else "",
            conversation=self.transcript,
            fail_reason="" if tagged else "Model reply lacked a final <answer> tag",
            response_metadata=self.metadata,
        )


@dataclass(frozen=True)
class _AzurePromptBundle:
    system: str
    user: str

    def as_chat_payload(self) -> list[dict]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def _require_openai_chat_backend() -> None:
    if get_target_backend() != "openai_chat":
        raise ValueError("azure_search mode requires target_backend='openai_chat'")


def _compose_azure_prompts(
    item: dict,
    skill_content: str,
    probe_on: bool,
    probe_text: str,
) -> _AzurePromptBundle:
    user_text = build_user_prompt(
        item,
        diagnostic_mode=probe_on,
        diagnostic_instruction=probe_text,
        search_mode=MODE_AZURE,
    )
    system_text = build_system_prompt(skill_content, search_mode=MODE_AZURE)
    return _AzurePromptBundle(system=system_text, user=user_text)


def _invoke_native_web_search(
    prompts: _AzurePromptBundle,
    token_budget: int,
) -> tuple[str, list[dict], dict]:
    transcript: list[dict] = [{"role": "user", "content": prompts.user}]
    message, meta = invoke_chat(
        prompts.as_chat_payload(),
        max_completion_tokens=token_budget,
        tools=[{"type": "web_search"}],
    )
    reply = message.content or ""
    append_assistant_event(transcript, content=reply, metadata=meta)
    return reply, transcript, meta


def run_azure_dialogue(item: dict, skill_content: str, **options: Any) -> _DialogueOutcome:
    """Single-shot azure/native web-search dialogue.

    Accepted options: ``max_completion_tokens``, ``diagnostic_mode``,
    ``diagnostic_instruction`` (same names as historical callers).
    """
    _require_openai_chat_backend()
    token_budget = int(options.get("max_completion_tokens", 16384) or 16384)
    probe_on = bool(options.get("diagnostic_mode", False))
    probe_text = str(options.get("diagnostic_instruction", "") or "")
    prompts = _compose_azure_prompts(item, skill_content, probe_on, probe_text)
    reply, transcript, meta = _invoke_native_web_search(prompts, token_budget)
    return _AzureTurnRecord(
        system=prompts.system,
        user=prompts.user,
        reply=reply,
        transcript=transcript,
        metadata=meta,
    ).to_outcome()
