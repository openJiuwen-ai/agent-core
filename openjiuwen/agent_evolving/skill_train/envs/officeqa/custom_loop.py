# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Controller-mediated web lookup dialogue for OfficeQA custom_search mode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from openjiuwen.agent_evolving.skill_train.envs.officeqa.chat_support import (
    append_assistant_event,
    invoke_chat,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.outcome import _DialogueOutcome
from openjiuwen.agent_evolving.skill_train.envs.officeqa.prompt_builders import (
    MODE_CUSTOM,
    build_system_prompt,
    build_user_prompt,
    extract_answer,
    extract_search_queries,
    has_answer_tag,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.tool_runtime import custom_search
from openjiuwen.agent_evolving.skill_train.model_compat import get_target_backend


@dataclass(frozen=True)
class _LookupEndpoint:
    """HTTP lookup endpoint knobs used by the controller."""

    url: str
    auth_env: str
    provider: str
    hit_limit: int
    deadline_s: int


@dataclass
class _SessionScratch:
    """Mutable buffers for one custom-search dialogue."""

    system: str
    user: str
    transcript: list[dict]
    events: list[dict]
    reply: str = ""
    meta: dict = field(default_factory=dict)
    stop_reason: str = ""


def _require_custom_prereqs(endpoint: _LookupEndpoint) -> None:
    if not endpoint.url.strip():
        raise ValueError("custom_search mode needs search_api_url")
    if not os.environ.get(endpoint.auth_env, "").strip():
        raise ValueError(f"custom_search mode needs env token {endpoint.auth_env}")
    backend = get_target_backend()
    if backend not in {"openai_chat", "qwen_chat"}:
        raise ValueError(
            "custom_search mode needs target_backend openai_chat or qwen_chat "
            f"(got {backend!r})"
        )


def _lookup_bundle(terms: list[str], endpoint: _LookupEndpoint) -> str:
    sections: list[str] = []
    for index, term in enumerate(terms, start=1):
        try:
            body = custom_search(
                term,
                api_url=endpoint.url,
                auth_env=endpoint.auth_env,
                provider=endpoint.provider,
                max_num_results=endpoint.hit_limit,
                timeout=endpoint.deadline_s,
            )
        except Exception as exc:  # noqa: BLE001
            body = f"term={term!r}\ncontroller-error={exc}"
        sections.append(f"## Lookup {index}\n{body}")
    return "\n\n".join(sections)


def _cap_terms(terms: list[str], limit: int) -> list[str]:
    if len(terms) <= limit:
        return list(terms)
    end = limit
    return list(terms)[0:end]


def _closing_hint(*, round_no: int, round_cap: int, term_cap: int) -> str:
    if round_no >= round_cap:
        return (
            f"## Controller note\n"
            f"Round {round_no}/{round_cap} is the last model call. "
            "Emit `<answer>...</answer>` only."
        )
    remain = round_cap - round_no
    return (
        f"## Controller note\n"
        f"Round {round_no}/{round_cap}. "
        f"Either finish with `<answer>...</answer>`, or ask for at most "
        f"{term_cap} terms via `<search_queries>...</search_queries>`. "
        f"{remain} call(s) left after this."
    )


def _finish_ok(scratch: _SessionScratch) -> _DialogueOutcome:
    return _DialogueOutcome(
        system=scratch.system,
        user=scratch.user,
        response=scratch.reply,
        answer=extract_answer(scratch.reply),
        conversation=scratch.events,
        fail_reason="",
        response_metadata=scratch.meta,
    )


def _finish_fail(scratch: _SessionScratch) -> _DialogueOutcome:
    return _DialogueOutcome(
        system=scratch.system,
        user=scratch.user,
        response=scratch.reply,
        answer="",
        conversation=scratch.events,
        fail_reason=scratch.stop_reason,
        response_metadata=scratch.meta,
    )


def _boot_session(
    item: dict,
    skill_content: str,
    *,
    round_cap: int,
    term_cap: int,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    oracle_context: str,
) -> _SessionScratch:
    system = build_system_prompt(
        skill_content,
        search_mode=MODE_CUSTOM,
        max_tool_turns=round_cap,
        max_queries_per_turn=term_cap,
    )
    user = build_user_prompt(
        item,
        diagnostic_mode=diagnostic_mode,
        diagnostic_instruction=diagnostic_instruction,
        search_mode=MODE_CUSTOM,
        turn=1,
        max_tool_turns=round_cap,
        max_queries_per_turn=term_cap,
        oracle_context=oracle_context,
    )
    return _SessionScratch(
        system=system,
        user=user,
        transcript=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        events=[{"role": "user", "content": user}],
    )


def _ingest_model_turn(
    scratch: _SessionScratch,
    *,
    round_no: int,
    token_cap: int,
) -> None:
    message, meta = invoke_chat(scratch.transcript, max_completion_tokens=token_cap)
    scratch.reply = message.content or ""
    scratch.meta = meta
    scratch.transcript.append({"role": "assistant", "content": scratch.reply})
    append_assistant_event(
        scratch.events,
        content=scratch.reply,
        turn=round_no,
        metadata=meta,
    )


def _push_lookup_followup(
    scratch: _SessionScratch,
    *,
    round_no: int,
    round_cap: int,
    term_cap: int,
    terms: list[str],
    dump: str,
) -> None:
    scratch.events.append(
        {
            "type": "tool_call",
            "turn": round_no,
            "cmd": f"custom_search({terms!r})",
            "obs": dump,
        }
    )
    nxt = round_no + 1
    follow = (
        f"## Lookup dump (after round {round_no})\n{dump}\n\n"
        + _closing_hint(round_no=nxt, round_cap=round_cap, term_cap=term_cap)
        + "\n\nFollow the controller note."
    )
    scratch.user = follow
    scratch.transcript.append({"role": "user", "content": follow})
    scratch.events.append({"role": "user", "turn": nxt, "content": follow})


def run_custom_dialogue(
    item: dict,
    skill_content: str,
    *,
    max_tool_turns: int,
    max_completion_tokens: int,
    max_queries_per_turn: int,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    search_api_url: str,
    search_auth_env: str,
    search_provider: str,
    search_max_num_results: int,
    search_timeout_seconds: int,
    oracle_context: str = "",
) -> _DialogueOutcome:
    endpoint = _LookupEndpoint(
        url=str(search_api_url or ""),
        auth_env=str(search_auth_env or ""),
        provider=str(search_provider or ""),
        hit_limit=int(search_max_num_results),
        deadline_s=int(search_timeout_seconds),
    )
    _require_custom_prereqs(endpoint)

    round_cap = int(max_tool_turns)
    term_cap = int(max_queries_per_turn)
    scratch = _boot_session(
        item,
        skill_content,
        round_cap=round_cap,
        term_cap=term_cap,
        diagnostic_mode=diagnostic_mode,
        diagnostic_instruction=diagnostic_instruction,
        oracle_context=oracle_context,
    )

    round_no = 1
    while round_no <= round_cap:
        _ingest_model_turn(scratch, round_no=round_no, token_cap=max_completion_tokens)
        if has_answer_tag(scratch.reply):
            return _finish_ok(scratch)

        if round_no >= round_cap:
            scratch.stop_reason = (
                f"Last model round ({round_cap}) finished without <answer>...</answer>"
            )
            break

        terms = _cap_terms(extract_search_queries(scratch.reply), term_cap)
        if not terms:
            scratch.stop_reason = "Model returned neither lookup terms nor a final answer"
            break

        dump = _lookup_bundle(terms, endpoint)
        _push_lookup_followup(
            scratch,
            round_no=round_no,
            round_cap=round_cap,
            term_cap=term_cap,
            terms=terms,
            dump=dump,
        )
        round_no += 1

    return _finish_fail(scratch)
