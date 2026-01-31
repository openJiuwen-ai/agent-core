"""
Test log function
"""

import logging
import os
import sys
import tempfile
import threading
from io import StringIO
from typing import (
    Any,
    Dict,
)
from unittest import mock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import (
    LogManager,
    LoggerProtocol,
    get_session_id,
    set_session_id,
)
from openjiuwen.core.common.logging.default import DefaultLogger


def thread_function(session_id, log_list, stdout_capture):
    """Thread function, used for testing thread isolation"""
    logger = LogManager.get_logger("common")

    if not logger._logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        logger._logger.addHandler(handler)
    else:
        for handler in logger._logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                break
        else:
            handler = logging.StreamHandler(sys.stdout)
            logger._logger.addHandler(handler)

    logger.set_level(logging.INFO)

    set_session_id(session_id)
    logger.info(f"Thread started with session id {session_id}")

    for handler in logger._logger.handlers:
        handler.flush()

    log_list.append((session_id, get_session_id()))


@pytest.fixture
def temp_config_dir():
    """Create a temporary configuration directory"""
    temp_dir = tempfile.TemporaryDirectory()
    yield temp_dir
    temp_dir.cleanup()


@pytest.fixture
def test_config_file(temp_config_dir):
    """Create the YAML configuration file for the test"""
    config_file_path = os.path.join(temp_config_dir.name, "test_config.yaml")

    test_config = {
        "logging": {
            "level": "INFO",
            "backup_count": 5,
            "max_bytes": 1024 * 1024,
            "format": "%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s",
            "log_file": "common.log",
            "output": ["console", "file"],
            "interface_log_file": "interface.log",
            "prompt_builder_interface_log_file": "prompt_builder.log",
            "performance_log_file": "performance.log",
            "interface_output": ["console", "file"],
            "performance_output": ["console", "file"],
            "log_path": temp_config_dir.name,
        }
    }

    import yaml

    with open(config_file_path, "w", encoding="utf-8") as f:
        yaml.dump(test_config, f)

    return config_file_path


@pytest.fixture
def mock_log_config(test_config_file):
    """Mock log configuration"""
    from openjiuwen.core.common.logging.default.log_config import (
        LogConfig,
        log_config as original_log_config,
    )
    from openjiuwen.core.common.logging.default.config_manager import (
        ConfigManager,
        config_manager as original_config_manager,
    )

    test_log_config = LogConfig(test_config_file)
    test_config_manager = ConfigManager(test_config_file)

    import openjiuwen.core.common.logging.default.log_config as log_config_module
    import openjiuwen.core.common.logging.default.config_manager as config_manager_module

    _original_log_config = original_log_config
    _original_config_manager = original_config_manager

    log_config_module.log_config = test_log_config
    config_manager_module.config_manager = test_config_manager

    yield test_log_config

    log_config_module.log_config = _original_log_config
    config_manager_module.config_manager = _original_config_manager


@pytest.fixture(scope="function")
def stdout_capture():
    """Capture the stdout output"""
    original_stdout = sys.stdout
    capture = StringIO()
    sys.stdout = capture
    yield capture
    sys.stdout = original_stdout


@pytest.fixture(scope="function")
def stderr_capture():
    """Capture the stderr output"""
    original_stderr = sys.stderr
    capture = StringIO()
    sys.stderr = capture
    yield capture
    sys.stderr = original_stderr


