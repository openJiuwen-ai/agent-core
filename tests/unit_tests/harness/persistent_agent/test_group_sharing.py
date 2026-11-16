# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group sharing contracts and genuine native pause/resume coverage."""

from importlib import import_module

import pytest

pytestmark = pytest.mark.level0


def test_public_group_share_signal():
    api = import_module("openjiuwen.harness.persistent_agent")
    ports = import_module("openjiuwen.harness.persistent_agent.ports")
    assert api.GroupMessageApprovalRequired is ports.GroupMessageApprovalRequired
    signal = api.GroupMessageApprovalRequired(approval_id="approval-1")
    assert signal.approval_id == "approval-1"
    assert str(signal) == "跨群消息分享需要所有者批准。"
    assert not hasattr(signal, "candidates")


def test_intercepted_approval_ref_is_optional_and_serializable():
    api = import_module("openjiuwen.harness.persistent_agent")
    fields = dict(tool_call_id="call-1", tool_name="search_group_messages", arguments="{}")
    assert api.InterceptedToolCall(**fields).approval_ref is None
    call = api.InterceptedToolCall(**fields, approval_ref="approval-1")
    assert call.model_dump(mode="json")["approval_ref"] == "approval-1"
