# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for openjiuwen.extensions.context_evolver.offline_memory.bank_io.

Pure filesystem I/O -- no LLM, no mocking needed; uses pytest's tmp_path
for isolation.
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.extensions.context_evolver.offline_memory import bank_io


class TestTextRoundTrip:
    def test_missing_file_returns_empty_string(self, tmp_path: Path) -> None:
        assert bank_io.load_text(tmp_path / "missing.md") == ""

    def test_save_then_load_strips_and_appends_newline(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.md"
        bank_io.save_text(path, "  hello world  ")
        assert path.read_text(encoding="utf-8") == "hello world\n"
        assert bank_io.load_text(path) == "hello world"


class TestYamlRoundTrip:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert bank_io.load_yaml(tmp_path / "missing.yaml") == {}

    def test_save_then_load(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "profile.yaml"
        data = {"reliability": "high", "strengths": ["fast", "thorough"]}
        bank_io.save_yaml(path, data)
        assert bank_io.load_yaml(path) == data


class TestJsonlRoundTrip:
    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        assert bank_io.load_jsonl(tmp_path / "missing.jsonl") == []

    def test_append_then_load_preserves_order(self, tmp_path: Path) -> None:
        path = tmp_path / "compositions.jsonl"
        bank_io.append_jsonl(path, [{"sample_id": "a"}, {"sample_id": "b"}])
        bank_io.append_jsonl(path, [{"sample_id": "c"}])
        assert bank_io.load_jsonl(path) == [{"sample_id": "a"}, {"sample_id": "b"}, {"sample_id": "c"}]

    def test_append_empty_rows_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "compositions.jsonl"
        bank_io.append_jsonl(path, [])
        assert not path.exists()


class TestJsonRoundTrip:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert bank_io.load_json(tmp_path / "missing.json") == {}

    def test_save_then_load(self, tmp_path: Path) -> None:
        path = tmp_path / "outcome_stats_general.json"
        data = {"window": [0.5, 0.8], "window_size": 20}
        bank_io.save_json(path, data)
        assert bank_io.load_json(path) == data


class TestNewItemId:
    def test_avoids_collision_with_existing_keys(self) -> None:
        existing = {"aaaaaaaa": {}}
        new_id = bank_io.new_item_id(existing)
        assert new_id not in existing
        assert len(new_id) == 8

    def test_handles_none(self) -> None:
        assert len(bank_io.new_item_id(None)) == 8


class TestLedgerRoundTrip:
    def test_missing_file_returns_empty_set(self, tmp_path: Path) -> None:
        assert bank_io.load_ledger(tmp_path / "missing.json") == set()

    def test_save_then_load_is_sorted_and_deduped(self, tmp_path: Path) -> None:
        path = tmp_path / ".processed_l2.json"
        bank_io.save_ledger(path, {"b", "a", "a"})
        assert bank_io.load_ledger(path) == {"a", "b"}


class TestMergeProfile:
    def test_scalar_fields_overwritten_by_new(self) -> None:
        old = {"reliability": "medium"}
        new = {"reliability": "high"}
        assert bank_io.merge_profile(old, new)["reliability"] == "high"

    def test_list_fields_unioned_and_capped(self) -> None:
        old = {"strengths": ["fast", "thorough"]}
        new = {"strengths": ["thorough", "careful", "clear", "concise", "proactive"]}
        merged = bank_io.merge_profile(old, new, list_cap=3)
        # "thorough" is deduped, insertion order preserved, capped at 3.
        assert merged["strengths"] == ["fast", "thorough", "careful"]

    def test_new_field_not_in_old_is_added(self) -> None:
        merged = bank_io.merge_profile({}, {"communication_style": "terse"})
        assert merged["communication_style"] == "terse"

    def test_does_not_mutate_old_dict(self) -> None:
        old = {"strengths": ["fast"]}
        bank_io.merge_profile(old, {"strengths": ["thorough"]})
        assert old["strengths"] == ["fast"]
