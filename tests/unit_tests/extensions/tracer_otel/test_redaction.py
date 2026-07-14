# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tracer_otel redaction utilities."""

import hashlib

import pytest

from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.redaction import truncate, hash_value, redact, _should_redact


class TestTruncate:
    def test_short_value_not_truncated(self):
        assert truncate("abc", 10) == "abc"

    def test_long_value_truncated(self):
        result = truncate("abcdefghij", 5)
        assert result == "abcde...<truncated>"

    def test_exact_length_not_truncated(self):
        assert truncate("abcde", 5) == "abcde"

    def test_zero_max_length_truncates(self):
        # max_length <= 0 → always return original
        assert truncate("abc", 0) == "abc"

    def test_negative_max_length_passes_through(self):
        assert truncate("abc", -1) == "abc"


class TestHashValue:
    def test_hash_returns_sha256_prefix(self):
        result = hash_value("hello")
        digest = hashlib.sha256("hello".encode("utf-8")).hexdigest()
        assert result == f"sha256:{digest[:16]}"

    def test_hash_empty_string(self):
        result = hash_value("")
        digest = hashlib.sha256(b"").hexdigest()
        assert result == f"sha256:{digest[:16]}"


class TestRedact:
    def test_redaction_enabled_returns_hash(self):
        config = OtelTracerConfig(redaction_enabled=True)
        result = redact("hello world", config)
        assert result.startswith("sha256:")

    def test_redaction_disabled_returns_truncated(self):
        config = OtelTracerConfig(redaction_enabled=False, max_attr_length=10)
        result = redact("hello world longer text", config)
        assert result == "hello worl...<truncated>"

    def test_redact_none_returns_empty(self):
        config = OtelTracerConfig(redaction_enabled=True)
        assert redact(None, config) == "sha256:" + hashlib.sha256(b"").hexdigest()[:16]

    def test_redact_non_string_converts(self):
        config = OtelTracerConfig(redaction_enabled=True)
        result = redact(123, config)
        assert result.startswith("sha256:")

    def test_redact_short_value_no_hash_truncation_disabled(self):
        config = OtelTracerConfig(redaction_enabled=False, max_attr_length=100)
        assert redact("short", config) == "short"


class TestShouldRedact:
    """Test _should_redact resolution with redact_prompts / redact_completions overrides."""

    def test_field_none_uses_redaction_enabled_true(self):
        config = OtelTracerConfig(redaction_enabled=True)
        assert _should_redact(config, None) is True

    def test_field_none_uses_redaction_enabled_false(self):
        config = OtelTracerConfig(redaction_enabled=False)
        assert _should_redact(config, None) is False

    def test_field_prompts_override_true(self):
        """redact_prompts=True overrides redaction_enabled=False."""
        config = OtelTracerConfig(redaction_enabled=False, redact_prompts=True)
        assert _should_redact(config, "prompts") is True

    def test_field_prompts_override_false(self):
        """redact_prompts=False overrides redaction_enabled=True."""
        config = OtelTracerConfig(redaction_enabled=True, redact_prompts=False)
        assert _should_redact(config, "prompts") is False

    def test_field_prompts_none_fallback(self):
        """redact_prompts=None → falls back to redaction_enabled."""
        config = OtelTracerConfig(redaction_enabled=True, redact_prompts=None)
        assert _should_redact(config, "prompts") is True

    def test_field_completions_override_true(self):
        config = OtelTracerConfig(redaction_enabled=False, redact_completions=True)
        assert _should_redact(config, "completions") is True

    def test_field_completions_override_false(self):
        config = OtelTracerConfig(redaction_enabled=True, redact_completions=False)
        assert _should_redact(config, "completions") is False

    def test_field_completions_none_fallback(self):
        config = OtelTracerConfig(redaction_enabled=False, redact_completions=None)
        assert _should_redact(config, "completions") is False

    def test_different_fields_independent(self):
        """redact_prompts and redact_completions can differ."""
        config = OtelTracerConfig(redaction_enabled=True, redact_prompts=False, redact_completions=True)
        assert _should_redact(config, "prompts") is False
        assert _should_redact(config, "completions") is True


class TestRedactWithField:
    """Test redact() with field= parameter for prompt/completion split."""

    def test_redact_prompt_with_override(self):
        """field="prompts" with redact_prompts=False → truncate, not hash."""
        config = OtelTracerConfig(redaction_enabled=True, redact_prompts=False, max_attr_length=100)
        result = redact("secret prompt", config, field="prompts")
        assert not result.startswith("sha256:")
        assert result == "secret prompt"

    def test_redact_completion_with_override(self):
        """field="completions" with redact_completions=False → truncate, not hash."""
        config = OtelTracerConfig(redaction_enabled=True, redact_completions=False, max_attr_length=100)
        result = redact("secret response", config, field="completions")
        assert not result.startswith("sha256:")
        assert result == "secret response"

    def test_redact_prompt_override_hash(self):
        """field="prompts" with redact_prompts=True overrides redaction_enabled=False."""
        config = OtelTracerConfig(redaction_enabled=False, redact_prompts=True)
        result = redact("test", config, field="prompts")
        assert result.startswith("sha256:")

    def test_redact_field_none_uses_legacy(self):
        """field=None uses legacy redaction_enabled."""
        config = OtelTracerConfig(redaction_enabled=False, redact_prompts=True, max_attr_length=100)
        result = redact("data", config, field=None)
        assert not result.startswith("sha256:")
        assert result == "data"