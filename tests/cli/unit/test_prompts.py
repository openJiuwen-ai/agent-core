"""Unit tests for openjiuwen.harness.cli.prompts.builder."""

from __future__ import annotations

from openjiuwen.harness.cli.prompts.builder import build_system_prompt


class TestBuildSystemPrompt:
    """Tests for system prompt assembly."""

    def test_contains_environment_section(self) -> None:
        """System prompt contains the environment section."""
        prompt = build_system_prompt(
            cwd="/tmp/test",
            model="gpt-4o",
            provider="OpenAI",
        )
        assert "Environment" in prompt

    def test_contains_cwd(self) -> None:
        """Dynamic section includes the working directory."""
        prompt = build_system_prompt(
            cwd="/my/project/dir",
            model="gpt-4o",
            provider="OpenAI",
        )
        assert "/my/project/dir" in prompt

    def test_contains_model_name(self) -> None:
        """Dynamic section includes the model name."""
        prompt = build_system_prompt(
            cwd="/tmp/test",
            model="qwen-max",
            provider="DashScope",
        )
        assert "qwen-max" in prompt
        assert "DashScope" in prompt

    def test_contains_platform_info(self) -> None:
        """Dynamic section includes platform info."""
        prompt = build_system_prompt(
            cwd="/tmp/test",
            model="gpt-4o",
            provider="OpenAI",
        )
        assert "Platform" in prompt
        assert "Python" in prompt

    def test_contains_date(self) -> None:
        """Dynamic section includes the current date."""
        prompt = build_system_prompt(
            cwd="/tmp/test",
            model="gpt-4o",
            provider="OpenAI",
        )
        assert "Date" in prompt
