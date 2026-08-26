# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.config."""

import os

from openjiuwen.extensions.app import config


class TestConfig:
    def test_get_returns_default_for_unknown_key(self):
        assert config.get("NOT_A_REAL_KEY") is None
        assert config.get("NOT_A_REAL_KEY", "fallback") == "fallback"

    def test_get_returns_known_defaults(self):
        assert config.get("MODEL_PROVIDER") is not None
        assert config.get("PORT") == int(config.get("PORT"))

    def test_set_value_then_get_round_trips(self):
        config.set_value("API_KEY", "mock-api-key-for-tests")
        try:
            assert config.get("API_KEY") == "mock-api-key-for-tests"
        finally:
            config.set_value("API_KEY", "")

    def test_llm_temperature_and_seed_have_numeric_defaults(self):
        assert isinstance(config.get("LLM_TEMPERATURE"), float)
        assert isinstance(config.get("LLM_SEED"), int)

    def test_llm_reasoning_effort_defaults_to_none(self):
        # Not every provider/model accepts this (e.g. DeepSeek's chat models
        # reject it outright) -- must default to unset, never a guessed value.
        assert config.get("LLM_REASONING_EFFORT") is None

    def test_llm_reasoning_effort_round_trips_when_set(self):
        config.set_value("LLM_REASONING_EFFORT", "low")
        try:
            assert config.get("LLM_REASONING_EFFORT") == "low"
        finally:
            config.set_value("LLM_REASONING_EFFORT", None)

    def test_free_search_ddg_enabled_by_default(self):
        # config.py sets this env var (via os.environ.setdefault) on import so
        # WebFreeSearchTool works out of the box; importing the module already
        # ran that, so just assert the process-wide effect took hold.
        assert os.environ.get("FREE_SEARCH_DDG_ENABLED", "").lower() in {"1", "true", "yes", "on", "enabled"}
