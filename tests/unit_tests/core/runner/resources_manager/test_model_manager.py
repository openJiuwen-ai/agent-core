# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""ModelMgr.get_model 的 identity 透传契约。

嵌入侧（如 studio runtime 的双写桥）拿到的会话是裸会话、无 agent card，
控制器层的 agent 身份只能经 ``identity=`` 显式带给 llm span。本文件钉死：
identity 原样透传给 decorate_model_with_trace；缺省（全部存量调用方）保持
``identity=None``，线上 wire 形态不变。
"""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.runner.resources_manager.model_manager import ModelMgr

pytestmark = pytest.mark.asyncio


class TestModelMgr:
    async def test_get_model_forwards_identity_to_decorator(self, monkeypatch):
        mgr = ModelMgr()
        bare = MagicMock(name="bare-model")

        async def fake_get_resource(model_id):
            return bare

        monkeypatch.setattr(mgr, "_get_resource", fake_get_resource)

        captured = {}

        def fake_decorate(model, session, identity=None):
            captured["model"] = model
            captured["session"] = session
            captured["identity"] = identity
            return "decorated"

        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.model_manager.decorate_model_with_trace",
            fake_decorate,
        )

        session = MagicMock()
        out = await mgr.get_model("m-1", session, identity={"agent_id": "agent-9"})
        assert out == "decorated"
        assert captured["model"] is bare
        assert captured["session"] is session
        assert captured["identity"] == {"agent_id": "agent-9"}

        # 缺省调用（存量调用方）：identity=None，请求形态与引入参数前一致。
        await mgr.get_model("m-1", session)
        assert captured["identity"] is None
