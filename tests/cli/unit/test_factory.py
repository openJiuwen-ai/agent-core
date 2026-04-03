"""Unit tests for vision/audio config fallback in factory."""

from __future__ import annotations

import pytest

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.agent.factory import (
    _load_audio_config,
    _load_vision_config,
)


@pytest.fixture()
def cli_cfg() -> CLIConfig:
    """Minimal CLIConfig with a test API key."""
    return CLIConfig(
        api_key="sk-main-key",
        api_base="https://main.example.com/v1",
    )


class TestLoadVisionConfig:
    """Tests for _load_vision_config()."""

    def test_fallback_to_main_model(
        self,
        cli_cfg: CLIConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uses main model key when VISION_API_KEY unset."""
        monkeypatch.delenv(
            "VISION_API_KEY", raising=False
        )
        result = _load_vision_config(cli_cfg)
        assert result is not None
        assert result.api_key == "sk-main-key"
        assert (
            result.base_url
            == "https://main.example.com/v1"
        )

    def test_uses_env_when_set(
        self,
        cli_cfg: CLIConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uses VISION_API_KEY when explicitly set."""
        monkeypatch.setenv(
            "VISION_API_KEY", "sk-vision-key"
        )
        monkeypatch.setenv(
            "VISION_BASE_URL",
            "https://vision.example.com/v1",
        )
        result = _load_vision_config(cli_cfg)
        assert result is not None
        assert result.api_key == "sk-vision-key"
        assert (
            result.base_url
            == "https://vision.example.com/v1"
        )


class TestLoadAudioConfig:
    """Tests for _load_audio_config()."""

    def test_fallback_to_main_model(
        self,
        cli_cfg: CLIConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uses main model key when AUDIO_API_KEY unset."""
        monkeypatch.delenv(
            "AUDIO_API_KEY", raising=False
        )
        result = _load_audio_config(cli_cfg)
        assert result is not None
        assert result.api_key == "sk-main-key"
        assert (
            result.base_url
            == "https://main.example.com/v1"
        )

    def test_uses_env_when_set(
        self,
        cli_cfg: CLIConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uses AUDIO_API_KEY when explicitly set."""
        monkeypatch.setenv(
            "AUDIO_API_KEY", "sk-audio-key"
        )
        monkeypatch.setenv(
            "AUDIO_BASE_URL",
            "https://audio.example.com/v1",
        )
        result = _load_audio_config(cli_cfg)
        assert result is not None
        assert result.api_key == "sk-audio-key"
        assert (
            result.base_url
            == "https://audio.example.com/v1"
        )
