# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for LLM-safe tool name sanitization (OpenAI function calling)."""

from __future__ import annotations

from openjiuwen.core.foundation.tool.name_sanitize import (
    LLM_TOOL_NAME_PATTERN,
    is_llm_safe_tool_name,
    sanitize_llm_tool_name,
)


def test_compliant_name_passes_through_unchanged() -> None:
    assert sanitize_llm_tool_name("aippt_doc_beautify") == "aippt_doc_beautify"
    assert sanitize_llm_tool_name("web-search") == "web-search"
    assert sanitize_llm_tool_name("mcp_aippt_doc_beautify") == "mcp_aippt_doc_beautify"


def test_is_llm_safe_tool_name_matches_openai_rule() -> None:
    assert is_llm_safe_tool_name("aippt_doc_beautify") is True
    assert is_llm_safe_tool_name("aippt.doc_beautify") is False
    assert is_llm_safe_tool_name("") is False
    assert is_llm_safe_tool_name("a" * 65) is False
    assert is_llm_safe_tool_name("a" * 64) is True
    assert LLM_TOOL_NAME_PATTERN.fullmatch("A9_-") is not None


def test_illegal_characters_collapse_to_underscore() -> None:
    assert sanitize_llm_tool_name("aippt.doc_beautify") == "aippt_doc_beautify"
    assert sanitize_llm_tool_name("dbsheet.create_fields") == "dbsheet_create_fields"
    assert sanitize_llm_tool_name("金山文档.aippt") == "aippt"
    assert sanitize_llm_tool_name("...") == "tool"


def test_overlong_name_gets_digest_suffix_and_stays_in_limit() -> None:
    long_name = "mcp_server_" + "t" * 80
    result = sanitize_llm_tool_name(long_name)
    assert len(result) <= 64
    assert LLM_TOOL_NAME_PATTERN.fullmatch(result) is not None
    assert result.startswith("mcp_server_")
    # Deterministic: same input maps to the same output.
    assert result == sanitize_llm_tool_name(long_name)


def test_collision_with_taken_names_is_disambiguated() -> None:
    taken = {"aippt_doc_beautify"}
    result = sanitize_llm_tool_name("aippt.doc_beautify", taken=taken)
    assert result != "aippt_doc_beautify"
    assert LLM_TOOL_NAME_PATTERN.fullmatch(result) is not None
    assert result not in taken
    # The caller can add the result back and keep requesting new names safely.
    taken.add(result)
    other = sanitize_llm_tool_name("aippt_doc.beautify", taken=taken)
    assert other not in taken
    assert other != result


def test_sanitized_name_never_shadows_a_taken_raw_key() -> None:
    # Invariant relied on by AbilityManager: a sanitized name never equals any
    # registered raw key, so alias lookups stay unambiguous.
    taken = {"note_create", "web_search"}
    assert sanitize_llm_tool_name("note.create", taken=taken) not in taken
    assert sanitize_llm_tool_name("web_search", taken=taken) != "web_search"


def test_idempotent_for_already_sanitized_names() -> None:
    first = sanitize_llm_tool_name("aippt.doc_beautify")
    assert sanitize_llm_tool_name(first) == first
