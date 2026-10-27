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

## Default backend output and lifecycle

`DefaultLogger` sends its built-in console and file output through an in-process
`QueueHandler` / `QueueListener`. Message construction, context capture (session/trace,
member and request) and necessary filters still run on the caller. Final formatting,
writes, flushes and rotation run on the consumer. Returning from a log call does not
mean delivery has finished. `error(..., exc_info=True)` retains its traceback for
consumer-side formatting without queue preparation modifying sibling handlers' records.

Each named logger has a **4096-record queue**, plus at most one record in flight.
This absorbs short bursts (about four seconds at 1000 records/second), not sustained
backpressure. A full or closed queue drops new records at **every level**, including
ERROR and CRITICAL. It never waits for queue space or falls back to synchronous output.
The record count is bounded; message sizes and objects retained by tracebacks are not.

Additional `DefaultLogger` methods, not requirements for other `LoggerProtocol` backends:

- `flush(timeout: float = 2.0) -> bool` waits up to the timeout for previously accepted
  records. This explicit barrier blocks; asyncio callers can use
  `await asyncio.to_thread(logger.flush)`. True means processed, including shutdown
  discards, and does not guarantee that every destination succeeded.
- `get_queue_stats() -> dict` reports `capacity`, `queued`, `peak`, `accepted`,
  `completed`, `dropped_by_level`, `discarded_on_close` and `output_failures`.
  Completed includes shutdown discards. Failures count failed sink operations.
  Drop keys are standard integer levels; custom levels share key `0` to keep counter
  cardinality bounded. Counters replace overflow log messages to avoid recursion.

`close()` seals ingress and drains for at most two seconds, then discards the pending
tail. An in-flight OS write cannot safely be interrupted: its daemon closes the owned
handlers and exits when the write returns. Normal interpreter exit shares a two-second
drain deadline across default loggers; abnormal termination may lose the tail.
Repeated setup/reconfiguration replaces built-in output only. It waits up to two seconds
for old output to become idle, with ingress still open. If that wait times out (including
under continuous production), replacement is rejected: the old configuration, consumer
and queued records remain active. Normal capacity/drop limits still apply. Invalid
configuration or a replacement consumer startup failure also leaves old output active.
Once committed, producers already holding the old handler are forwarded to the new queue.
Old sink cleanup gets a separate bounded two-second wait; slow final flush/close does not
undo a successful swap. Further replacement is rejected until that retiring consumer
exits, bounding consumers to one active plus at most one retiring per logger. An explicitly
closed logger stays closed if replacement is rejected. Closing a stale wrapper never
closes its successor.

Plain file/pipe stdout uses an independently owned, unbuffered descriptor, preserving
encoding/error handling and complete writes without holding the global stdout buffer
lock needed at interpreter exit. Custom or transforming streams and native Windows
console streams retain their write/flush contract and remain responsible for their own
blocking and shutdown behavior.

Compatibility boundaries:

- Custom handlers, logger filters and `logger()` retain stdlib behavior. Custom handlers
  remain synchronous and caller-owned; reconfiguration does not close them.
- Propagation follows configuration. Root/ancestor handlers can still block or duplicate
  built-in output. The SDK neither disables propagation nor takes ownership of external
  handlers; applications must configure their queuing separately.
- Initialization, path validation, file opening and reconfiguration can still block.
  Schedule explicit drain/close/reconfiguration during lifecycle management or on workers.
- Forking after initialization inherits a queue without its consumer and is unsupported.
  Use a fresh interpreter (for example `spawn`), or initialize logging only in the child.
  Reset/reconfigure after fork cannot reliably repair inherited thread locks.
