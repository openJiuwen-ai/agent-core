# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE self-normalized BM25 similarity helpers."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.ttse.bm25_sim import (
    DEFAULT_BM25_SIM_THRESHOLD,
    bm25_best_match,
    bm25_one_way_scores,
    pairwise_bm25_sims,
)


def test_default_threshold_is_half():
    assert DEFAULT_BM25_SIM_THRESHOLD == 0.5


def test_one_way_identical_is_one():
    text = "grader checks case sensitivity on csv"
    scores = bm25_one_way_scores(text, [text])
    assert len(scores) == 1
    assert scores[0] == pytest.approx(1.0)


def test_one_way_unrelated_near_zero():
    query = "grader checks case sensitivity on csv"
    other = "compile cxx with cl utf-8 flag"
    scores = bm25_one_way_scores(query, [other])
    assert scores[0] < 0.5


def test_one_way_empty_documents():
    assert bm25_one_way_scores("hello world", []) == []


def test_one_way_untokenizable_query_all_zero():
    # Punctuation-only / whitespace has no BM25 tokens.
    assert bm25_one_way_scores("   !!!   ", ["hello world"]) == [0.0]


def test_best_match_at_threshold():
    query = "PresentBench grades slides.md not a pptx file"
    near = "PresentBench grades slides.md rather than pptx"
    far = "go run works on windows"
    docs = [far, near]
    assert bm25_best_match(query, docs, threshold=0.5) == 1
    # Far-only pool stays below the floor.
    assert bm25_best_match(query, [far], threshold=0.5) is None
    # Identical text is a perfect match.
    assert bm25_best_match(query, [far, query], threshold=0.99) == 1


def test_best_match_empty():
    assert bm25_best_match("hello", [], threshold=0.5) is None


def test_pairwise_symmetric_and_identical():
    a = "grader checks case sensitivity"
    b = "grader case sensitivity checks"
    c = "completely unrelated cxx compile flag"
    pairs = {(i, j): sim for i, j, sim in pairwise_bm25_sims([a, b, c])}
    assert (0, 1) in pairs
    assert (0, 2) in pairs
    assert (1, 2) in pairs
    assert pairs[(0, 1)] >= 0.5
    assert pairs[(0, 2)] < 0.5
    # Same corpus yields deterministic ordering (i < j only).
    assert list(pairwise_bm25_sims([a, b]))[0][0:2] == (0, 1)


def test_pairwise_too_small():
    assert pairwise_bm25_sims([]) == []
    assert pairwise_bm25_sims(["only one"]) == []
