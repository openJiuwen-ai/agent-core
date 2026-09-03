# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``MessageHandler`` external_runtime_failed JSON rendering.

``_render_external_runtime_failed`` is a staticmethod that depends only on the
message fields, the i18n table and the pure renderer — no coordination wiring.
"""

from __future__ import annotations

import json

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.i18n import get_language, set_language
from tests.test_logger import logger


def _failure_payload(**overrides) -> str:
    base = {
        "type": "external_runtime_failed",
        "failure_id": "fid-1",
        "team_name": "team",
        "member_name": "worker1",
        "agent_kind": "codex",
        "phase": "turn",
        "category": "request_rejected",
        "user_action_required": True,
        "summary": "Codex 400",
        "suggested_action": "inspect request configuration",
        "reason": {
            "message": "bad request",
            "sdk_error_type": "SdkError",
            "sdk_error_code": "badRequest",
            "http_status": 400,
        },
        "round_id": 3,
    }
    base.update(overrides)
    return json.dumps(base)


class _Msg:
    def __init__(self, *, protocol: str, content: str) -> None:
        self.protocol = protocol
        self.content = content
        self.message_id = "m1"
        self.from_member_name = "worker1"


@pytest.fixture
def _lang():
    saved = get_language()
    set_language("cn")
    yield
    set_language(saved)


def test_renders_external_runtime_failed_as_team_event(_lang):
    text = MessageHandler._render_external_runtime_failed(_Msg(protocol="json", content=_failure_payload()))
    assert text is not None
    assert 'kind="external-runtime-failed"' in text
    assert "worker1" in text
    assert "request_rejected" in text
    assert "failure_id=fid-1" in text
    assert "round_id=3" in text
    assert "http_status=400" in text
    assert "sdk_error_type=SdkError" in text
    assert "sdk_error_code=badRequest" in text
    assert "CLI 已成功启动" in text
    assert "不得将其诊断为 CLI 未安装" in text
    logger.info("rendered: %s", text)


def test_non_json_returns_none(_lang):
    assert MessageHandler._render_external_runtime_failed(_Msg(protocol="plain", content="hi")) is None


def test_wrong_type_returns_none(_lang):
    assert (
        MessageHandler._render_external_runtime_failed(
            _Msg(protocol="json", content=json.dumps({"type": "tool_approval_result"}))
        )
        is None
    )


def test_malformed_payload_returns_none_no_crash(_lang):
    assert (
        MessageHandler._render_external_runtime_failed(
            _Msg(protocol="json", content='{"type":"external_runtime_failed"}')
        )
        is None
    )


def test_unreadable_json_returns_none(_lang):
    assert MessageHandler._render_external_runtime_failed(_Msg(protocol="json", content="not json")) is None


def test_english_render(_lang):
    set_language("en")
    text = MessageHandler._render_external_runtime_failed(_Msg(protocol="json", content=_failure_payload()))
    assert text is not None
    assert "external-runtime-failed" in text
    assert "worker1" in text
    assert "http_status=400" in text
    assert "CLI started successfully" in text
