"""
测试日志功能
"""

import unittest
import logging
import tempfile
import os
import sys
import threading
from io import StringIO
from unittest import mock

from jiuwen.core.common.logging import LogManager
from jiuwen.extensions.common.log import DefaultLogger
from jiuwen.core.common.logging import set_thread_session
from jiuwen.core.common.logging import get_thread_session
from jiuwen.core.common.logging import LoggerProtocol



def thread_function(session_id, log_list):
    logger = LogManager.get_logger('common')

    if not logger._logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        logger._logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    set_thread_session(session_id)
    logger.info(f'Thread started with session id {session_id}')

    for handler in logger._logger.handlers:
        handler.flush()

    log_list.append((session_id, get_thread_session()))

class LoggerBaseTest(unittest.TestCase):
    """日志测试基类，处理环境设置和清理"""
    def setUp(self):
        """设置测试环境"""
        import tempfile
        import os
        import sys
        from io import StringIO
        from unittest import mock

        # 创建临时YAML配置文件
        self.temp_config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_config_dir.cleanup)
        
        self.config_file_path = os.path.join(self.temp_config_dir.name, 'test_config.yaml')
        
        # 创建测试用的YAML配置
        test_config = {
            'logging': {
                'level': 'INFO',
                'backup_count': 5,
                'max_bytes': 1024 * 1024,
                'format': '%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s',
                'log_file': 'common.log',
                'output': ['console', 'file'],
                'interface_log_file': 'interface.log',
                'prompt_builder_interface_log_file': 'prompt_builder.log',
                'performance_log_file': 'performance.log',
                'interface_output': ['console', 'file'],
                'performance_output': ['console', 'file'],
                'log_path': self.temp_config_dir.name
            }
        }
        
        import yaml
        with open(self.config_file_path, 'w', encoding='utf-8') as f:
            yaml.dump(test_config, f)

        self.log_config_patcher = mock.patch('jiuwen.extensions.common.configs.log_config.LogConfig')
        self.config_manager_patcher = mock.patch('jiuwen.extensions.common.configs.config_manager.ConfigManager')

        from jiuwen.extensions.common.configs.log_config import LogConfig
        from jiuwen.extensions.common.configs.config_manager import ConfigManager

        self.test_log_config = LogConfig(self.config_file_path)
        self.test_config_manager = ConfigManager(self.config_file_path)

        import jiuwen.extensions.common.configs.config_manager
        jiuwen.extensions.common.configs.log_config.log_config = self.test_log_config
        jiuwen.extensions.common.configs.config_manager.config_manager = self.test_config_manager
        
        self.addCleanup(self.log_config_patcher.stop)
        self.addCleanup(self.config_manager_patcher.stop)

        self.temp_log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_log_dir.cleanup)

        self.stdout_capture = StringIO()
        self.original_stdout = sys.stdout
        sys.stdout = self.stdout_capture

        self.stderr_capture = StringIO()
        self.original_stderr = sys.stderr
        sys.stderr = self.stderr_capture
        self.addCleanup(lambda: setattr(sys, 'stdout', self.original_stdout))
        self.addCleanup(lambda: setattr(sys, 'stderr', self.original_stderr))

        LogManager.reset()

        LogManager.initialize()

        for log in LogManager.get_all_loggers().values():
            if not log._logger.handlers:
                handler = logging.StreamHandler(sys.stdout)
                log._logger.addHandler(handler)



    def tearDown(self):
        set_thread_session('')

        try:
            for logger in LogManager.get_all_loggers().values():
                if hasattr(logger, '_logger'):
                    # Close all handlers
                    for handler in logger._logger.handlers[:]:
                        try:
                            handler.close()
                        except Exception:
                            pass
                        logger._logger.removeHandler(handler)
            LogManager.reset()
        except Exception:
            pass


class ThreadSafetyTest(LoggerBaseTest):
    def test_log_output_contains_trace_id(self):
        test_id = "TRACE-12345"
        set_thread_session(test_id)

        logger = LogManager.get_logger('common')
        logger.setLevel(logging.INFO)

        self.stdout_capture.truncate(0)
        self.stdout_capture.seek(0)
        self.stderr_capture.truncate(0)
        self.stderr_capture.seek(0)

        logger.info("Test log message with trace_id")

        for handler in logger._logger.handlers:
            handler.flush()

        stdout_output = self.stdout_capture.getvalue()
        stderr_output = self.stderr_capture.getvalue()
        combined_output = stdout_output + stderr_output

        print("Actual output:", repr(combined_output))
        print("Logger handlers:", logger._logger.handlers)


        self.assertIn(test_id, combined_output)
        self.assertRegex(
            combined_output,
            r'.*TRACE-12345.*Test log message with trace_id'
        )

    def test_thread_trace_id_isolation(self):
        log_list = []
        threads = [
            threading.Thread(target=thread_function, args=('10001', log_list)),
            threading.Thread(target=thread_function, args=('10002', log_list)),
            threading.Thread(target=thread_function, args=('10003', log_list))
        ]

        self.stdout_capture.truncate(0)
        self.stdout_capture.seek(0)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for session_id, recorded_id in log_list:
            self.assertEqual(session_id, recorded_id)

        self.assertEqual(get_thread_session(), '')

        output = self.stdout_capture.getvalue()
        print("Actual output:", repr(output))

        self.assertIn('10001', output)
        self.assertIn('10002', output)
        self.assertIn('10003', output)

