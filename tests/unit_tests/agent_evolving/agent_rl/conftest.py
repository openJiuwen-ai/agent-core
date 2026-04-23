# -*- coding: utf-8 -*-
"""Shared fixtures for agent_rl unit tests."""

import pytest


@pytest.fixture
def mock_tokenizer():
    """Mock tokenizer with apply_chat_template and encode for RolloutEncoder/batch tests."""

    class MockTokenizer:
        pad_token_id = 0

        @staticmethod
        def apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, tools=None
        ):
            if isinstance(messages, list) and messages:
                parts = []
                for m in messages:
                    if isinstance(m, dict):
                        role = m.get("role", "user")
                        content = m.get("content", "") or ""
                        if isinstance(content, list):
                            content = " ".join(
                                c.get("text", str(c)) for c in content if isinstance(c, dict)
                            )
                        parts.append(f"<{role}>{content}")
                    else:
                        parts.append(str(m))
                return " ".join(parts) + (" " if add_generation_prompt else "")
            return ""

        @staticmethod
        def encode(text, add_special_tokens=True):
            if not text:
                return []
            return [ord(c) % 100 for c in text[:50]]

    return MockTokenizer()
