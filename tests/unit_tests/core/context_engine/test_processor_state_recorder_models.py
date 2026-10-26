# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Unit tests for ``ContextProcessorStateRecorder._resolve_model_name``.

Mirrors ``PrefixCompactProcessor._auto_compression_executors`` selection so the
compression state event reports the model that actually ran: the per-call
``model_config``/``model_client_config`` override (e.g. from main-call overflow
recovery) must win over the processor config, and an empty
``auto_compression.model_name`` falls back to the request model actually used.
"""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.core.context_engine.context.processor_state_recorder import (
    ContextProcessorStateRecorder,
)


def _processor(*, auto_model_name="", conversation_model="base-conversation-model"):
    return SimpleNamespace(
        config=SimpleNamespace(
            model=SimpleNamespace(model_name=conversation_model),
            model_client=SimpleNamespace(),
            auto_compression=SimpleNamespace(
                model_name=auto_model_name,
                fallback_model_name="",
            ),
        )
    )


def _resolve(*, processor, model_config=None, model_client_config=None) -> str:
    return ContextProcessorStateRecorder._resolve_model_name(
        processor,
        "manual",
        True,
        model_config=model_config,
        model_client_config=model_client_config,
    )


def test_reports_auto_compression_primary_when_configured():
    processor = _processor(auto_model_name="compact-primary")
    assert _resolve(processor=processor) == "compact-primary"
    # The override must not shadow an explicit auto_compression.model_name.
    override = SimpleNamespace(model_name="override-primary")
    assert _resolve(processor=processor, model_config=override) == "compact-primary"


def test_empty_auto_model_falls_back_to_conversation_model():
    # auto_compression is configured but model_name is empty: report the request
    # model that actually drives the executor (config.model here).
    processor = _processor(auto_model_name="")
    assert _resolve(processor=processor) == "base-conversation-model"


def test_per_call_model_config_override_wins_over_processor_config():
    # Overflow recovery passes a per-call model_config; the recorded name must
    # match the model that actually ran, not the stale processor config.model.
    processor = _processor(auto_model_name="")
    override = SimpleNamespace(model_name="override-primary")
    assert _resolve(processor=processor, model_config=override) == "override-primary"


def test_auto_config_without_credentials_falls_back_to_conversation_model():
    # When auto_compression is configured but no model_client is available, the
    # executor falls back to the main conversation model.
    processor = SimpleNamespace(
        config=SimpleNamespace(
            model=SimpleNamespace(model_name="base-conversation-model"),
            model_client=None,
            auto_compression=SimpleNamespace(model_name="compact-primary"),
        )
    )
    assert _resolve(processor=processor) == "base-conversation-model"


def test_returns_empty_for_missing_processor_config():
    assert _resolve(processor=SimpleNamespace(config=None)) == ""
