# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local file integration: malformed Unicode must not lose error records."""

from unittest.mock import Mock

import pytest

from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.common.logging.default.default_impl import DefaultLogger


@pytest.mark.parametrize("structured", [False, True])
def test_file_error_record_survives_surrogates_and_rotation(tmp_path, structured):
    path = tmp_path / "encoding.log"
    logger = DefaultLogger(
        "test-surrogate-log",
        {
            "output": ["file"],
            "level": "INFO",
            "log_file": str(path),
            "max_bytes": 1024,
            "backup_count": 2,
            "format": "%(levelname)s %(message)s",
            "propagate": False,
        },
    )
    handler = logger._logger.handlers[0]
    handler.handleError = Mock()
    try:
        logger.info("x" * 1100)
        if structured:
            logger.error(
                "request encoding error",
                event_type=LogEventType.LLM_CALL_ERROR,
                exception="UnicodeEncodeError: \ud83d",
                model_name="test-model",
            )
        else:
            logger.error("request encoding error: 中文 🔴 \ud83d\udfff")
        handler.flush()
        content = path.read_text(encoding="utf-8")
        assert "ERROR" in content
        assert "request encoding error" in content
        assert "\\ud83d" in content
        if not structured:
            assert "中文 🔴" in content
            assert "\\udfff" in content
        handler.handleError.assert_not_called()
    finally:
        handler.close()
        logger._logger.removeHandler(handler)
