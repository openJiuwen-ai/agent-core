# openjiuwen.core.common.logging

## class openjiuwen.core.common.logging.protocol.LoggerProtocol

```
class openjiuwen.core.common.logging.protocol.LoggerProtocol
```

Logger protocol interface. When developers implement custom loggers, they need to implement all methods defined in the `LoggerProtocol` protocol.

### debug(msg: str, *args, **kwargs) -> None

Log a debug-level message.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### info(msg: str, *args, **kwargs) -> None

Log an info-level message.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### warning(msg: str, *args, **kwargs) -> None

Log a warning-level message.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### error(msg: str, *args, **kwargs) -> None

Log an error-level message.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### critical(msg: str, *args, **kwargs) -> None

Log a critical-level message.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### exception(msg: str, *args, **kwargs) -> None

Log exception information, including stack trace.

**Parameters**:

* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### log(level: int, msg: str, *args, **kwargs) -> None

Generic logging method that allows specifying the log level.

**Parameters**:

* **level** (int): Log level (e.g., logging.DEBUG, logging.INFO, etc.)
* **msg** (str): Log message content
* **\*args**: Variable positional arguments for message formatting
* **\*\*kwargs**: Variable keyword arguments for passing additional information

**Returns**:

No return value.

### set_level(level: int) -> None

Set the log level.

**Parameters**:

* **level** (int): Log level (e.g., logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)

**Returns**:

No return value.

### add_handler(handler: logging.Handler) -> None

Add a log handler.

**Parameters**:

* **handler** (logging.Handler): The log handler to add

**Returns**:

No return value.

### remove_handler(handler: logging.Handler) -> None

Remove a log handler.

**Parameters**:

* **handler** (logging.Handler): The log handler to remove

**Returns**:

No return value.

### add_filter(filter) -> None

Add a log filter.

**Parameters**:

* **filter**: The log filter to add

**Returns**:

No return value.

### remove_filter(filter) -> None

Remove a log filter.

**Parameters**:

* **filter**: The log filter to remove

**Returns**:

No return value.

### get_config() -> Dict[str, Any]

Get logger configuration information.

**Returns**:

`Dict[str, Any]`, the logger's configuration dictionary.

### reconfigure(config: Dict[str, Any]) -> None

Reconfigure the logger.

**Parameters**:

* **config** (Dict[str, Any]): New configuration dictionary

**Returns**:

No return value.

### logger()

Return the internal logger object.

**Returns**:

Internal logger object.
