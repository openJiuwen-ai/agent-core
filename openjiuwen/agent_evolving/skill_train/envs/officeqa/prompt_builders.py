# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt assembly and tag parsing for OfficeQA dialogue modes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import wraps

from openjiuwen.agent_evolving.skill_train.envs.io_helpers import format_skill_section
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt

MODE_OFFLINE = "offline"
MODE_CUSTOM = "custom_search"
MODE_AZURE = "azure_search"

_TAG_ANSWER = re.compile(r"<answer>(.*?)</answer>", re.IGNORECASE | re.DOTALL)
_TAG_QUERIES = re.compile(r"<search_queries>(.*?)</search_queries>", re.IGNORECASE | re.DOTALL)


def normalize_search_mode(search_mode: str | None) -> str:
    token = str(search_mode or MODE_OFFLINE).strip().lower()
    aliases = {
        "custom": MODE_CUSTOM,
        MODE_CUSTOM: MODE_CUSTOM,
        "azure": MODE_AZURE,
        MODE_AZURE: MODE_AZURE,
    }
    return aliases.get(token, MODE_OFFLINE)


def _bullet_section(title: str, values: list[str] | None, *, empty_line: str) -> str:
    bullets = [f"* {value}" for value in (values or []) if str(value).strip()]
    body = "\n".join(bullets) if bullets else empty_line
    return f"### {title}\n{body}"


def _step_policy_block(*, step: int, budget: int, query_limit: int) -> str:
    header = "### Step policy\n"
    if step >= budget:
        return (
            header
            + f"Final model step ({step}/{budget}). "
            + "Respond with `<answer>...</answer>` only — omit `<search_queries>`."
        )
    remaining = budget - step
    return (
        header
        + f"Turn {step}/{budget}. "
        + f"Close with `<answer>...</answer>` or issue up to {query_limit} "
        + f"queries in `<search_queries>...</search_queries>`. "
        + f"{remaining} step(s) left after this reply."
    )


def _azure_system(skill_block: str) -> str:
    preface = (
        "Role: OfficeQA analyst with access to the model's native web search. "
        "When local hints are thin, call that search, prefer authoritative sources, "
        "and enclose the final value in <answer>...</answer>."
    )
    return f"{preface}\n\n{skill_block}".rstrip()


def _custom_system(skill_block: str, *, budget: int, query_limit: int) -> str:
    contract = (
        "Role: OfficeQA analyst driven by oracle page excerpts plus controller "
        "custom-search evidence.\n"
        "Loop rules:\n"
        f"- Model steps available: {budget}.\n"
        f"- Before the last step you may emit "
        f'`<search_queries>["q1", "q2"]</search_queries>` '
        f"(at most {query_limit} queries) XOR `<answer>...</answer>`.\n"
        "- Never combine a search request and a final answer in one reply.\n"
        "- On the last step emit only `<answer>...</answer>`.\n"
        "- Prefer concise answers that reconcile conflicting snippets.\n\n"
    )
    return contract + skill_block + "When finished, wrap the value in <answer>...</answer>."


def _offline_plain_system(skill_block: str) -> str:
    preface = (
        "Role: OfficeQA analyst limited to oracle page excerpts and source hints. "
        "Do not invent external search or local function tools. "
        "Put the final value inside <answer>...</answer>."
    )
    return f"{preface}\n\n{skill_block}".rstrip()


_SYSTEM_BUILDERS: dict[str, Callable[..., str]] = {}


def _register_system(mode: str):
    def _decorator(fn: Callable[..., str]) -> Callable[..., str]:
        @wraps(fn)
        def _bound(*args: object, **kwargs: object) -> str:
            return fn(*args, **kwargs)

        _SYSTEM_BUILDERS[mode] = _bound
        return _bound

    return _decorator


@_register_system(MODE_AZURE)
def _build_azure(skill_block: str, **_kwargs: object) -> str:
    return _azure_system(skill_block)


@_register_system(MODE_CUSTOM)
def _build_custom(
    skill_block: str,
    *,
    max_tool_turns: int = 12,
    max_queries_per_turn: int = 4,
    **_kwargs: object,
) -> str:
    return _custom_system(skill_block, budget=max_tool_turns, query_limit=max_queries_per_turn)


def build_system_prompt(
    skill_content: str,
    *,
    search_mode: str = MODE_OFFLINE,
    use_local_tools: bool = True,
    max_tool_turns: int = 12,
    max_queries_per_turn: int = 4,
) -> str:
    skill_block = format_skill_section(skill_content)
    mode = normalize_search_mode(search_mode)
    builder = _SYSTEM_BUILDERS.get(mode)
    if builder is not None:
        return builder(
            skill_block,
            max_tool_turns=max_tool_turns,
            max_queries_per_turn=max_queries_per_turn,
        )
    if not use_local_tools:
        return _offline_plain_system(skill_block)
    return load_prompt("rollout_system", env="officeqa").format(skill_section=skill_block)


def _cap_file_list(files: list[str] | None, *, limit: int = 20) -> list[str]:
    capped = list(files or [])
    if len(capped) > limit:
        return capped[:limit]
    return capped


