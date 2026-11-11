# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""StructuredLoggerMixin PII masking tests.

Core logging module masks PII at the mixin level so standalone hosts (no
jiuwenswarm pipeline) are covered: plain messages via ``_sanitize_message``,
structured event field values via ``_build_structured_event_dict``.
"""

from openjiuwen.core.common.logging.base_impl import StructuredLoggerMixin
from openjiuwen.core.common.logging.events import LogEventType


class _T(StructuredLoggerMixin):
    log_type = "test"


def test_sanitize_message_masks_pii_and_control_chars() -> None:
    out = _T()._sanitize_message("手机 13800138000\n换行")
    assert "13800138000" not in out
    assert "\\n" in out  # 控制字符转义仍在


def test_structured_event_fields_masked() -> None:
    event_dict = _T()._build_structured_event_dict(
        "INFO", "LLM error", LogEventType.LLM_CALL_ERROR,
        last_user_preview="电话 13800138000",
        extra_params={"note": "卡号 4242424242424242"},
    )
    assert "13800138000" not in str(event_dict)
    assert "4242424242424242" not in str(event_dict)


def test_structured_event_clean_fields_untouched() -> None:
    event_dict = _T()._build_structured_event_dict(
        "INFO", "plain", LogEventType.LLM_CALL_ERROR,
        model_name="gpt-x", messages_count=3,
    )
    assert event_dict["model_name"] == "gpt-x"
    assert event_dict["messages_count"] == 3
