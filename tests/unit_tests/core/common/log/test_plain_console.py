"""The default console sink remains readable when redirected to service logs."""

from io import StringIO

from loguru import logger

from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG


def test_default_console_output_does_not_contain_ansi_colors():
    config = DEFAULT_INNER_LOG_CONFIG["sinks"]["console"]
    output = StringIO()
    handler = logger.add(output, colorize=config["colorize"], format=config["format"], level=config["level"])
    try:
        logger.bind(log_type="common", trace_id="plain-test", short_path="test").info("plain-console-marker")
    finally:
        logger.remove(handler)
    assert "plain-console-marker" in output.getvalue()
    assert "\x1b" not in output.getvalue()
