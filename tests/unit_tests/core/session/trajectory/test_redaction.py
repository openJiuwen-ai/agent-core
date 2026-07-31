# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Secret redaction must strip credential-shaped substrings, keep the rest."""
from openjiuwen.core.session.trajectory.redaction import REDACTED, redact_text


def test_none_and_empty_pass_through():
    assert redact_text(None) is None
    assert redact_text("") == ""


def test_plain_text_is_untouched():
    text = "please check the weather in shanghai and summarize"
    assert redact_text(text) == text


def test_json_key_value_is_redacted_in_place():
    redacted = redact_text('{"url": "https://x", "api_key": "abc123secret", "city": "sh"}')
    assert "abc123secret" not in redacted
    # Surrounding JSON structure must survive for the file to stay useful.
    assert '"url": "https://x"' in redacted
    assert '"city": "sh"' in redacted
    assert REDACTED in redacted


def test_env_style_assignment_is_redacted():
    redacted = redact_text("run with PASSWORD=hunter2 and DEBUG=1")
    assert "hunter2" not in redacted
    assert "DEBUG=1" in redacted


def test_bearer_header_is_redacted():
    redacted = redact_text("Authorization: Bearer eyJhbGciOi.payload.sig")
    assert "eyJhbGciOi" not in redacted
    assert REDACTED in redacted


def test_known_token_prefixes_are_redacted():
    for token in ("sk-abc123def456ghi789jkl", "ghp_abcdefghijklmnop1234", "AKIAABCDEFGHIJKLMNOP"):
        redacted = redact_text(f"found {token} in output")
        assert token not in redacted, token
        assert REDACTED in redacted
