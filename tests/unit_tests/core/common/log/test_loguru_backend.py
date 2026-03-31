# -*- coding: UTF-8 -*-

import importlib
import json
import logging
import os

import pytest

pytest.importorskip("loguru")

from openjiuwen.core.common.exception.errors import BaseError  # noqa: E402
from openjiuwen.core.common.logging import (  # noqa: E402
    LogManager,
    set_session_id,
)
from openjiuwen.core.common.logging.events import (  # noqa: E402
    LogEventType,
    create_log_event,
)
from tests.unit_tests.core.common.log.test_logger import (  # noqa: E402
    patched_logging_config,
    write_yaml_config,
)


@pytest.fixture(autouse=True)
def reset_log_manager():
    LogManager.reset()
    set_session_id()
    yield
    LogManager.reset()
    set_session_id()


def _read_lines(log_file_path: str) -> list[str]:
    with open(log_file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f.readlines() if line.strip()]


def _read_last_json_record(log_file_path: str) -> dict:
    lines = _read_lines(log_file_path)
    assert lines
    return json.loads(lines[-1])


def _read_json_records(log_file_path: str) -> list[dict]:
    return [json.loads(line) for line in _read_lines(log_file_path)]


def _load_log_config(config_file_path: str):
    log_config_module = importlib.import_module("openjiuwen.core.common.logging.log_config")
    return log_config_module.LogConfig(config_file_path)


def _get_loguru_logger_class():
    loguru_module = importlib.import_module("openjiuwen.core.common.logging.loguru.loguru_impl")
    return loguru_module.LoguruLogger


def _load_loguru_provider_functions():
    provider_module = importlib.import_module("openjiuwen.core.common.logging.loguru.config_provider")
    return provider_module.build_loguru_logger_config, provider_module.load_loguru_backend_config


def _make_loguru_config(tmp_path, **overrides):
    base_config = {
        "logging": {
            "backend": "loguru",
            "defaults": {
                "level": "INFO",
                "enqueue": False,
                "catch": False,
                "backtrace": False,
                "diagnose": False,
            },
            "sinks": {
                "console": {
                    "target": "stdout",
                    "level": "INFO",
                    "serialize": False,
                    "colorize": False,
                    "enqueue": False,
                    "format": "{extra[log_type]} | {extra[trace_id]} | {message}",
                },
                "team_console": {
                    "target": "stdout",
                    "level": "DEBUG",
                    "serialize": False,
                    "colorize": False,
                    "enqueue": False,
                    "format": "{extra[log_type]} | {extra[trace_id]} | {message}",
                },
                "app_json": {
                    "target": os.path.join(tmp_path, "common.jsonl"),
                    "level": "INFO",
                    "serialize": True,
                    "enqueue": False,
                    "encoding": "utf-8",
                },
                "perf_json": {
                    "target": os.path.join(tmp_path, "performance.jsonl"),
                    "level": "INFO",
                    "serialize": True,
                    "enqueue": False,
                    "encoding": "utf-8",
                },
            },
            "routes": {
                "common": ["console", "app_json"],
                "interface": ["console", "app_json"],
                "performance": ["perf_json"],
                "team": ["team_console"],
                "*": ["console", "app_json"],
            },
            "loggers": {
                "team": {
                    "level": "DEBUG",
                }
            },
        }
    }

    for key, value in overrides.items():
        base_config["logging"][key] = value

    return base_config


def _make_default_config(tmp_path, **overrides):
    base_config = {
        "logging": {
            "backend": "default",
            "level": "INFO",
            "format": "%(log_type)s | %(trace_id)s | %(levelname)s | %(message)s",
            "log_path": str(tmp_path),
            "output": ["console"],
            "interface_output": ["console"],
            "performance_output": ["console"],
            "loggers": {},
        }
    }

    for key, value in overrides.items():
        base_config["logging"][key] = value

    return base_config


