# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline OfficeQA dialogue runners (plain LLM and local-tool loop)."""

from __future__ import annotations

import os
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.officeqa.chat_support import (
    LOCAL_TOOL_SCHEMAS,
    append_assistant_event,
    invoke_chat,
    parse_tool_arguments,
    tool_calls_openai_shape,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.outcome import _DialogueOutcome
from openjiuwen.agent_evolving.skill_train.envs.officeqa.prompt_builders import (
    MODE_OFFLINE,
    build_system_prompt,
    build_user_prompt,
    extract_answer,
    has_answer_tag,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.tool_runtime import run_tool


def run_offline_plain_dialogue(
    item: dict,
    skill_content: str,
    *,
    max_completion_tokens: int,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    candidate_files: list[str],
    oracle_context: str = "",
) -> _DialogueOutcome:
    system = build_system_prompt(skill_content, search_mode=MODE_OFFLINE, use_local_tools=False)
    user = build_user_prompt(
        item,
        candidate_files,
        diagnostic_mode=diagnostic_mode,
        diagnostic_instruction=diagnostic_instruction,
        search_mode=MODE_OFFLINE,
        oracle_context=oracle_context,
    )
    transcript = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    log: list[dict] = [{"role": "user", "content": user}]
    message, meta = invoke_chat(transcript, max_completion_tokens=max_completion_tokens)
    text = message.content or ""
    append_assistant_event(log, content=text, metadata=meta)

    ok = has_answer_tag(text)
    return _DialogueOutcome(
        system=system,
        user=user,
        response=text,
        answer=extract_answer(text) if ok else "",
        conversation=log,
        fail_reason="" if ok else "Model did not produce a final answer",
        response_metadata=meta,
    )


def run_offline_tools_dialogue(
    item: dict,
    skill_content: str,
    *,
    max_tool_turns: int,
    max_completion_tokens: int,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    candidate_files: list[str],
    docs_roots: list[str],
    oracle_context: str = "",
) -> _DialogueOutcome:
    system = build_system_prompt(skill_content, search_mode=MODE_OFFLINE, use_local_tools=True)
    user = build_user_prompt(
        item,
        candidate_files,
        diagnostic_mode=diagnostic_mode,
        diagnostic_instruction=diagnostic_instruction,
        search_mode=MODE_OFFLINE,
        oracle_context=oracle_context,
    )
    transcript: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    log: list[dict] = [{"role": "user", "content": user}]
    allowed = [os.path.basename(path) for path in candidate_files]
    last_text = ""
    answer = ""
    fail = ""
    meta: dict = {}

    for step in range(1, max_tool_turns + 1):
        message, meta = invoke_chat(
            transcript,
            max_completion_tokens=max_completion_tokens,
            tools=LOCAL_TOOL_SCHEMAS,
            tool_choice="auto",
        )
        last_text = message.content or ""
        calls = tool_calls_openai_shape(message)

        assistant_row: dict[str, Any] = {"role": "assistant", "content": last_text}
        if calls:
            assistant_row["tool_calls"] = calls
        transcript.append(assistant_row)
        log.append({"type": "message", "content": last_text})

        if calls:
            for call in calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                args = parse_tool_arguments(fn.get("arguments") or "{}")
                cmd, obs = run_tool(
                    name,
                    args,
                    allowed_roots=docs_roots,
                    allowed_files=allowed,
                )
                log.append({"type": "tool_call", "cmd": cmd, "obs": obs})
                transcript.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": obs,
                    }
                )
            continue

        if has_answer_tag(last_text):
            answer = extract_answer(last_text)
            fail = ""
            break

        if step >= max_tool_turns:
            fail = f"Exceeded tool-turn budget ({max_tool_turns})"
        else:
            fail = "Model neither produced a tool request nor a final answer"
            break

    return _DialogueOutcome(
        system=system,
        user=user,
        response=last_text,
        answer=answer,
        conversation=log,
        fail_reason=fail,
        response_metadata=meta,
    )
