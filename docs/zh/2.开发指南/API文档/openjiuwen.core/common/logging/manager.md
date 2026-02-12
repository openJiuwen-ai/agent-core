# openjiuwen.core.common.logging

## class openjiuwen.core.common.logging.manager.LogManager

```python
class openjiuwen.core.common.logging.manager.LogManager
```

`LogManager`类统一管理所有日志器，提供了日志器的注册与获取功能。

### register_logger

```python
classmethod register_logger(cls, log_type: str, logger: LoggerProtocol) -> None
```

注册日志器。

**参数**：

* **log_type**(str)：日志器id。
* **logger**([LoggerProtocol](./protocol.md#class-openjiuwencorecommonloggingprotocolloggerprotocol))：日志器。

**样例**：

```python
>>> # 实现自定义CustomLogger，并使用LogManager的register_logger接口注册管理。
>>> from openjiuwen.core.common.logging import LogManager
>>> from openjiuwen.core.common.logging.protocol import LoggerProtocol
>>> import logging
>>>
>>> class CustomLogger(LoggerProtocol):
...     """自定义日志器实现"""
...
...     def __init__(self, log_type: str, config: dict):
...         self.log_type = log_type
...         self.config = config
...         self._logger = logging.getLogger(f"custom_{log_type}")
...         self._setup_logger()
...
...     def _setup_logger(self):
...         """设置日志器"""
...         level = self.config.get('level', logging.INFO)
...         self._logger.setLevel(level)
...
...         # 添加自定义格式
...         formatter = logging.Formatter(
...             '%(asctime)s | [CUSTOM] | %(levelname)s | %(message)s'
...         )
...
...         handler = logging.StreamHandler()
...         handler.setFormatter(formatter)
...         self._logger.addHandler(handler)
...
...     # 实现 LoggerProtocol 接口的所有方法
...     def debug(self, msg: str, *args, **kwargs) -> None:
...         self._logger.debug(msg, *args, **kwargs)
...
...     def info(self, msg: str, *args, **kwargs) -> None:
...         self._logger.info(msg, *args, **kwargs)
...
...     def warning(self, msg: str, *args, **kwargs) -> None:
...         self._logger.warning(msg, *args, **kwargs)
...
...     def error(self, msg: str, *args, **kwargs) -> None:
...         self._logger.error(msg, *args, **kwargs)
...
...     def critical(self, msg: str, *args, **kwargs) -> None:
...         self._logger.critical(msg, *args, **kwargs)
...
...     def exception(self, msg: str, *args, **kwargs) -> None:
...         self._logger.exception(msg, *args, **kwargs)
...
...     def log(self, level: int, msg: str, *args, **kwargs) -> None:
...         self._logger.log(level, msg, *args, **kwargs)
...
...     def setLevel(self, level: int) -> None:
...         self._logger.setLevel(level)
... 
>>> # 创建自定义日志器
>>> custom_config = {
...     'level': logging.INFO,
...     'format': 'custom_format'
... }
... 
>>> custom_logger = CustomLogger('my_app', custom_config)
>>> 
>>> # 注册到 LogManager
>>> LogManager.register_logger('my_app', custom_logger)
```

### get_logger

```python
classmethod get_logger(cls, log_type: str) -> LoggerProtocol
```

获取日志器。

**参数**：

* **log_type**(str)：日志器id。

**返回**：

**[LoggerProtocol](./protocol.md#class-openjiuwencorecommonloggingprotocolloggerprotocol)**，日志器。

**样例**：

```python
>>> from openjiuwen.core.common.logging import LogManager
>>> 
>>> # 使用日志器
>>> logger = LogManager.get_logger('my_app')
>>> logger.info("这是自定义日志器的消息")
2025-09-29 03:07:04,789 | [CUSTOM] | INFO | 这是自定义日志器的消息
```

### set_default_logger_class

```python
classmethod set_default_logger_class(cls, logger_class: Type[LoggerProtocol]) -> None
```

设置默认日志器类。

**参数**：

* **logger_class**(Type[LoggerProtocol])：默认日志器类，必须实现`LoggerProtocol`协议。

**返回**：

无返回值。

### initialize

```python
classmethod initialize(cls) -> None
```

初始化日志管理器。在异步环境中，通常只在应用启动时调用一次。多次调用时，已初始化的部分会被跳过（幂等操作）。

**返回**：

无返回值。

**异常**：

* **RuntimeError**：如果`LogConfig`不可用时抛出。

### get_all_loggers

```python
classmethod get_all_loggers(cls) -> Dict[str, LoggerProtocol]
```

获取所有已注册的日志器。

**返回**：

`Dict[str, LoggerProtocol]`，所有日志器的副本字典，键为日志器id，值为日志器实例。

### reset

```python
classmethod reset(cls) -> None
```

重置日志管理器。清除所有日志器和初始化状态。主要用于测试场景。

**返回**：

无返回值。