def _make_event_first_loguru_config(tmp_path, **overrides):
    config = _make_loguru_config(tmp_path, **overrides)
    config["logging"]["sinks"]["app_json"]["serialize_mode"] = "event"
    config["logging"]["sinks"]["perf_json"]["serialize_mode"] = "event"
    return config


def test_initialize_can_switch_to_loguru_backend_via_argument(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_arg.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        LogManager.initialize(backend="loguru")
        logger = LogManager.get_logger("common")

        assert isinstance(logger, _get_loguru_logger_class())

        set_session_id("TRACE-ARG")
        logger.info("value=%s", 42)

        output = capsys.readouterr().out
        assert "common | TRACE-ARG | value=42" in output


def test_yaml_backend_can_bootstrap_loguru_backend(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_yaml.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")

        assert isinstance(logger, _get_loguru_logger_class())

        set_session_id("TRACE-YAML")
        logger.info("yaml backend active")

        output = capsys.readouterr().out
        assert "common | TRACE-YAML | yaml backend active" in output


def test_builtin_default_backend_initializes_common_with_loguru_class():
    LogManager.reset()
    logger = LogManager.get_logger("common")

    assert isinstance(logger, _get_loguru_logger_class())


def test_runtime_reconfigure_rebuilds_common_and_runner_loggers(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "default_then_loguru.yaml")
    write_yaml_config(config_file_path, _make_default_config(tmp_path))

    logging_module = importlib.import_module("openjiuwen.core.common.logging")
    events_module = importlib.import_module("openjiuwen.core.common.logging.events")
    log_config_module = importlib.import_module("openjiuwen.core.common.logging.log_config")
    default_logger_module = importlib.import_module("openjiuwen.core.common.logging.default.default_impl")

    with patched_logging_config(config_file_path):
        logging_module.logger.info("default common")

        assert isinstance(LogManager.get_logger("common"), default_logger_module.DefaultLogger)
        assert isinstance(events_module._get_common_logger(), default_logger_module.DefaultLogger)

        log_config_module.configure_log_config(_make_loguru_config(tmp_path)["logging"])

        common_logger = LogManager.get_logger("common")
        runner_logger = LogManager.get_logger("runner")

        assert isinstance(common_logger, _get_loguru_logger_class())
        assert isinstance(runner_logger, _get_loguru_logger_class())
        assert isinstance(events_module._get_common_logger(), _get_loguru_logger_class())

        set_session_id("TRACE-RECONFIGURE")
        logging_module.logger.info("common switched to loguru")
        logging_module.runner_logger.info("runner switched to loguru")

        assert isinstance(logging_module.logger._logger, _get_loguru_logger_class())
        assert isinstance(logging_module.runner_logger._logger, _get_loguru_logger_class())

        output = capsys.readouterr().out
        assert "common | TRACE-RECONFIGURE | common switched to loguru" in output
        assert "runner | TRACE-RECONFIGURE | runner switched to loguru" in output


def test_structured_event_type_uses_native_loguru_json_envelope(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_json_event_type.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-JSON")

        logger.info(
            "Agent started",
            event_type=LogEventType.AGENT_START,
            module_id="agent_123",
            agent_type="react",
        )

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))
    record = payload["record"]

    assert record["message"] == "Agent started"
    assert record["extra"]["log_type"] == "common"
    assert record["extra"]["trace_id"] == "TRACE-JSON"
    assert record["extra"]["event"]["event_type"] == "agent_start"
    assert record["extra"]["event"]["module_id"] == "agent_123"
    assert record["extra"]["event"]["agent_type"] == "react"
    assert record["extra"]["event"]["message"] == "Agent started"


def test_event_object_keeps_message_and_event_separate_in_json_output(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_json_event.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    event = create_log_event(
        LogEventType.AGENT_START,
        module_id="agent_123",
        message="Original event message",
    )

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-EVENT")
        logger.info("Replacement message", event=event)

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))
    record = payload["record"]

    assert record["message"] == "Replacement message"
    assert record["extra"]["event"]["message"] == "Replacement message"
    assert record["extra"]["event"]["module_id"] == "agent_123"
    assert record["extra"]["trace_id"] == "TRACE-EVENT"