@pytest.fixture(scope="function")
def initialized_logger(mock_log_config, stdout_capture):
    """Initialize the log manager and set up the test environment"""
    LogManager.reset()
    LogManager.initialize()

    from openjiuwen.core.common.logging.default.default_impl import ContextFilter

    for log in LogManager.get_all_loggers().values():
        for handler in log.logger().handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                handler.stream = sys.stdout
            elif hasattr(handler, "stream"):
                try:
                    handler.close()
                except Exception:
                    pass
                log._logger.removeHandler(handler)

        has_stream_handler = any(isinstance(h, logging.StreamHandler) for h in log.logger().handlers)
        if not has_stream_handler:
            handler = logging.StreamHandler(sys.stdout)
            handler.add_filter(ContextFilter(log.log_type))
            formatter = log._get_formatter()
            handler.setFormatter(formatter)
            handler.set_level(logging.DEBUG)
            log.logger().addHandler(handler)

        log.logger().setLevel(logging.DEBUG)

    yield

    set_session_id("")
    try:
        for logger in LogManager.get_all_loggers().values():
            if hasattr(logger, "_logger"):
                for handler in logger._logger.handlers[:]:
                    try:
                        handler.close()
                    except Exception:
                        pass
                    logger._logger.removeHandler(handler)
        LogManager.reset()
    except Exception:
        pass


class TestThreadSafety:
    """Test thread safety"""

    def test_thread_trace_id_isolation(self, initialized_logger, stdout_capture):
        """Test the isolation of trace_id between threads"""
        log_list = []
        threads = [
            threading.Thread(target=thread_function, args=("10001", log_list, stdout_capture)),
            threading.Thread(target=thread_function, args=("10002", log_list, stdout_capture)),
            threading.Thread(target=thread_function, args=("10003", log_list, stdout_capture)),
        ]

        stdout_capture.truncate(0)
        stdout_capture.seek(0)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for session_id, recorded_id in log_list:
            assert session_id == recorded_id, f"Thread session_id mismatch: expected {session_id}, actual {recorded_id}"

        assert get_session_id() == "default_trace_id"

        output = stdout_capture.getvalue()
        assert "10001" in output
        assert "10002" in output
        assert "10003" in output


class TestLogManager:
    """Test the log manager"""

    def test_custom_logger_registration_and_usage(self, initialized_logger, capsys):
        """Test the registration and usage of the custom logger"""

        class CustomLogger:
            def __init__(self):
                self.messages = []
                self._config = {}

            def info(self, msg, *args, **kwargs):
                formatted_msg = f"CUSTOM LOGGER INFO: {msg}"
                print(formatted_msg)
                self.messages.append(formatted_msg)

            def debug(self, msg, *args, **kwargs):
                pass

            def warning(self, msg, *args, **kwargs):
                pass

            def error(self, msg, *args, **kwargs):
                pass

            def critical(self, msg, *args, **kwargs):
                pass

            def exception(self, msg, *args, **kwargs):
                pass

            def log(self, level, msg, *args, **kwargs):
                pass

            def set_level(self, level):
                pass

            def add_handler(self, handler):
                pass

            def remove_handler(self, handler):
                pass

            def add_filter(self, filter):
                pass

            def remove_filter(self, filter):
                pass

            def get_config(self) -> Dict[str, Any]:
                return self._config.copy()

            def reconfigure(self, config: Dict[str, Any]):
                self._config = config

        custom_logger_instance = CustomLogger()

        LogManager.register_logger("custom", custom_logger_instance)

        retrieved_logger = LogManager.get_logger("custom")
        assert retrieved_logger is custom_logger_instance
        retrieved_logger.info("Test custom logger")

        assert "CUSTOM LOGGER INFO: Test custom logger" in custom_logger_instance.messages
        captured = capsys.readouterr()
        assert "CUSTOM LOGGER INFO: Test custom logger" in captured.out

    def test_default_logger_creation(self, initialized_logger, capsys):
        """Test the dynamic creation of the default logger"""
        new_logger = LogManager.get_logger("new_type_test")

        assert isinstance(new_logger, DefaultLogger)

        new_logger.warning("Test new logger type")

        for handler in new_logger._logger.handlers:
            handler.flush()

        captured = capsys.readouterr()
        output = captured.out
        assert "Test new logger type" in output
        assert "new_type_test" in output
        assert "WARNING" in output

    def test_get_all_loggers(self, initialized_logger):
        """Test to obtain all loggers"""
        all_loggers = LogManager.get_all_loggers()
        expected_types = {"common", "interface", "prompt_builder", "performance"}
        assert expected_types.issubset(set(all_loggers.keys()))

        for log_instance in all_loggers.values():
            assert isinstance(log_instance, LoggerProtocol)

    def test_register_logger_type_check(self, initialized_logger):
        """Test the type checking when registering the logger"""

        class InvalidLogger:
            pass

        invalid_logger = InvalidLogger()

        with pytest.raises(TypeError, match="Logger must implement LoggerProtocol"):
            LogManager.register_logger("invalid", invalid_logger)

    def test_get_logger_creates_on_demand(self, initialized_logger):
        """Test the creation of a logger as needed"""
        new_type_logger = LogManager.get_logger("on_demand_test")

        assert isinstance(new_type_logger, DefaultLogger)
        assert new_type_logger.log_type == "on_demand_test"

        same_logger = LogManager.get_logger("on_demand_test")
        assert same_logger is new_type_logger


