# coding: utf-8
"""Tests for contract drafting and parsing (方案一 完成契约机制)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.goal.contract_parser import (
    _parse_contract_json,
    draft_contract,
    parse_contract_from_text,
)


# -- _parse_contract_json: three-layer fallback --


def test_parse_contract_json_bare_object() -> None:
    assert _parse_contract_json('{"outcome": "x", "verification": "y"}') == {
        "outcome": "x",
        "verification": "y",
    }


def test_parse_contract_json_fenced_block() -> None:
    assert _parse_contract_json('```json\n{"outcome": "x"}\n```') == {"outcome": "x"}


def test_parse_contract_json_nested_code_block_in_value() -> None:
    raw = '```json\n{"verification": "see ```python\\nprs = Presentation()\\n```\\ndone"}\n```'
    parsed = _parse_contract_json(raw)
    assert parsed is not None
    assert "```python" in parsed["verification"]


def test_parse_contract_json_invalid_returns_none() -> None:
    assert _parse_contract_json("not json") is None
    assert _parse_contract_json("") is None


# -- parse_contract_from_text: inline extraction --


def test_parse_contract_from_text_extracts_fields_and_strips_objective() -> None:
    text = (
        "Migrate auth to JWT\n"
        "verify: the auth test suite passes\n"
        "constraints: keep the public /login response shape unchanged\n"
        "boundaries: only touch services/auth and its tests\n"
        "stop when: a schema change needs product sign-off"
    )
    objective, contract = parse_contract_from_text(text)
    assert objective == "Migrate auth to JWT"
    assert contract.verification == "the auth test suite passes"
    assert contract.constraints == "keep the public /login response shape unchanged"
    assert contract.boundaries == "only touch services/auth and its tests"
    assert contract.stop_when == "a schema change needs product sign-off"
    assert not contract.is_empty()


def test_parse_contract_from_text_no_fields_returns_original_and_empty_contract() -> None:
    text = "Just a vague objective with no inline fields"
    objective, contract = parse_contract_from_text(text)
    assert objective == text
    assert contract.is_empty()


def test_parse_contract_from_text_case_insensitive_verify_alias() -> None:
    text = "Fix bug\nVERIFY: tests pass"
    objective, contract = parse_contract_from_text(text)
    assert objective == "Fix bug"
    assert contract.verification == "tests pass"


# -- draft_contract: auxiliary LLM call with degradation --


@pytest.mark.asyncio
async def test_draft_contract_success_parses_json() -> None:
    model = MagicMock()
    model.invoke = AsyncMock(
        return_value=MagicMock(
            content='{"outcome": "bug fixed", "verification": "tests pass"}'
        )
    )
    contract = await draft_contract("Fix bug", model, "cn")
    assert contract.outcome == "bug fixed"
    assert contract.verification == "tests pass"
    assert not contract.is_empty()
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_contract_failure_degrades_to_empty_contract() -> None:
    model = MagicMock()
    model.invoke = AsyncMock(side_effect=RuntimeError("model down"))
    contract = await draft_contract("Fix bug", model, "cn")
    assert contract.is_empty()


@pytest.mark.asyncio
async def test_draft_contract_unparseable_response_degrades_to_empty() -> None:
    model = MagicMock()
    model.invoke = AsyncMock(return_value=MagicMock(content="not json at all"))
    contract = await draft_contract("Fix bug", model, "cn")
    assert contract.is_empty()


@pytest.mark.asyncio
async def test_draft_contract_empty_objective_returns_empty_without_call() -> None:
    model = MagicMock()
    model.invoke = AsyncMock()
    contract = await draft_contract("   ", model, "cn")
    assert contract.is_empty()
    model.invoke.assert_not_awaited()