def test_event_type_can_emit_event_first_json_payload(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_event_first_event_type.yaml")
    write_yaml_config(config_file_path, _make_event_first_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-EVENT-FIRST")

        logger.info(
            "Agent started",
            event_type=LogEventType.AGENT_START,
            module_id="agent_123",
            agent_type="react",
        )

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))

    assert "record" not in payload
    assert "text" not in payload
    assert payload["event_type"] == "agent_start"
    assert payload["module_id"] == "agent_123"
    assert payload["module_name"] == "common"
    assert payload["module_type"] == "agent"
    assert payload["agent_type"] == "react"
    assert payload["trace_id"] == "TRACE-EVENT-FIRST"
    assert payload["message"] == "Agent started"
    assert payload["metadata"]["_log_context"]["log_type"] == "common"
    assert payload["metadata"]["_log_context"]["source"]["function"] == "test_event_type_can_emit_event_first_json_payload"


def test_event_object_event_first_json_preserves_metadata_and_context(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_event_first_event_object.yaml")
    write_yaml_config(config_file_path, _make_event_first_loguru_config(tmp_path))

    event = create_log_event(
        LogEventType.AGENT_START,
        module_id="agent_123",
        message="Original event message",
        metadata={"biz": "value", "_log_context": {"stale": True}},
    )

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-EVENT-OBJECT")
        logger.info("Replacement message", event=event)

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))

    assert payload["message"] == "Replacement message"
    assert payload["module_id"] == "agent_123"
    assert payload["metadata"]["biz"] == "value"
    assert payload["metadata"]["_log_context"]["log_type"] == "common"
    assert "stale" not in payload["metadata"]["_log_context"]


def test_plain_log_can_emit_event_first_json_payload(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_event_first_plain.yaml")
    write_yaml_config(config_file_path, _make_event_first_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-PLAIN")
        logger.info("plain log")

        output = capsys.readouterr().out
        assert "common | TRACE-PLAIN | plain log" in output

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))

    assert payload["event_type"] == "plain_log"
    assert payload["log_level"] == "INFO"
    assert payload["message"] == "plain log"
    assert payload["module_id"] == "common"
    assert payload["module_name"] == "common"
    assert payload["module_type"] == "system"
    assert payload["trace_id"] == "TRACE-PLAIN"
    assert payload["metadata"]["_log_context"]["log_type"] == "common"