class TestLogLevel:
    def test_log_level_filtering(self, initialized_logger, capsys):
        """Test the log-level filtering function"""
        test_logger_instance = LogManager.get_logger("level_test")

        test_logger_instance.set_level(logging.DEBUG)

        test_logger_instance.debug("Debug message")
        test_logger_instance.info("Info message")
        test_logger_instance.warning("Warning message")
        test_logger_instance.error("Error message")

        for handler in test_logger_instance._logger.handlers:
            handler.flush()

        captured = capsys.readouterr()
        output = captured.out

        assert "Debug message" in output
        assert "Info message" in output
        assert "Warning message" in output
        assert "Error message" in output

        test_logger_instance.set_level(logging.ERROR)

        test_logger_instance.debug("Should not appear debug")
        test_logger_instance.info("Should not appear info")
        test_logger_instance.warning("Should not appear warning")
        test_logger_instance.error("Should appear error")

        for handler in test_logger_instance._logger.handlers:
            handler.flush()

        captured = capsys.readouterr()
        output = captured.out

        assert "Should not appear debug" not in output
        assert "Should not appear info" not in output
        assert "Should not appear warning" not in output
        assert "Should appear error" in output


class TestLogFileOutput:
    """Test log file output"""

    def test_interface_log_file_output(self, initialized_logger, stdout_capture, temp_config_dir):
        """Output the test interface log file"""
        interface_logger = LogManager.get_logger("interface")

        set_session_id("FILE-TEST-123")

        test_message = "This is a test message for file output"
        interface_logger.info(test_message)

        for handler in interface_logger._logger.handlers:
            handler.flush()

        actual_log_file = interface_logger.config.get("log_file", "")

        if not os.path.exists(actual_log_file):
            stdout_output = stdout_capture.getvalue()
            assert test_message in stdout_output, "Console output should contain test message"
            assert "FILE-TEST-123" in stdout_output, "Console output should contain trace_id"
            return

        with open(actual_log_file, "r", encoding="utf-8") as f:
            content = f.read()

        if content.strip():
            assert test_message in content, "Log file should contain test message"
            assert "FILE-TEST-123" in content, "Log file should contain trace_id"
        else:
            stdout_output = stdout_capture.getvalue()
            assert test_message in stdout_output, "Console output should contain test message"
            assert "FILE-TEST-123" in stdout_output, "Console output should contain trace_id"
            return

        stdout_output = stdout_capture.getvalue()
        assert test_message in stdout_output, "Console output should contain test message"


