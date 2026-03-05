# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test bootstrap for deepagents unit tests.

The repository may run without optional third-party SDKs such as
``dashscope`` in local CI environments. Provide a lightweight stub
so deepagent module imports remain testable.
"""
from __future__ import annotations

import sys
import types


def _install_dashscope_stub() -> None:
    if "dashscope" in sys.modules:
        return

    module = types.ModuleType("dashscope")

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

    module.MultiModalConversation = _DummyApi
    module.VideoSynthesis = _DummyApi
    module.base_http_api_url = ""
    sys.modules["dashscope"] = module


_install_dashscope_stub()

