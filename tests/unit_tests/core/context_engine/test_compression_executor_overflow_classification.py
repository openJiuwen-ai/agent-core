# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Unit tests for the context-overflow classification.

There are two deliberately different classifiers with the same overflow
semantics but different precision:

- ``classify_compression_error`` is the *compression chain* classifier. It is
  intentionally lenient: it keeps broad/bare markers such as a standalone "413"
  (upstream gateways often wrap a payload-too-large error as
  ``RuntimeError("HTTP 413: request entity too large")`` without exposing a
  status attribute) and TPM-overlapping wording ("token limit"/"too many
  tokens"). A false positive here only costs one extra compression retry; it
  never rewrites session history.

- ``is_strict_context_overflow`` is the *main-call recovery* predicate. It is
  deliberately conservative because a false positive there force-compacts and
  irreversibly replaces real session messages with a summary before retrying:
  only an exact 413 status code or an unambiguous length marker qualifies. A
  bare "413" substring (request id, elapsed time, numeric field) or TPM-style
  wording must not be treated as an overflow.
"""

from __future__ import annotations

from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    classify_compression_error,
    is_strict_context_overflow,
)


class _StatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _is_overflow(error: Exception) -> bool:
    """Compression-chain (lenient) classification."""
    return classify_compression_error(error).is_context_overflow


def _is_strict_overflow(error: Exception) -> bool:
    """Main-call (strict) classification."""
    return is_strict_context_overflow(error)


# --------------------------------------------------------------------------- #
# Compression chain (lenient) classifier
# --------------------------------------------------------------------------- #

def test_returns_true_for_exact_413_status_code():
    assert _is_overflow(_StatusError("too large", 413)) is True


def test_returns_true_for_explicit_length_markers():
    for text in (
        "context length exceeded",
        "context_length exceeded",
        "maximum context reached",
        "max context tokens exceeded",
        "token limit reached",
        "too many tokens",
        "prompt is too long",
        "input is too long",
        "request too large",
    ):
        assert _is_overflow(RuntimeError(text)) is True, text


def test_returns_true_for_text_only_413_without_status_attribute():
    # Regression: removing the "413" marker would weaken the compression chain.
    # A gateway wraps a payload-too-large error as a plain RuntimeError that
    # carries neither a status attribute nor the full "request too large"
    # marker; the chain must still classify it as an overflow so it shrinks the
    # span and retries instead of giving up.
    assert _is_overflow(RuntimeError("HTTP 413: request entity too large")) is True


def test_returns_false_for_non_overflow_errors():
    for text in (
        "500 internal server error",
        "429 too many requests",
        "401 unauthorized",
    ):
        assert _is_overflow(RuntimeError(text)) is False, text


def test_status_code_413_wins_over_confusing_text():
    # An explicit 413 status code is authoritative even if the message lacks a
    # length marker.
    assert _is_overflow(_StatusError("Request failed", 413)) is True


def test_bare_413_status_code_not_confused_with_other_error_classes():
    assert _is_overflow(_StatusError("ratelimited", 429)) is False
    assert _is_overflow(_StatusError("bad request", 400)) is False


# --------------------------------------------------------------------------- #
# Main-call (strict) classifier
# --------------------------------------------------------------------------- #

def test_strict_returns_true_for_exact_413_status_code():
    assert _is_strict_overflow(_StatusError("too large", 413)) is True
    assert _is_strict_overflow(_StatusError("Request failed", 413)) is True


def test_strict_returns_true_for_unambiguous_length_markers():
    for text in (
        "context length exceeded",
        "context_length exceeded",
        "maximum context reached",
        "max context tokens exceeded",
        "prompt is too long",
        "input is too long",
        "request too large",
    ):
        assert _is_strict_overflow(RuntimeError(text)) is True, text


def test_strict_returns_false_for_bare_413_substring_in_unrelated_text():
    # Regression: these messages contain "413" but are NOT context overflows.
    # The lenient compression-chain classifier may flag them (an extra retry);
    # the strict main-call predicate must NOT, or it would destructively
    # force-compact and rewrite session history on an unrelated error.
    for text in (
        "request abc413 timed out after 120ms",
        "gateway aborted on request 4130",
        "max_tokens=413 exceeded budget",
        "call took 413ms and hit server error",
    ):
        assert _is_strict_overflow(RuntimeError(text)) is False, text


def test_strict_returns_false_for_tpm_overlap_wording():
    # "token limit"/"too many tokens" overlap with TPM rate-limit errors.
    # Treating them as an overflow on the destructive main-call path would both
    # fail to fix the real (rate) limit and irreversibly rewrite history.
    for text in (
        "token limit reached",
        "too many tokens per minute",
        "tokens per minute (TPM) limit exceeded",
    ):
        assert _is_strict_overflow(RuntimeError(text)) is False, text


def test_strict_returns_false_for_non_overflow_errors():
    for text in (
        "500 internal server error",
        "429 too many requests",
        "401 unauthorized",
    ):
        assert _is_strict_overflow(RuntimeError(text)) is False, text
