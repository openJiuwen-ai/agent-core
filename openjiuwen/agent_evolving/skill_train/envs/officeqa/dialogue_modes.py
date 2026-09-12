# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Select the OfficeQA dialogue runner for a normalized search mode."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.agent_evolving.skill_train.envs.officeqa.azure_loop import run_azure_dialogue
from openjiuwen.agent_evolving.skill_train.envs.officeqa.custom_loop import run_custom_dialogue
from openjiuwen.agent_evolving.skill_train.envs.officeqa.offline_loop import (
    run_offline_plain_dialogue,
    run_offline_tools_dialogue,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.outcome import _DialogueOutcome
from openjiuwen.agent_evolving.skill_train.envs.officeqa.prompt_builders import (
    MODE_AZURE,
    MODE_CUSTOM,
    MODE_OFFLINE,
    SEARCH_AZURE,
    SEARCH_CUSTOM,
    SEARCH_OFFLINE,
    build_system_prompt,
    build_user_prompt,
    extract_answer,
    extract_search_queries,
    has_answer_tag,
    normalize_search_mode,
)


@dataclass(frozen=True)
class _DispatchContext:
    """Packed dialogue options passed to mode-specific runners."""

    skill_content: str
    limits: tuple[int, int, int]  # tool turns, completion tokens, queries/turn
    lookup: tuple[str, str, str, int, int] | None  # url, auth, provider, hits, timeout
    probe: tuple[bool, str]
    corpus: tuple[list[str], list[str], str] | None  # candidates, roots, oracle
    local_tools: bool = True


def _run_from_context(item: dict, ctx: _DispatchContext, mode: str) -> _DialogueOutcome:
    tool_cap, token_cap, query_cap = ctx.limits
    diag_on, diag_text = ctx.probe

    match mode:
        case _ if mode == MODE_CUSTOM:
            if ctx.lookup is None or ctx.corpus is None:
                raise ValueError("custom_search dispatch requires lookup and corpus context")
            url, auth, provider, hits, timeout = ctx.lookup
            _, _, oracle = ctx.corpus
            return run_custom_dialogue(
                item,
                ctx.skill_content,
                max_tool_turns=tool_cap,
                max_completion_tokens=token_cap,
                max_queries_per_turn=query_cap,
                diagnostic_mode=diag_on,
                diagnostic_instruction=diag_text,
                search_api_url=url,
                search_auth_env=auth,
                search_provider=provider,
                search_max_num_results=hits,
                search_timeout_seconds=timeout,
                oracle_context=oracle,
            )
        case _ if mode == MODE_AZURE:
            return run_azure_dialogue(
                item,
                ctx.skill_content,
                max_completion_tokens=token_cap,
                diagnostic_mode=diag_on,
                diagnostic_instruction=diag_text,
            )
        case _:
            if ctx.corpus is None:
                raise ValueError("offline dispatch requires corpus context")
            candidates, roots, oracle = ctx.corpus
            if ctx.local_tools:
                return run_offline_tools_dialogue(
                    item,
                    ctx.skill_content,
                    max_tool_turns=tool_cap,
                    max_completion_tokens=token_cap,
                    diagnostic_mode=diag_on,
                    diagnostic_instruction=diag_text,
                    candidate_files=candidates,
                    docs_roots=roots,
                    oracle_context=oracle,
                )
            return run_offline_plain_dialogue(
                item,
                ctx.skill_content,
                max_completion_tokens=token_cap,
                diagnostic_mode=diag_on,
                diagnostic_instruction=diag_text,
                candidate_files=candidates,
                oracle_context=oracle,
            )


def dispatch_dialogue(
    item: dict,
    skill_content: str,
    *,
    mode: str,
    max_tool_turns: int,
    max_completion_tokens: int,
    max_queries_per_turn: int,
    search_api_url: str,
    search_auth_env: str,
    search_provider: str,
    search_max_num_results: int,
    search_timeout_seconds: int,
    use_local_tools: bool,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    candidate_files: list[str],
    docs_roots: list[str],
    oracle_context: str,
) -> _DialogueOutcome:
    """Pick azure / custom / offline dialogue implementation for ``mode``."""
    normalized = normalize_search_mode(mode)
    lookup_pack = (
        search_api_url,
        search_auth_env,
        search_provider,
        search_max_num_results,
        search_timeout_seconds,
    )
    ctx = _DispatchContext(
        skill_content=skill_content,
        limits=(max_tool_turns, max_completion_tokens, max_queries_per_turn),
        lookup=lookup_pack if normalized == MODE_CUSTOM else None,
        probe=(diagnostic_mode, diagnostic_instruction),
        corpus=(candidate_files, docs_roots, oracle_context),
        local_tools=use_local_tools,
    )
    return _run_from_context(item, ctx, normalized)


__all__ = [
    "MODE_AZURE",
    "MODE_CUSTOM",
    "MODE_OFFLINE",
    "SEARCH_AZURE",
    "SEARCH_CUSTOM",
    "SEARCH_OFFLINE",
    "_DialogueOutcome",
    "build_system_prompt",
    "build_user_prompt",
    "dispatch_dialogue",
    "extract_answer",
    "extract_search_queries",
    "has_answer_tag",
    "normalize_search_mode",
    "run_azure_dialogue",
    "run_custom_dialogue",
    "run_offline_plain_dialogue",
    "run_offline_tools_dialogue",
]
