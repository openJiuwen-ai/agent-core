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
        "model": "gpt-effective",
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
    assert "模型 gpt-effective" in text
    assert "request_rejected" in text
    assert "failure_id=fid-1" in text
    assert "round_id=3" in text
    assert "http_status=400" in text
    assert "sdk_error_type=SdkError" in text
    assert "sdk_error_code=badRequest" in text
    assert "user_action_required=True" in text
    assert "CLI 已成功启动" in text
    assert "不得将其诊断为 CLI 未安装" in text
    assert "已识别到必须由用户或外部系统完成的操作" in text
    assert "不表示已安排新的 round" in text
    logger.info("rendered: %s", text)


def test_false_user_action_is_not_rendered_as_definitive(_lang):
    text = MessageHandler._render_external_runtime_failed(
        _Msg(protocol="json", content=_failure_payload(user_action_required=False)),
    )

    assert text is not None
    assert "user_action_required=False" in text
    assert "尚未识别到必须由用户完成的操作" in text
    assert "后续仍可能需要用户介入" in text


def test_explicit_cli_path_is_rendered_but_missing_path_is_omitted(_lang: None) -> None:
    with_path = MessageHandler._render_external_runtime_failed(
        _Msg(protocol="json", content=_failure_payload(cli_path="C:/tools/codex.exe")),
    )
    without_path = MessageHandler._render_external_runtime_failed(
        _Msg(protocol="json", content=_failure_payload()),
    )

    assert with_path is not None
    assert "cli_path=C:/tools/codex.exe" in with_path
    assert without_path is not None
    assert "cli_path=" not in without_path


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
    assert "model gpt-effective" in text
    assert "http_status=400" in text
    assert "CLI started successfully" in text
    assert "identified an action that the user or an external system must complete" in text
    assert "does not mean a new round was scheduled" in text


def test_missing_model_renders_unknown_for_backward_compatibility(_lang):
    payload = json.loads(_failure_payload())
    payload.pop("model")

    text = MessageHandler._render_external_runtime_failed(
        _Msg(protocol="json", content=json.dumps(payload)),
    )

    assert text is not None
    assert "模型 &lt;unknown&gt;" in text
