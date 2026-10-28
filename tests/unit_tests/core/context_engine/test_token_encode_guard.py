# coding: utf-8
"""Guard: token counting stays cost-bounded on degenerate input and caches the rest."""

from __future__ import annotations

import time

from openjiuwen.core.context_engine.token.encode_guard import (
    MAX_PIECE_CHARS,
    CountCache,
    count_with_guard,
    estimate_tokens,
    is_pathological,
    longest_piece_length,
)
from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter


class _RecordingEncoding:
    """Stands in for tiktoken's ``Encoding`` and records every encode call."""

    def __init__(self, tokens: int = 1) -> None:
        self.calls: list[str] = []
        self._tokens = tokens

    def encode(self, text, *, disallowed_special=()):  # noqa: ANN001
        self.calls.append(text)
        return list(range(self._tokens))


def _pathological_blob() -> str:
    # Mirrors the fetch_webpage payload of the AgentServer stall report: a long
    # single-character run inside a list-looking envelope.
    inner = "function(){" + "x" * 400_000 + "}"
    return "[{'url': 'https://example.test/vue.js', 'content': '" + inner + "'}]"


def test_long_separator_free_run_is_pathological():
    assert is_pathological("x" * MAX_PIECE_CHARS)
    assert not is_pathological("x" * (MAX_PIECE_CHARS - 1))


def test_separators_bound_the_longest_piece():
    text = "\n".join(f"2026-10-26 INFO request id={index} finished ok" for index in range(5000))

    assert longest_piece_length(text) < 64
    assert not is_pathological(text)


def test_repeated_character_run_is_detected_through_the_envelope():
    assert longest_piece_length(_pathological_blob()) >= 400_000


def test_full_width_punctuation_bounds_cjk_pieces():
    # tiktoken breaks a letter run at any non-letter, so full-width punctuation
    # must bound a piece here too; otherwise a long CJK paragraph looks like one
    # pathological piece and stops being counted exactly.
    paragraph = "这是一个用于测试的长中文段落，" * 1_500

    assert len(paragraph) > MAX_PIECE_CHARS
    assert longest_piece_length(paragraph) < 64
    assert not is_pathological(paragraph)


def test_refused_cjk_piece_is_not_under_counted():
    blob = "中" * (MAX_PIECE_CHARS + 1)

    assert is_pathological(blob)
    assert estimate_tokens(blob) == len(blob)


def test_counter_refuses_to_encode_oversized_piece():
    counter = TiktokenCounter()
    recording = _RecordingEncoding()
    counter._enc = recording
    blob = _pathological_blob()

    started = time.perf_counter()
    tokens = counter.count(blob)
    elapsed = time.perf_counter() - started

    assert recording.calls == []
    assert tokens == estimate_tokens(blob)
    assert elapsed < 2.0, f"pathological count took {elapsed:.3f}s"


def test_counter_caches_repeated_counts():
    counter = TiktokenCounter()
    recording = _RecordingEncoding(tokens=7)
    counter._enc = recording
    text = "an ordinary tool result " * 50

    assert counter.count(text) == 7
    assert counter.count(text) == 7
    assert recording.calls == [text]


def test_counter_encodes_normal_text_exactly():
    counter = TiktokenCounter()
    text = "hello world, this is a normal tool result."

    assert counter.count(text) == len(counter._enc.encode(text, disallowed_special=()))


def test_counter_falls_back_to_estimate_without_encoding():
    counter = TiktokenCounter()
    counter._enc = None
    counter._fallback_warning_printed = True

    assert counter.count("x" * 30) == estimate_tokens("x" * 30)


def test_counter_attributes_degradation_to_the_current_count_only():
    class _RejectingEncoding:
        def encode(self, text, *, disallowed_special=()):  # noqa: ANN001
            if "\ud800" in text:
                raise UnicodeEncodeError("utf-8", text, 0, 1, "surrogates not allowed")
            return list(range(len(text)))

    counter = TiktokenCounter()
    counter._enc = _RejectingEncoding()
    text = f"or{chr(0xD800)}dinary"

    assert counter.count(text) == estimate_tokens(text)
    assert counter.measurement_source == "string_length_fallback"
    assert counter.measurement_fallback_reason == "text_encoding_failed"

    assert counter.count("plain ascii text") == 16
    assert counter.measurement_source == "tiktoken"
    assert counter.measurement_fallback_reason is None

    # The estimate is not cached, so counting the text again reports the fallback
    # again instead of replaying a stale exact attribution.
    assert counter.count(text) == estimate_tokens(text)
    assert counter.measurement_source == "string_length_fallback"


def test_count_with_guard_caches_and_skips_pathological_encodes():
    cache = CountCache()
    encoded: list[str] = []

    def encode(text: str) -> int:
        encoded.append(text)
        return 5

    blob = _pathological_blob()

    assert count_with_guard(blob, cache=cache, encode=encode) == estimate_tokens(blob)
    assert count_with_guard(blob, cache=cache, encode=encode) == estimate_tokens(blob)
    assert count_with_guard("ordinary text", cache=cache, encode=encode) == 5
    assert count_with_guard("ordinary text", cache=cache, encode=encode) == 5
    assert encoded == ["ordinary text"]


def test_upstream_merge_stays_cheap_on_oversized_pieces():
    # Canary for the stall this module defends against: on tiktoken <=0.12 a
    # single oversized pre-token piece merged quadratically (64K chars ~7s, and
    # a 400K blob never returned); 0.14 fixed it (~0.010s). Encoding is called
    # directly here because the guard would otherwise short-circuit to the
    # estimate and hide a regression.
    encoding = TiktokenCounter()._enc
    normal = "2026-10-26 INFO request id=1 finished ok\n" * 2_000
    oversized = "x" * 65_536

    started = time.perf_counter()
    encoding.encode(normal, disallowed_special=())
    normal_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    encoding.encode(oversized, disallowed_special=())
    oversized_elapsed = time.perf_counter() - started

    assert oversized_elapsed < max(normal_elapsed, 0.001) * 50, (
        f"oversized piece took {oversized_elapsed:.3f}s vs {normal_elapsed:.3f}s for comparable "
        "normal text; upstream may have regressed to a superlinear merge"
    )


def test_estimate_tokens_never_under_counts_non_ascii_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("x" * 30) == 10
    # One token per non-ASCII code point: len//3 would under-count CJK ~3x and
    # let an oversized payload slip past the offload/compaction thresholds.
    assert estimate_tokens("中" * 30) == 30
    assert estimate_tokens("abc中" * 3) == 6
    assert estimate_tokens("中文abc") == 3


def test_count_cache_evicts_by_entries():
    cache = CountCache(max_entries=2, max_chars=1_000)
    cache.put("a" * 10, 1)
    cache.put("b" * 10, 2)
    cache.put("c" * 10, 3)

    assert cache.get("a" * 10) is None
    assert cache.get("c" * 10) == 3


def test_count_cache_evicts_by_retained_characters():
    cache = CountCache(max_entries=10, max_chars=25)
    cache.put("a" * 10, 1)
    cache.put("b" * 10, 2)
    cache.put("c" * 10, 3)

    assert cache.get("a" * 10) is None
    assert cache.get("b" * 10) == 2
    assert cache.get("c" * 10) == 3


def test_count_cache_skips_texts_larger_than_its_budget():
    cache = CountCache(max_entries=10, max_chars=100)

    cache.put("x" * 101, 1)

    assert cache.get("x" * 101) is None
