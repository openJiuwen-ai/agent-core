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
