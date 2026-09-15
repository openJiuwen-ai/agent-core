# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for dated browser-agent log layout under the unified log dir."""

import logging

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime import (
    browser_logging,
)


@pytest.fixture(autouse=True)
def _fresh_browser_logger(monkeypatch):
    """Give each test an isolated browser-agent logger."""
    # 隔离外层日期布局环境：未显式注入的用例一律按旧布局运行
    monkeypatch.delenv("JIUWENSWARM_LOG_DATE_ROOT", raising=False)
    logger = logging.getLogger(browser_logging._BROWSER_LOGGER_NAME)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    for attr in (browser_logging._BROWSER_LOG_ANNOUNCED_MARKER,):
        if hasattr(logger, attr):
            delattr(logger, attr)
    yield logger
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


class TestGetBrowserAgentLogPath:
    """Path resolution for the three configuration modes."""

    @staticmethod
    def test_unified_dir_uses_today_date_dir(tmp_path, monkeypatch):
        # 注入目录以 core 结尾（桌面真实形态）：日期目录建在父层，再进 core
        core_dir = tmp_path / "core"
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(core_dir))
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)

        from openjiuwen.core.common.logging.dated_file_handler import (
            format_log_date,
        )

        path = browser_logging.get_browser_agent_log_path()
        assert path == (tmp_path / format_log_date() / "core" / "browser_agent.log").resolve()

    @staticmethod
    def test_legacy_env_falls_back(tmp_path, monkeypatch):
        monkeypatch.delenv("JIUWENSWARM_CORE_LOG_DIR", raising=False)
        monkeypatch.setenv("JIUWENSWARM_LOG_DIR", str(tmp_path))
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)

        path = browser_logging.get_browser_agent_log_path()
        assert path.parent.name != ""
        assert path.name == "browser_agent.log"
        assert tmp_path.name in path.parts

    @staticmethod
    def test_no_env_uses_cwd_logs(tmp_path, monkeypatch):
        monkeypatch.delenv("JIUWENSWARM_CORE_LOG_DIR", raising=False)
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)
        monkeypatch.chdir(tmp_path)

        path = browser_logging.get_browser_agent_log_path()
        assert path == (tmp_path / "logs" / "browser_agent.log").resolve()

    @staticmethod
    def test_disable_value_returns_none(tmp_path, monkeypatch):
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", "off")

        assert browser_logging.get_browser_agent_log_path() is None

    @staticmethod
    def test_explicit_override_wins_flat(tmp_path, monkeypatch):
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(tmp_path))
        explicit = tmp_path / "custom" / "browser.log"
        monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", str(explicit))

        path = browser_logging.get_browser_agent_log_path()
        assert path == explicit.resolve()


class TestGetBrowserAgentLogger:
    """Handler installation for each configuration mode."""

    @staticmethod
    def _marked_handlers():
        logger = logging.getLogger(browser_logging._BROWSER_LOGGER_NAME)
        return [h for h in logger.handlers if getattr(h, browser_logging._BROWSER_HANDLER_MARKER, False)]

    def test_unified_dir_installs_dated_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(tmp_path))
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)

        browser_logging.get_browser_agent_logger()

        handlers = self._marked_handlers()
        assert len(handlers) == 1
        from openjiuwen.core.common.logging.dated_file_handler import (
            DatedDailyFileHandler,
        )

        assert isinstance(handlers[0], DatedDailyFileHandler)
        assert handlers[0].base_dir == str(tmp_path)

    def test_dated_handler_writes_under_date_dir(self, tmp_path, monkeypatch):
        # 注入目录以 core 结尾（桌面真实形态）：日志落 <父>/<日期>/core/ 下
        core_dir = tmp_path / "core"
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(core_dir))
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)

        browser_logging.browser_agent_log_info("dated hello")

        from openjiuwen.core.common.logging.dated_file_handler import (
            format_log_date,
        )

        target = tmp_path / format_log_date() / "core" / "browser_agent.log"
        assert target.exists()
        assert "dated hello" in target.read_text(encoding="utf-8")

    def test_no_env_flat_file_appends(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JIUWENSWARM_CORE_LOG_DIR", raising=False)
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)
        monkeypatch.chdir(tmp_path)

        browser_logging.browser_agent_log_info("first line")
        # A second configuration round must append, not truncate.
        browser_logging.browser_agent_log_warning("second line")

        target = tmp_path / "logs" / "browser_agent.log"
        content = target.read_text(encoding="utf-8")
        assert "first line" in content
        assert "second line" in content

    def test_disabled_adds_no_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", "none")

        browser_logging.get_browser_agent_logger()

        assert self._marked_handlers() == []


class TestOuterDateLayout:
    """Outer-date layout (JIUWENSWARM_LOG_DATE_ROOT injected by the desktop)."""

    @staticmethod
    def _inject_env(monkeypatch, tmp_path):
        date_root = tmp_path / "logs"
        core_dir = date_root / "jiuwenswarm" / "uid1" / "core"
        monkeypatch.setenv("JIUWENSWARM_LOG_DATE_ROOT", str(date_root))
        monkeypatch.setenv("JIUWENSWARM_CORE_LOG_DIR", str(core_dir))
        monkeypatch.delenv("JIUWENSWARM_LOG_DIR", raising=False)
        monkeypatch.delenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", raising=False)
        return date_root, core_dir

    def test_path_uses_outer_date_dir(self, tmp_path, monkeypatch):
        date_root, _ = self._inject_env(monkeypatch, tmp_path)

        from openjiuwen.core.common.logging.dated_file_handler import (
            format_log_date,
        )

        path = browser_logging.get_browser_agent_log_path()
        expected = (
            date_root
            / format_log_date()
            / "jiuwenswarm"
            / "uid1"
            / "core"
            / "browser_agent.log"
        )
        assert path == expected.resolve()

    def test_handler_writes_under_outer_date_dir(self, tmp_path, monkeypatch):
        date_root, _ = self._inject_env(monkeypatch, tmp_path)

        browser_logging.browser_agent_log_info("outer hello")

        from openjiuwen.core.common.logging.dated_file_handler import (
            format_log_date,
        )

        target = (
            date_root
            / format_log_date()
            / "jiuwenswarm"
            / "uid1"
            / "core"
            / "browser_agent.log"
        )
        assert target.exists()
        assert "outer hello" in target.read_text(encoding="utf-8")

    def test_dated_handler_base_is_date_root(self, tmp_path, monkeypatch):
        date_root, core_dir = self._inject_env(monkeypatch, tmp_path)

        unified = browser_logging._unified_log_dir()
        assert unified is not None
        assert browser_logging._dated_handler_base(unified) == date_root
        assert browser_logging._dated_core_dir(unified) == (
            date_root / browser_logging.format_log_date() / "jiuwenswarm" / "uid1" / "core"
        )