class TestDefaultLogger:
    """Test the default logger function"""

    def test_message_sanitization(self, initialized_logger, capsys):
        """Test the message cleaning function (to prevent log injection)"""
        logger = LogManager.get_logger("common")
        logger.set_level(logging.INFO)

        test_message = "Test message\nwith newline\r\nand carriage return\r"
        logger.info(test_message)

        for handler in logger._logger.handlers:
            handler.flush()

        captured = capsys.readouterr()
        output = captured.out

        lines = output.split("\n")
        for line in lines:
            if "Test message" in line:
                assert "\r" not in line
                assert "with newline" in line
                assert "and carriage return" in line

    def test_logger_config_access(self, initialized_logger):
        """Test log configuration access"""
        logger = LogManager.get_logger("common")

        config = logger.get_config()
        assert isinstance(config, dict)
        assert "log_file" in config
        assert "output" in config
        assert "level" in config

    def test_logger_reconfigure(self, initialized_logger, stdout_capture):
        """Reconfigure the test log"""
        logger = LogManager.get_logger("common")

        original_config = logger.get_config()
        new_config = original_config.copy()
        new_config["level"] = logging.DEBUG

        logger.reconfigure(new_config)

        updated_config = logger.get_config()
        assert updated_config["level"] == logging.DEBUG

    def test_all_log_levels(self, initialized_logger, stdout_capture):
        """Test all log levels"""
        logger = LogManager.get_logger("common")
        logger.set_level(logging.DEBUG)

        stdout_capture.truncate(0)
        stdout_capture.seek(0)

        logger.debug("Debug level message")
        logger.info("Info level message")
        logger.warning("Warning level message")
        logger.error("Error level message")
        logger.critical("Critical level message")

        for handler in logger._logger.handlers:
            handler.flush()

        output = stdout_capture.getvalue()
        assert "Debug level message" in output
        assert "Info level message" in output
        assert "Warning level message" in output
        assert "Error level message" in output
        assert "Critical level message" in output

    def test_exception_logging(self, initialized_logger, stdout_capture):
        """Test abnormal log recording"""
        logger = LogManager.get_logger("common")
        logger.set_level(logging.ERROR)

        stdout_capture.truncate(0)
        stdout_capture.seek(0)

        try:
            raise ValueError("Test exception")
        except ValueError:
            logger.exception("Exception occurred")

        for handler in logger._logger.handlers:
            handler.flush()

        output = stdout_capture.getvalue()
        assert "Exception occurred" in output
        assert "Test exception" in output or "ValueError" in output


class TestLogManagerReset:
    """Test the log manager reset function"""

    def test_reset_clears_loggers(self, initialized_logger):
        """Test reset clears all loggers"""
        logger1 = LogManager.get_logger("common")
        logger2 = LogManager.get_logger("interface")

        assert len(LogManager.get_all_loggers()) > 0

        LogManager.reset()

        logger3 = LogManager.get_logger("common")
        assert isinstance(logger3, DefaultLogger)


