# openjiuwen.core.common.logging

## class openjiuwen.core.common.logging.manager.LogManager

```python
class openjiuwen.core.common.logging.manager.LogManager
```

The `LogManager` class centrally manages all loggers and provides functionality for registering and retrieving loggers.

### register_logger

```python
classmethod register_logger(cls, log_type: str, logger: LoggerProtocol) -> None
```

Registers a logger.

**Parameters**:

* **log_type** (str): The logger identifier.
* **logger** ([LoggerProtocol](./protocol.md#class-openjiuwencorecommonloggingprotocolloggerprotocol)): The logger instance.

**Example**:

```python
>>> # Implement a custom CustomLogger and register it using LogManager.register_logger
>>> from openjiuwen.core.common.logging import LogManager
>>> from openjiuwen.core.common.logging.protocol import LoggerProtocol
>>> import logging
>>>
>>> class CustomLogger(LoggerProtocol):
...     """Custom logger implementation"""
...
...     def __init__(self, log_type: str, config: dict):
...         self.log_type = log_type
...         self.config = config
...         self._logger = logging.getLogger(f"custom_{log_type}")
...         self._setup_logger()
...
...     def _setup_logger(self):
...         """Configure the logger"""
...         level = self.config.get('level', logging.INFO)
...         self._logger.setLevel(level)
...
...         # Add a custom format
...         formatter = logging.Formatter(
...             '%(asctime)s | [CUSTOM] | %(levelname)s | %(message)s'
...         )
...
...         handler = logging.StreamHandler()
...         handler.setFormatter(formatter)
...         self._logger.addHandler(handler)
...
...     # Implement all methods required by LoggerProtocol
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
>>> # Create a custom logger
>>> custom_config = {
...     'level': logging.INFO,
...     'format': 'custom_format'
... }
...
>>> custom_logger = CustomLogger('my_app', custom_config)
>>>
>>> # Register it with LogManager
>>> LogManager.register_logger('my_app', custom_logger)
```

### get_logger

```python
classmethod get_logger(cls, log_type: str) -> LoggerProtocol
```

Retrieves a logger.

**Parameters**:

* **log_type** (str): The logger identifier.

**Returns**:

* **[LoggerProtocol](./protocol.md#class-openjiuwencorecommonloggingprotocolloggerprotocol)**: The logger instance.

**Example**:

```python
>>> from openjiuwen.core.common.logging import LogManager
>>>
>>> # Use the logger
>>> logger = LogManager.get_logger('my_app')
>>> logger.info("This is a message from the custom logger")
2025-09-29 03:07:04,789 | [CUSTOM] | INFO | This is a message from the custom logger
```

### set_default_logger_class

```python
classmethod set_default_logger_class(cls, logger_class: Type[LoggerProtocol]) -> None
```

Sets the default logger class.

**Parameters**:

* **logger_class** (Type[LoggerProtocol]): Default logger class, must implement the `LoggerProtocol` protocol.

**Returns**:

No return value.

### initialize

```python
classmethod initialize(cls) -> None
```

Initializes the log manager. In async environments, typically called only once at application startup. When called multiple times, already initialized parts are skipped (idempotent operation).

**Returns**:

No return value.

**Exceptions**:

* **RuntimeError**: Raised if `LogConfig` is unavailable.

### get_all_loggers

```python
classmethod get_all_loggers(cls) -> Dict[str, LoggerProtocol]
```

Retrieves all registered loggers.

**Returns**:

`Dict[str, LoggerProtocol]`, a copy dictionary of all loggers, with logger IDs as keys and logger instances as values.

### reset

```python
classmethod reset(cls) -> None
```

Resets the log manager. Clears all loggers and initialization state. Mainly used for testing scenarios.

**Returns**:

No return value.