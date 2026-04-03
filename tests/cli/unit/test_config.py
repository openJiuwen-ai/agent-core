"""Unit tests for openjiuwen.harness.cli.agent.config."""

from __future__ import annotations

import json

import pytest

from openjiuwen.harness.cli.agent.config import (
    CLIConfig,
    load_config,
    load_settings_json,
    save_settings_json,
)


class TestCLIConfig:
    """Tests for CLIConfig defaults and validation."""

    def test_default_values(self) -> None:
        """Default configuration values are correct."""
        cfg = CLIConfig()
        assert cfg.provider == "OpenAI"
        assert cfg.model == "gpt-4o"
        assert cfg.max_tokens == 8192
        assert cfg.max_iterations == 30
        assert cfg.api_base == "https://api.openai.com/v1"
        assert cfg.server_url == ""

    def test_validate_no_api_key(self) -> None:
        """Missing API key raises ValueError in local mode."""
        cfg = CLIConfig(api_key="")
        with pytest.raises(ValueError, match="API key"):
            cfg.validate()

    def test_validate_small_max_tokens(self) -> None:
        """max_tokens < 256 raises ValueError."""
        cfg = CLIConfig(api_key="test", max_tokens=32)
        with pytest.raises(
            ValueError, match="dangerously small"
        ):
            cfg.validate()

    def test_validate_max_tokens_boundary(self) -> None:
        """max_tokens=256 is the minimum valid value."""
        cfg = CLIConfig(api_key="test", max_tokens=256)
        cfg.validate()  # should not raise

    def test_validate_bad_max_iterations(self) -> None:
        """max_iterations < 1 raises ValueError."""
        cfg = CLIConfig(api_key="test", max_iterations=0)
        with pytest.raises(ValueError, match="max_iterations"):
            cfg.validate()

    def test_validate_with_server_url(self) -> None:
        """api_key is not required when server_url is set."""
        cfg = CLIConfig(
            api_key="",
            server_url="http://localhost:8080",
        )
        cfg.validate()  # should not raise

    def test_validate_success(self) -> None:
        """Valid config passes validation."""
        cfg = CLIConfig(api_key="sk-test-key")
        cfg.validate()  # should not raise

    def test_validate_error_mentions_settings_json(
        self,
    ) -> None:
        """Validation error mentions settings.json."""
        cfg = CLIConfig(api_key="")
        with pytest.raises(
            ValueError, match="settings.json"
        ):
            cfg.validate()


# -------------------------------------------------------------------
# load_settings_json / save_settings_json
# -------------------------------------------------------------------


class TestLoadSettingsJson:
    """Tests for load_settings_json()."""

    def test_missing_file(self, tmp_path):
        """Returns {} when the file does not exist."""
        assert (
            load_settings_json(tmp_path / "nope.json")
            == {}
        )

    def test_valid_file(self, tmp_path):
        """Parses a well-formed JSON object."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"apiKey": "sk-test", "model": "gpt-4o"}'
        )
        result = load_settings_json(p)
        assert result == {
            "apiKey": "sk-test",
            "model": "gpt-4o",
        }

    def test_malformed_json(self, tmp_path):
        """Returns {} on malformed JSON."""
        p = tmp_path / "settings.json"
        p.write_text("{invalid json")
        assert load_settings_json(p) == {}

    def test_non_dict_json(self, tmp_path):
        """Returns {} when JSON root is not an object."""
        p = tmp_path / "settings.json"
        p.write_text('["a", "b"]')
        assert load_settings_json(p) == {}

    def test_empty_file(self, tmp_path):
        """Returns {} on an empty file."""
        p = tmp_path / "settings.json"
        p.write_text("")
        assert load_settings_json(p) == {}


class TestSaveSettingsJson:
    """Tests for save_settings_json()."""

    def test_creates_new_file(self, tmp_path):
        """Creates file and parent dirs if needed."""
        p = tmp_path / "sub" / "settings.json"
        save_settings_json({"apiKey": "sk-test"}, p)
        data = json.loads(p.read_text())
        assert data["apiKey"] == "sk-test"

    def test_merges_existing(self, tmp_path):
        """Merges into existing file, overriding keys."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"model": "gpt-4o", "apiKey": "old"}'
        )
        save_settings_json({"apiKey": "new"}, p)
        data = json.loads(p.read_text())
        assert data["apiKey"] == "new"
        assert data["model"] == "gpt-4o"

    def test_returns_path(self, tmp_path):
        """Returns the resolved file path."""
        p = tmp_path / "settings.json"
        result = save_settings_json({"a": 1}, p)
        assert result == p


