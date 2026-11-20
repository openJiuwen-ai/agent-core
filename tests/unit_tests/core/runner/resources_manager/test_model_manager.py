# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""ModelMgr.get_model 与 ResourceMgr.get_model 的 identity 透传契约。

嵌入侧（如 studio runtime 的双写桥）拿到的会话是裸会话、无 agent card，
控制器层的 agent 身份只能经 ``identity=`` 显式带给 llm span。本文件钉死：
identity 原样透传给 decorate_model_with_trace；**identity 不是开关**——
缺省 / 空一律照常装饰（与基线一致，匿名 span），带 identity 只是多写
署名属性。core 内部存量调用方改由各自调用点自带模块身份（无名变有名）。

嵌入侧拿模型的公共入口是 ``Runner.resource_mgr.get_model``（ResourceMgr），
不是 ModelMgr——入口签名不收 identity 时调用直接 TypeError（比遥测丢失
严重：模型创建失败）。ResourceMgr 层用例钉死三环透传
（get_model → _inner_get_resources_by_provider → _dispatch_get）。
"""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.runner.resources_manager.model_manager import ModelMgr
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr

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

    async def test_default_call_still_decorates(self, monkeypatch):
        """缺省调用（core 全部存量调用方）照常装饰：与基线一致返回
        包装模型，identity 以 None 透传（wire 形态不变，匿名 span）。"""
        mgr = ModelMgr()
        bare = MagicMock(name="bare-model")

        async def fake_get_resource(model_id):
            return bare

        monkeypatch.setattr(mgr, "_get_resource", fake_get_resource)

        calls = []

        def fake_decorate(model, session, identity=None):
            calls.append(identity)
            return "decorated"

        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.model_manager.decorate_model_with_trace",
            fake_decorate,
        )

        out = await mgr.get_model("m-1", MagicMock())
        assert out == "decorated"
        assert calls == [None]

    async def test_empty_identity_still_decorates(self, monkeypatch):
        """空 identity 视同缺省：照常装饰（嵌入侧探测失败 / 归因信息缺失时
        只会传空，行为退回匿名 span 而非不产 span）。"""
        mgr = ModelMgr()
        bare = MagicMock(name="bare-model")

        async def fake_get_resource(model_id):
            return bare

        monkeypatch.setattr(mgr, "_get_resource", fake_get_resource)

        calls = []

        def fake_decorate(model, session, identity=None):
            calls.append(identity)
            return "decorated"

        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.model_manager.decorate_model_with_trace",
            fake_decorate,
        )

        out = await mgr.get_model("m-1", MagicMock(), identity={})
        assert out == "decorated"
        assert calls == [{}]


class TestResourceMgrIdentity:
    async def test_get_model_entry_forwards_identity(self, monkeypatch):
        """公共入口全链透传：get_model → _inner_get_resources_by_provider
        → _dispatch_get → ModelMgr.get_model，identity 原样到达叶子。"""
        mgr = ResourceMgr()
        captured = {}

        class _FakeModelMgr:
            async def get_model(self, model_id, session=None, identity=None):
                captured["model_id"] = model_id
                captured["session"] = session
                captured["identity"] = identity
                return "decorated-model"

        monkeypatch.setattr(mgr, "_get_mgr", lambda resource_type: _FakeModelMgr())
        monkeypatch.setattr(
            mgr,
            "_inner_find_resource_ids",
            lambda **kwargs: (["m-1"], True),
        )
        tag_mgr = MagicMock()
        tag_mgr.has_resource.return_value = True
        monkeypatch.setattr(mgr, "_tag_mgr", tag_mgr)

        session = MagicMock()
        out = await mgr.get_model(model_id="m-1", session=session, identity={"agent_id": "agent-9"})
        assert out == "decorated-model"
        assert captured["model_id"] == "m-1"
        assert captured["session"] is session
        assert captured["identity"] == {"agent_id": "agent-9"}

        # 缺省（全部存量调用方）：叶子不收到 identity 实参，wire 形态不变。
        await mgr.get_model(model_id="m-1", session=session)
        assert captured["identity"] is None

    def test_dispatch_get_keeps_identity_model_only(self, monkeypatch):
        """identity 只转发给 model 类型：其余资源类型的 get 不收到该参数，
        共享分发器的通用契约不被单类型参数污染。"""
        mgr = ResourceMgr()
        captured = {}

        class _FakeAgentMgr:
            def get_agent(self, resource_id, **kwargs):
                captured["resource_id"] = resource_id
                captured["kwargs"] = dict(kwargs)
                return "agent"

        monkeypatch.setattr(mgr, "_get_mgr", lambda resource_type: _FakeAgentMgr())
        mgr._dispatch_get("agent", "a-1", session="sess", identity={"agent_id": "x"})
        assert captured["resource_id"] == "a-1"
        assert "identity" not in captured["kwargs"]