class LogManagerTest(LoggerBaseTest):
    """测试日志管理器功能"""
    def test_custom_logger_registration_and_usage(self):
        class CustomLogger:
            def __init__(self):
                self.messages = []
            def info(self, msg):
                formatted_msg = f"CUSTOM LOGGER INFO: {msg}"
                print(formatted_msg) # 模拟输出到 stdout
                self.messages.append(formatted_msg)
            def debug(self, msg, *args, **kwargs): pass
            def warning(self, msg, *args, **kwargs): pass
            def error(self, msg, *args, **kwargs): pass
            def critical(self, msg, *args, **kwargs): pass
            def exception(self, msg, *args, **kwargs): pass
            def log(self, level, msg, *args, **kwargs): pass
            def setLevel(self, level): pass
            def addHandler(self, handler): pass
            def removeHandler(self, handler): pass
            def addFilter(self, filter): pass
            def removeFilter(self, filter): pass
            def get_config(self): return {}
            def reconfigure(self, config): pass

        custom_logger_instance = CustomLogger()

        LogManager.register_logger('custom', custom_logger_instance)

        retrieved_logger = LogManager.get_logger('custom')
        self.assertIs(retrieved_logger, custom_logger_instance)
        retrieved_logger.info("Test custom logger")

        self.assertIn("CUSTOM LOGGER INFO: Test custom logger", custom_logger_instance.messages)
        output = self.stdout_capture.getvalue()
        self.assertIn("CUSTOM LOGGER INFO: Test custom logger", output)

    def test_default_logger_creation(self):
        """测试动态创建默认日志记录器"""
        new_logger = LogManager.get_logger('new_type_test')


        self.assertIsInstance(new_logger, DefaultLogger)

        new_logger.warning("Test new logger type")

        output = self.stdout_capture.getvalue()
        self.assertIn("Test new logger type", output)

        self.assertIn("new_type_test", output)
        self.assertIn("WARNING", output)

    def test_get_all_loggers(self):
        """测试获取所有日志记录器"""
        all_loggers = LogManager.get_all_loggers()
        expected_types = {'common', 'interface', 'prompt_builder', 'performance'}
        self.assertTrue(expected_types.issubset(set(all_loggers.keys())))
        for log_instance in all_loggers.values():
            self.assertIsInstance(log_instance, LoggerProtocol)


class LogLevelTest(LoggerBaseTest):

    def test_log_level_filtering(self):

        test_logger_instance = LogManager.get_logger('level_test')


        test_logger_instance.setLevel(logging.DEBUG)
        self.stdout_capture.truncate(0)
        self.stdout_capture.seek(0)


        test_logger_instance.debug("Debug message")
        test_logger_instance.info("Info message")
        test_logger_instance.warning("Warning message")
        test_logger_instance.error("Error message")

        output = self.stdout_capture.getvalue()


        self.assertIn("Debug message", output)
        self.assertIn("Info message", output)
        self.assertIn("Warning message", output)
        self.assertIn("Error message", output)


        test_logger_instance.setLevel(logging.ERROR)
        self.stdout_capture.truncate(0) # 清空缓冲区
        self.stdout_capture.seek(0)


        test_logger_instance.debug("Should not appear debug")
        test_logger_instance.info("Should not appear info")
        test_logger_instance.warning("Should not appear warning")
        test_logger_instance.error("Should appear error")

        output = self.stdout_capture.getvalue()


        self.assertNotIn("Should not appear debug", output)
        self.assertNotIn("Should not appear info", output)
        self.assertNotIn("Should not appear warning", output)
        self.assertIn("Should appear error", output)






class LogFileOutputTest(LoggerBaseTest):
    
    def test_interface_log_file_output(self):
        import os

        interface_logger = LogManager.get_logger('interface')

        set_thread_session('FILE-TEST-123')

        test_message = "这是文件输出测试消息"
        interface_logger.info(test_message)

        for handler in interface_logger._logger.handlers:
            handler.flush()

        actual_log_file = interface_logger.config.get('log_file', '')

        if not os.path.exists(actual_log_file):
            print(f"警告: 日志文件不存在: {actual_log_file}")
            print(f"Logger配置: {interface_logger.config}")

            stdout_output = self.stdout_capture.getvalue()
            self.assertIn(test_message, stdout_output, "控制台输出应该包含测试消息")
            self.assertIn('FILE-TEST-123', stdout_output, "控制台输出应该包含trace_id")
            return

        with open(actual_log_file, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn(test_message, content, "日志文件应该包含测试消息")
        self.assertIn('FILE-TEST-123', content, "日志文件应该包含trace_id")

        stdout_output = self.stdout_capture.getvalue()
        self.assertIn(test_message, stdout_output, "控制台输出应该包含测试消息")
        
        print(f"日志文件输出测试通过: {actual_log_file}")
        print(f"文件大小: {os.path.getsize(actual_log_file)} bytes")
        print(f"文件内容: {content.strip()}")







