# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test bootstrap for harness unit tests.

The repository may run without optional third-party SDKs such as
``dashscope`` in local CI environments. Provide a lightweight stub
so deepagent module imports remain testable.
"""

from __future__ import annotations

import sys
import types

import pytest


def _install_dashscope_stub() -> None:
    if "dashscope" in sys.modules:
        return

    module = types.ModuleType("dashscope")
    module.__path__ = []

    class _DummyApi:
        @staticmethod
        def call(*args, **kwargs):  # noqa: ANN001, ANN002
            class _Resp:
                status_code = 200
                output = {}
                code = ""
                message = ""

            return _Resp()

        @staticmethod
        def wait(*args, **kwargs):  # noqa: ANN001, ANN002
            class _Resp:
                status_code = 200
                output = {"video_url": ""}
                code = ""
                message = ""

            return _Resp()

    class _DashScopeAPIResponse:
        status_code = 200
        output = {}
        code = ""
        message = ""

    module.MultiModalConversation = _DummyApi
    module.VideoSynthesis = _DummyApi
    module.AioMultiModalEmbedding = _DummyApi
    module.MultiModalEmbedding = _DummyApi
    module.base_http_api_url = ""

    api_entities = types.ModuleType("dashscope.api_entities")
    api_entities.__path__ = []
    dashscope_response = types.ModuleType("dashscope.api_entities.dashscope_response")
    dashscope_response.DashScopeAPIResponse = _DashScopeAPIResponse

    common = types.ModuleType("dashscope.common")
    common.__path__ = []
    constants = types.ModuleType("dashscope.common.constants")
    constants.REQUEST_TIMEOUT_KEYWORD = "request_timeout"

    sys.modules["dashscope"] = module
    sys.modules["dashscope.api_entities"] = api_entities
    sys.modules["dashscope.api_entities.dashscope_response"] = dashscope_response
    sys.modules["dashscope.common"] = common
    sys.modules["dashscope.common.constants"] = constants

    # dashscope.api_entities.dashscope_response
    api_entities = types.ModuleType("dashscope.api_entities")
    dashscope_response = types.ModuleType("dashscope.api_entities.dashscope_response")
    dashscope_response.DashScopeAPIResponse = object
    api_entities.dashscope_response = dashscope_response
    module.api_entities = api_entities
    sys.modules["dashscope.api_entities"] = api_entities
    sys.modules["dashscope.api_entities.dashscope_response"] = dashscope_response

    # dashscope.common.constants
    common = types.ModuleType("dashscope.common")
    constants = types.ModuleType("dashscope.common.constants")
    constants.REQUEST_TIMEOUT_KEYWORD = "request_timeout"
    common.constants = constants
    module.common = common
    sys.modules["dashscope.common"] = common
    sys.modules["dashscope.common.constants"] = constants


_install_dashscope_stub()


@pytest.fixture(autouse=True)
def _mock_image_modality_probe(monkeypatch):
    """Prevent auto image-modality probe from consuming mock LLM responses."""
    from unittest.mock import MagicMock

    from openjiuwen.harness.image_modality_probe import reset_image_support_cache

    reset_image_support_cache()
    schedule = MagicMock()
    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", schedule)
    yield schedule
    reset_image_support_cache()


@pytest.fixture(autouse=True)
def _restore_processor_registry():
    """Undo global processor-registry overrides after each test.

    ``ContextProcessorRail`` activates the refactored ("forked") processors,
    which overwrite ``ContextEngine._PROCESSOR_MAP`` process-wide. Snapshot
    and restore the map so the override never leaks into other tests.
    """
    from openjiuwen.core.context_engine import ContextEngine

    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)
