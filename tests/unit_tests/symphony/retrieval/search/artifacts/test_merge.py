"""Unit tests for retrieval.merge — covers append.py."""

from __future__ import annotations

import unittest

import openjiuwen.symphony.retrieval.search.artifacts.merge as _append_mod
from openjiuwen.symphony.retrieval.search.service.models import SearchResult

_format_append_summary_line = getattr(_append_mod, "_format_append_summary_line")
hits_to_search_result = _append_mod.hits_to_search_result


class FormatAppendSummaryLineTests(unittest.TestCase):
    def test_normal_record(self) -> None:
        record = {"choice_id": "c1", "resolved_payload": "p1"}
        line = _format_append_summary_line(1, record, "progressive")
        self.assertEqual(line, "1. c1 -> p1 (source=progressive)")

    def test_fallback_to_raw_output(self) -> None:
        record = {"raw_output": "r1", "resolved_payload": "p1"}
        line = _format_append_summary_line(2, record, "test")
        self.assertEqual(line, "2. r1 -> p1 (source=test)")

    def test_empty_fields(self) -> None:
        record = {}
        line = _format_append_summary_line(3, record, "src")
        self.assertEqual(line, "3.  ->  (source=src)")

    def test_index_increments(self) -> None:
        record = {"choice_id": "x", "resolved_payload": "y"}
        line = _format_append_summary_line(10, record, "src")
        self.assertTrue(line.startswith("10."))


class HitsToSearchResultTests(unittest.TestCase):
    def test_basic_construction(self) -> None:
        records = [
            {"choice_id": "c1", "resolved_payload": "p1"},
            {"choice_id": "c2", "resolved_payload": "p2"},
        ]
        result = hits_to_search_result(
            method="progressive",
            source="test",
            elapsed_ms=42.5,
            trace_events=[{"event": "x"}],
            candidate_records=records,
        )
        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.method, "progressive")
        self.assertEqual(result.payloads, ["p1", "p2"])
        self.assertEqual(len(result.candidate_records), 2)
        self.assertAlmostEqual(result.elapsed_ms, 42.5)
        self.assertEqual(len(result.trace_events), 1)

    def test_summary_lines(self) -> None:
        records = [
            {"choice_id": "c1", "resolved_payload": "p1"},
            {"choice_id": "c2", "resolved_payload": "p2"},
        ]
        result = hits_to_search_result(
            method="auto",
            source="src",
            elapsed_ms=0,
            trace_events=[],
            candidate_records=records,
        )
        self.assertEqual(len(result.summary_lines), 2)
        self.assertIn("c1", result.summary_lines[0])
        self.assertIn("p2", result.summary_lines[1])

    def test_selected_payload_is_first(self) -> None:
        records = [
            {"choice_id": "c1", "resolved_payload": "p1"},
            {"choice_id": "c2", "resolved_payload": "p2"},
        ]
        result = hits_to_search_result(
            method="progressive",
            source="src",
            elapsed_ms=0,
            trace_events=[],
            candidate_records=records,
        )
        self.assertEqual(result.selected_payload, "p1")
        self.assertEqual(result.selected_rank, 1)

    def test_empty_records(self) -> None:
        result = hits_to_search_result(
            method="auto",
            source="src",
            elapsed_ms=0,
            trace_events=[],
            candidate_records=[],
        )
        self.assertEqual(result.payloads, [])
        self.assertIsNone(result.selected_payload)
        self.assertEqual(result.selected_rank, -1)
        self.assertEqual(result.summary_lines, [])

    def test_single_record(self) -> None:
        records = [{"choice_id": "only", "resolved_payload": "p_only"}]
        result = hits_to_search_result(
            method="progressive",
            source="src",
            elapsed_ms=1.0,
            trace_events=[],
            candidate_records=records,
        )
        self.assertEqual(result.payloads, ["p_only"])
        self.assertEqual(result.selected_payload, "p_only")

    def test_empty_resolved_payload_produces_empty_string(self) -> None:
        records = [{"choice_id": "c1", "resolved_payload": ""}]
        result = hits_to_search_result(
            method="progressive",
            source="src",
            elapsed_ms=0,
            trace_events=[],
            candidate_records=records,
        )
        self.assertEqual(result.payloads, [""])

    def test_elapsed_ms_is_float(self) -> None:
        result = hits_to_search_result(
            method="auto",
            source="src",
            elapsed_ms=10,
            trace_events=[],
            candidate_records=[],
        )
        self.assertIsInstance(result.elapsed_ms, float)
        self.assertAlmostEqual(result.elapsed_ms, 10.0)


if __name__ == "__main__":
    unittest.main()