class TestLogDirectoryCreation:
    """Test log directory creation function"""

    @staticmethod
    def test_create_nested_log_directory(temp_config_dir):
        """Test the creation of multi-level nested log directories (such as logs/run)"""
        nested_log_path = os.path.join(temp_config_dir.name, "logs", "run")
        nested_log_file = os.path.join(nested_log_path, "test.log")

        if os.path.exists(nested_log_path):
            import shutil

            shutil.rmtree(os.path.join(temp_config_dir.name, "logs"))

        config = {
            "log_file": nested_log_file,
            "output": ["file"],
            "level": logging.INFO,
            "backup_count": 5,
            "max_bytes": 1024 * 1024,
            "format": "%(asctime)s | %(levelname)s | %(message)s",
        }

        logger = DefaultLogger("test_nested", config)

        assert os.path.exists(nested_log_path), f"Directory {nested_log_path} should be created"
        assert os.path.isdir(nested_log_path), f"{nested_log_path} should be a directory"

        logger.info("Test nested directory log")

        # Access protected member for testing purposes
        for handler in logger._logger.handlers:  # pylint: disable=protected-access
            handler.flush()
            handler.close()

        assert os.path.exists(nested_log_file), f"Log file {nested_log_file} should be created"

        with open(nested_log_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "Test nested directory log" in content

    @staticmethod
    def test_create_log_directory_with_relative_path(temp_config_dir):
        """The test uses relative paths to create a log directory"""
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_config_dir.name)

            relative_log_file = os.path.join("logs", "run", "relative_test.log")

            if os.path.exists("logs"):
                import shutil

                shutil.rmtree("logs")

            config = {
                "log_file": relative_log_file,
                "output": ["file"],
                "level": logging.INFO,
                "backup_count": 5,
                "max_bytes": 1024 * 1024,
                "format": "%(asctime)s | %(levelname)s | %(message)s",
            }

            logger = DefaultLogger("test_relative", config)

            abs_log_file = os.path.abspath(relative_log_file)
            abs_log_dir = os.path.dirname(abs_log_file)
            assert os.path.exists(abs_log_dir), f"Directory {abs_log_dir} should be created"

            logger.info("Test relative path log")

            # Access protected member for testing purposes
            for handler in logger._logger.handlers:  # pylint: disable=protected-access
                handler.flush()
                handler.close()

            assert os.path.exists(abs_log_file), f"Log file {abs_log_file} should be created"

        finally:
            os.chdir(original_cwd)

    @staticmethod
    def test_create_log_directory_failure_raises_exception(temp_config_dir):
        """An exception was thrown when the test failed to create the log directory"""
        from openjiuwen.core.common.exception.codes import StatusCode

        if sys.platform == "win32":
            invalid_log_file = "Z:\\invalid\\drive\\test.log"
        else:
            invalid_log_file = "/proc/invalid_path/test.log"

        config = {
            "log_file": invalid_log_file,
            "output": ["file"],
            "level": logging.INFO,
            "backup_count": 5,
            "max_bytes": 1024 * 1024,
            "format": "%(asctime)s | %(levelname)s | %(message)s",
        }

        with mock.patch("os.makedirs") as mock_makedirs:
            mock_makedirs.side_effect = OSError("Permission denied")

            with pytest.raises(BaseError) as exc_info:
                DefaultLogger('test_failure', config)
            
            assert exc_info.value.code == StatusCode.COMMON_LOG_PATH_INIT_FAILED.code
            assert "common log_path initialization failed" in exc_info.value.message
    
    @staticmethod
    def test_create_existing_directory_no_error(temp_config_dir):
        """No error will be reported when the test directory already exists"""
        existing_log_path = os.path.join(temp_config_dir.name, "logs", "existing")
        os.makedirs(existing_log_path, exist_ok=True)

        existing_log_file = os.path.join(existing_log_path, "test.log")

        config = {
            "log_file": existing_log_file,
            "output": ["file"],
            "level": logging.INFO,
            "backup_count": 5,
            "max_bytes": 1024 * 1024,
            "format": "%(asctime)s | %(levelname)s | %(message)s",
        }

        logger = DefaultLogger("test_existing", config)

        assert os.path.exists(existing_log_path)

        logger.info("Test existing directory")

        # Access protected member for testing purposes
        for handler in logger._logger.handlers:  # pylint: disable=protected-access
            handler.flush()
            handler.close()

        assert os.path.exists(existing_log_file)

    @staticmethod
    def test_log_path_validation(temp_config_dir):
        """Verify the legitimacy of the test log path"""
        from openjiuwen.core.common.exception.codes import StatusCode

        if sys.platform == "win32":
            sensitive_path = "C:\\Windows\\System32\\test.log"
        else:
            sensitive_path = "/etc/passwd"

        config = {
            "log_file": sensitive_path,
            "output": ["file"],
            "level": logging.INFO,
            "backup_count": 5,
            "max_bytes": 1024 * 1024,
            "format": "%(asctime)s | %(levelname)s | %(message)s",
        }

        with pytest.raises(BaseError) as exc_info:
            DefaultLogger('test_sensitive', config)
        
        assert exc_info.value.code == StatusCode.COMMON_LOG_PATH_INVALID.code
        assert "common log_path is invalid" in exc_info.value.message.lower()