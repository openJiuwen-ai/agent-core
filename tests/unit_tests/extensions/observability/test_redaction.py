# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for observability attribute redaction utilities."""

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.redaction import (
    redact_completion,
    redact_system_prompt,
    truncate,
)


def test_truncate_identifies_otel_attribute_layer():
    assert truncate("abcdefghij", 5) == ("abcde...<OTel attribute truncated: 5 chars omitted>")


def test_completion_cap_does_not_look_like_tool_output_truncation():
    config = ObservabilityConfig(
        redact_completions=False,
        attribute_value_max_length=7,
    )

    assert redact_completion("tool-output", config) == ("tool-ou...<OTel attribute truncated: 4 chars omitted>")


def test_short_tool_owned_marker_passes_through_unchanged():
    value = "preview...[tool output truncated: 8 chars omitted]"

    assert truncate(value, len(value)) == value


def test_system_prompt_bypasses_attribute_length_cap():
    config = ObservabilityConfig(
        redact_prompts=False,
        attribute_value_max_length=5,
    )
    value = "complete-system-prompt"

    assert redact_system_prompt(value, config) == value


def test_system_prompt_still_respects_explicit_redaction():
    config = ObservabilityConfig(
        redact_prompts=True,
        attribute_value_max_length=5,
    )

    assert redact_system_prompt("secret-system-prompt", config).startswith("sha256:")
