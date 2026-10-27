# openjiuwen.core.common.logging

## class openjiuwen.core.common.logging.protocol.LoggerProtocol

```
class openjiuwen.core.common.logging.protocol.LoggerProtocol
```

日志器协议接口。开发者实现自定义日志器时，需要实现`LoggerProtocol`协议中定义的所有方法。

### debug(msg: str, *args, **kwargs) -> None

记录调试级别的日志消息。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### info(msg: str, *args, **kwargs) -> None

记录信息级别的日志消息。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### warning(msg: str, *args, **kwargs) -> None

记录警告级别的日志消息。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### error(msg: str, *args, **kwargs) -> None

记录错误级别的日志消息。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### critical(msg: str, *args, **kwargs) -> None

记录严重错误级别的日志消息。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### exception(msg: str, *args, **kwargs) -> None

记录异常信息，包含堆栈跟踪。

**参数：**

* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### log(level: int, msg: str, *args, **kwargs) -> None

通用日志记录方法，可指定日志级别。

**参数：**

* **level**(int)：日志级别（如 logging.DEBUG, logging.INFO 等）
* **msg**(str)：日志消息内容
* **\*args**：可变位置参数，用于格式化消息
* **\*\*kwargs**：可变关键字参数，用于传递额外信息

**返回：**

无返回值。

### set_level(level: int) -> None

设置日志级别。

**参数：**

* **level**(int)：日志级别（如 logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL）

**返回：**

无返回值。

### add_handler(handler: logging.Handler) -> None

添加日志处理器。

**参数：**

* **handler**(logging.Handler)：要添加的日志处理器

**返回：**

无返回值。

### remove_handler(handler: logging.Handler) -> None

移除日志处理器。

**参数：**

* **handler**(logging.Handler)：要移除的日志处理器

**返回：**

无返回值。

### add_filter(filter) -> None

添加日志过滤器。

**参数：**

* **filter**：要添加的日志过滤器

**返回：**

无返回值。

### remove_filter(filter) -> None

移除日志过滤器。

**参数：**

* **filter**：要移除的日志过滤器

**返回：**

无返回值。

### get_config() -> Dict[str, Any]

获取日志器配置信息。

**返回：**

`Dict[str, Any]`，日志器的配置字典。

### reconfigure(config: Dict[str, Any]) -> None

重新配置日志器。

**参数：**

* **config**(Dict[str, Any])：新的配置字典

**返回：**

无返回值。

### logger()

返回内部日志器对象。

**返回：**

内部日志器对象。

## 默认 backend 的异步输出与生命周期

`DefaultLogger` 的内置控制台和文件输出使用进程内 `QueueHandler` / `QueueListener`。
调用线程仍生成消息、捕获 session/trace、member、request 上下文并执行必要过滤；
消费者线程执行最终格式化、写入、flush 和文件轮转。返回日志调用不代表记录已经输出。
`error(..., exc_info=True)` 的 traceback 保留到消费者格式化，原始 `LogRecord` 不受队列准备操作修改。

每个命名 logger 的队列固定容纳 **4096 条记录**，另有最多一条正在输出。
这个容量用于吸收短时突发，例如每秒 1000 条日志约四秒的积压，不用于长期存储。
队列满或已关闭时，DEBUG、INFO、WARNING、ERROR、CRITICAL **统一丢弃新记录**，
不会等待空位，也不会回退到调用线程同步输出。错误级日志同样可能丢失。
记录数量有界不等于字节数有界：大消息和异常 traceback 引用的对象仍可能占用较多内存。

以下附加方法属于 `DefaultLogger`，不要求其他 `LoggerProtocol` 实现提供：

- `flush(timeout: float = 2.0) -> bool`：最多等待指定秒数，确认此前接受的记录已处理。
  这是显式阻塞操作，asyncio 调用者可使用 `await asyncio.to_thread(logger.flush)`。
  返回 `True` 不表示每个输出端均成功，关闭时丢弃的记录也算处理完成。
- `get_queue_stats() -> dict`：返回 `capacity`、`queued`、`peak`、`accepted`、`completed`、
  `dropped_by_level`、`discarded_on_close`、`output_failures`。
  `completed` 包含关闭时丢弃的记录；`output_failures` 按输出端失败次数统计。
  丢弃计数按标准等级整数分类，非标准等级合并到键 `0`，避免计数表无限增长。
  不向已过载的日志路径发送溢出警告；应用可以读取这些计数用于监控。

`close()` 关闭入口并尽力排空，最多等待两秒；超时丢弃尚未开始输出的尾部。
正在执行的系统写入不能被安全强杀，后台线程在写入恢复后关闭它持有的 handler 并退出。
解释器正常退出使用所有默认 logger 共享的两秒排空预算；异常终止可能丢失队列尾部。
重复创建同名 logger 或 `reconfigure()` 会替换旧的内置输出；旧写入仍阻塞时拒绝替换，
避免累积后台线程。无效配置在关闭旧输出之前检查。旧实例的 `close()` 不关闭其后继实例的输出。

对于标准文件／管道 stdout，消费者使用独立描述符和无缓冲输出，避免占住解释器退出时
需要的全局 stdout 缓冲锁，同时保留编码、编码错误处理和写全语义。
自定义流、带转换逻辑的流以及 Windows 原生控制台保持原有 `write` / `flush` 行为；
这些流本身的阻塞和退出安全由其实现负责。

兼容范围和使用边界：

- `add_handler` / `remove_handler`、logger filter 和 `logger()` 仍操作原标准库 logger；
  自定义 handler 保留同步语义，由调用者管理关闭，重配置不会关闭它们。
- `propagate` 仍按配置执行。root/父 logger 的 handler 可能同步输出或与内置输出重复；
  SDK 不静默关闭传播，也不接管外部 handler，需由应用分别配置其排队策略。
- 初始化、路径校验、打开文件及重配置仍可能同步等待；本次隔离的是初始化后的内置输出。
  显式排空、关闭及重配置应安排在应用生命周期阶段或工作线程。
- 初始化日志后再 `fork` 不会复制消费者线程，因此不支持继承已初始化的默认日志队列。
  使用启动新解释器的方式（如 `spawn`），或在 fork 前不初始化日志、由子进程首次初始化。
  不要依赖 fork 后调用 reset/reconfigure 修复继承的线程锁。