# -------------------------------------------------------------------
# load_config
# -------------------------------------------------------------------


class TestLoadConfig:
    """Tests for load_config() three-layer merge."""

    def test_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variables override defaults."""
        monkeypatch.setenv(
            "OPENJIUWEN_MODEL", "qwen-max"
        )
        monkeypatch.setenv(
            "OPENJIUWEN_PROVIDER", "DashScope"
        )
        monkeypatch.setenv(
            "OPENJIUWEN_API_KEY", "test-key"
        )
        cfg = load_config()
        assert cfg.model == "qwen-max"
        assert cfg.provider == "DashScope"

    def test_cli_args_override_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI arguments override environment variables."""
        monkeypatch.setenv(
            "OPENJIUWEN_MODEL", "qwen-max"
        )
        monkeypatch.setenv(
            "OPENJIUWEN_API_KEY", "test-key"
        )
        cfg = load_config(model="gpt-4o-mini")
        assert cfg.model == "gpt-4o-mini"

    def test_load_config_validates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() calls validate() automatically."""
        monkeypatch.delenv(
            "OPENJIUWEN_API_KEY", raising=False
        )
        with pytest.raises(ValueError, match="API key"):
            load_config()

    def test_settings_json_overrides_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """settings.json values override defaults."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"apiKey": "from-json", "model": "qwen"}'
        )
        monkeypatch.setattr(
            "openjiuwen.harness.cli.agent.config"
            ".SETTINGS_PATH",
            p,
        )
        monkeypatch.delenv(
            "OPENJIUWEN_API_KEY", raising=False
        )
        monkeypatch.delenv(
            "OPENJIUWEN_MODEL", raising=False
        )
        cfg = load_config()
        assert cfg.api_key == "from-json"
        assert cfg.model == "qwen"

    def test_env_overrides_settings_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """Env vars override settings.json values."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"apiKey": "from-json", "model": "from-json"}'
        )
        monkeypatch.setattr(
            "openjiuwen.harness.cli.agent.config"
            ".SETTINGS_PATH",
            p,
        )
        monkeypatch.setenv(
            "OPENJIUWEN_API_KEY", "from-env"
        )
        monkeypatch.setenv(
            "OPENJIUWEN_MODEL", "from-env"
        )
        cfg = load_config()
        assert cfg.api_key == "from-env"
        assert cfg.model == "from-env"

    def test_cli_overrides_settings_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """CLI args override settings.json values."""
        p = tmp_path / "settings.json"
        p.write_text('{"apiKey": "from-json"}')
        monkeypatch.setattr(
            "openjiuwen.harness.cli.agent.config"
            ".SETTINGS_PATH",
            p,
        )
        monkeypatch.delenv(
            "OPENJIUWEN_API_KEY", raising=False
        )
        cfg = load_config(api_key="from-cli")
        assert cfg.api_key == "from-cli"

    def test_settings_json_max_tokens(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """settings.json can set maxTokens."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"apiKey": "k", "maxTokens": 4096}'
        )
        monkeypatch.setattr(
            "openjiuwen.harness.cli.agent.config"
            ".SETTINGS_PATH",
            p,
        )
        monkeypatch.delenv(
            "OPENJIUWEN_API_KEY", raising=False
        )
        monkeypatch.delenv(
            "OPENJIUWEN_MAX_TOKENS", raising=False
        )
        cfg = load_config()
        assert cfg.max_tokens == 4096

    def test_settings_json_max_iterations(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """settings.json can set maxIterations."""
        p = tmp_path / "settings.json"
        p.write_text(
            '{"apiKey": "k", "maxIterations": 15}'
        )
        monkeypatch.setattr(
            "openjiuwen.harness.cli.agent.config"
            ".SETTINGS_PATH",
            p,
        )
        monkeypatch.delenv(
            "OPENJIUWEN_API_KEY", raising=False
        )
        monkeypatch.delenv(
            "OPENJIUWEN_MAX_ITERATIONS", raising=False
        )
        cfg = load_config()
        assert cfg.max_iterations == 15