def test_exception_can_emit_event_first_json_failure_payload(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_event_first_exception.yaml")
    write_yaml_config(config_file_path, _make_event_first_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        set_session_id("TRACE-EXCEPTION")

        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("plain failure")

    payload = _read_last_json_record(os.path.join(tmp_path, "common.jsonl"))

    assert payload["event_type"] == "plain_log"
    assert payload["log_level"] == "ERROR"
    assert payload["message"] == "plain failure"
    assert payload["status"] == "failure"
    assert payload["exception"] == "boom"
    assert payload["error_message"] == "boom"
    assert "RuntimeError" in payload["stacktrace"]
    assert payload["metadata"]["_log_context"]["log_type"] == "common"


def test_logger_level_override_affects_only_logger_threshold(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_logger_level.yaml")
    config = _make_loguru_config(tmp_path)
    config["logging"]["sinks"]["console"]["level"] = "DEBUG"
    config["logging"]["loggers"] = {
        "agent": {
            "level": "DEBUG",
        }
    }
    write_yaml_config(config_file_path, config)

    with patched_logging_config(config_file_path):
        common_logger = LogManager.get_logger("common")
        agent_logger = LogManager.get_logger("agent")

        set_session_id("TRACE-LEVEL")
        common_logger.info("common info visible")
        common_logger.debug("common debug hidden")
        agent_logger.debug("agent debug visible")
        agent_logger.info("agent info visible")

        output = capsys.readouterr().out
        assert "common debug hidden" not in output
        assert "common | TRACE-LEVEL | common info visible" in output
        assert "agent | TRACE-LEVEL | agent debug visible" in output
        assert "agent | TRACE-LEVEL | agent info visible" in output

    records = _read_json_records(os.path.join(tmp_path, "common.jsonl"))
    messages = [record["record"]["message"] for record in records]

    assert "common info visible" in messages
    assert "agent info visible" in messages
    assert "agent debug visible" not in messages


def test_team_logger_debug_uses_dedicated_debug_console_sink(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_team_debug.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        team_logger = importlib.import_module("openjiuwen.core.common.logging").team_logger
        set_session_id("TRACE-TEAM-DEBUG")
        team_logger.debug("team debug visible")

        output = capsys.readouterr().out
        assert "team | TRACE-TEAM-DEBUG | team debug visible" in output


def test_loguru_logger_rejects_logger_specific_sinks(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_invalid_logger_sinks.yaml")
    config = _make_loguru_config(
        tmp_path,
        loggers={
            "performance": {
                "sinks": ["perf_json"],
            }
        },
    )
    write_yaml_config(config_file_path, config)

    with pytest.raises(BaseError):
        _load_log_config(config_file_path)


def test_loguru_provider_builds_dynamic_logger_config(tmp_path):
    build_loguru_logger_config, load_loguru_backend_config = _load_loguru_provider_functions()
    config = _make_loguru_config(tmp_path)["logging"]
    config["loggers"] = {
        "agent": {
            "level": "DEBUG",
        }
    }

    normalized_config = load_loguru_backend_config(config)
    agent_config = build_loguru_logger_config(normalized_config, "agent")

    assert agent_config["backend"] == "loguru"
    assert agent_config["effective_level"] == logging.DEBUG
    assert [sink["name"] for sink in agent_config["sinks"]] == ["console", "app_json"]
    assert agent_config["sinks"][0]["target"] == "stdout"


def test_logger_set_level_only_changes_adapter_threshold(tmp_path, capsys):
    config_file_path = os.path.join(tmp_path, "loguru_set_level.yaml")
    write_yaml_config(config_file_path, _make_loguru_config(tmp_path))

    with patched_logging_config(config_file_path):
        logger = LogManager.get_logger("common")
        logger.set_level(logging.ERROR)
        set_session_id("TRACE-SET-LEVEL")

        logger.info("info hidden after set_level")
        logger.error("error visible after set_level")

        output = capsys.readouterr().out
        assert "info hidden after set_level" not in output
        assert "common | TRACE-SET-LEVEL | error visible after set_level" in output


def test_loguru_logger_rejects_sink_overrides(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_invalid_override.yaml")
    config = _make_loguru_config(
        tmp_path,
        loggers={
            "interface": {
                "sink_overrides": {
                    "missing_sink": {
                        "target": os.path.join(tmp_path, "missing.log"),
                    }
                }
            }
        },
    )
    write_yaml_config(config_file_path, config)

    with pytest.raises(BaseError):
        _load_log_config(config_file_path)


def test_loguru_backend_rejects_default_specific_root_keys(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_mixed_schema.yaml")
    config = _make_loguru_config(tmp_path)
    config["logging"]["output"] = ["console"]
    write_yaml_config(config_file_path, config)

    with pytest.raises(BaseError):
        _load_log_config(config_file_path)


def test_loguru_backend_rejects_invalid_serialize_mode(tmp_path):
    config_file_path = os.path.join(tmp_path, "loguru_invalid_serialize_mode.yaml")
    config = _make_loguru_config(tmp_path)
    config["logging"]["sinks"]["app_json"]["serialize_mode"] = "invalid"
    write_yaml_config(config_file_path, config)

    with pytest.raises(BaseError):
        _load_log_config(config_file_path)