def _append_mode_contract(
    sections: list[str],
    *,
    mode: str,
    turn: int,
    max_tool_turns: int,
    max_queries_per_turn: int,
) -> None:
    if mode == MODE_CUSTOM:
        sections.append(
            _step_policy_block(
                step=turn,
                budget=max_tool_turns,
                query_limit=max_queries_per_turn,
            )
        )
        sections.append(
            "### Output contract\n"
            "Need more evidence → emit only `<search_queries>[...]</search_queries>`.\n"
            "Ready to answer → emit only `<answer>...</answer>`.\n"
            "Use only oracle pages and controller-supplied custom search hits; "
            "ignore any built-in web search."
        )
        return
    if mode == MODE_AZURE:
        sections.append(
            "Call the model's built-in web search when useful, then return "
            "<answer>...</answer>."
        )


def _assemble_user_sections(
    item: dict,
    candidate_files: list[str] | None,
    *,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
    corpus_note: str,
    mode: str,
    turn: int,
    max_tool_turns: int,
    max_queries_per_turn: int,
    oracle_context: str,
) -> list[str]:
    sections: list[str] = [f"### Question\n{item['question']}"]

    oracle_text = oracle_context.strip()
    if oracle_text:
        sections.append(f"### Oracle parsed pages\n{oracle_text}")

    if mode == MODE_OFFLINE:
        if corpus_note.strip():
            sections.append(f"### Document corpus\n{corpus_note.strip()}")
        sections.append(
            _bullet_section(
                "Candidate files",
                _cap_file_list(candidate_files),
                empty_line="* (none resolved)",
            )
        )

    source_docs = item.get("source_docs")
    if source_docs:
        sections.append(_bullet_section("Source hints", list(source_docs), empty_line="* (none)"))

    if mode != MODE_OFFLINE and item.get("source_files"):
        sections.append(_bullet_section("File hints", list(item["source_files"]), empty_line="* (none)"))

    if diagnostic_mode and diagnostic_instruction.strip():
        sections.append(f"### Training readout\n{diagnostic_instruction.strip()}")

    _append_mode_contract(
        sections,
        mode=mode,
        turn=turn,
        max_tool_turns=max_tool_turns,
        max_queries_per_turn=max_queries_per_turn,
    )
    return sections


def build_user_prompt(
    item: dict,
    candidate_files: list[str] | None = None,
    *,
    diagnostic_mode: bool = False,
    diagnostic_instruction: str = "",
    corpus_note: str = "",
    search_mode: str = MODE_OFFLINE,
    turn: int = 1,
    max_tool_turns: int = 12,
    max_queries_per_turn: int = 4,
    oracle_context: str = "",
) -> str:
    mode = normalize_search_mode(search_mode)
    sections = _assemble_user_sections(
        item,
        candidate_files,
        diagnostic_mode=diagnostic_mode,
        diagnostic_instruction=diagnostic_instruction,
        corpus_note=corpus_note,
        mode=mode,
        turn=turn,
        max_tool_turns=max_tool_turns,
        max_queries_per_turn=max_queries_per_turn,
        oracle_context=oracle_context,
    )
    return "\n\n".join(sections)


def extract_answer(text: str) -> str:
    match = _TAG_ANSWER.search(text)
    if match:
        return match.group(1).strip()
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    return nonempty[-1] if nonempty else text.strip()


def has_answer_tag(text: str) -> bool:
    return "<answer>" in text.lower()


def _coerce_query_values(value: object) -> list[str] | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else None
    if isinstance(value, list):
        rows = [str(item).strip() for item in value if str(item).strip()]
        return rows or None
    return None


_QUERY_FIELD_EXTRACTORS: dict[str, Callable[[object], list[str] | None]] = {
    "queries": _coerce_query_values,
    "search_queries": _coerce_query_values,
    "query": _coerce_query_values,
}


def _queries_from_mapping(payload: dict) -> list[str]:
    for field, extractor in _QUERY_FIELD_EXTRACTORS.items():
        if field not in payload:
            continue
        rows = extractor(payload[field])
        if rows:
            return rows
    return []


def _decode_query_payload(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        return _queries_from_mapping(parsed)
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _split_csv_singleton(queries: list[str]) -> list[str]:
    if len(queries) != 1:
        return queries
    pieces = [part.strip(" \"'") for part in re.split(r"[;,]", queries[0]) if part.strip(" \"'")]
    return pieces if len(pieces) > 1 else queries


def _dedupe_preserve(queries: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned = query.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def extract_search_queries(text: str) -> list[str]:
    match = _TAG_QUERIES.search(text or "")
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    parsed = _decode_query_payload(raw)
    if not parsed:
        parsed = [
            line.strip(" -*\t\r\n\"'")
            for line in raw.splitlines()
            if line.strip(" -*\t\r\n\"'")
        ]
    return _dedupe_preserve(_split_csv_singleton(parsed))


# Back-compat aliases used by older imports / rollout.
SEARCH_OFFLINE = MODE_OFFLINE
SEARCH_CUSTOM = MODE_CUSTOM
SEARCH_AZURE = MODE_AZURE
