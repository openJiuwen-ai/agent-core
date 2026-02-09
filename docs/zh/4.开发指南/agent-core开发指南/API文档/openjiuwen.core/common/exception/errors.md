# openjiuwen.core.common.exception.errors

## class openjiuwen.core.common.exception.errors.BaseError

```python
class openjiuwen.core.common.exception.errors.BaseError(status: StatusCode, *, msg: Optional[str] = None, details: Optional[Any] =
  None, cause: Optional[BaseException] = None, **kwargs: dict[str, Any])
```

openJiuwen框架统一异常基类。所有框架内的异常都应继承自此类。

**参数**：

* **status** (StatusCode)：状态码，用于标识异常的具体类型和错误信息模板。必需的位置参数。
* **msg** (Optional[str])：自定义错误消息。如果提供，将覆盖从StatusCode模板渲染的默认消息。默认值为None。
* **details** (Optional[Any])：结构化上下文信息。可以是任意类型的数据，用于提供额外的错误详情。默认值为None。
* **cause** (Optional[BaseException])：链式异常。用于记录导致当前异常的原始异常。默认值为None。
* ****kwargs** (dict[str, Any])：模板参数。用于填充StatusCode的错误消息模板中的占位符。

### to_dict -> Dict[str, Any]

将异常转换为字典格式，用于API响应、RPC调用或日志记录。

**返回**：

**Dict[str, Any]**，包含以下键的字典：
- `code`: 错误码（整数）
- `status`: 状态码名称（字符串）
- `message`: 从模板渲染的消息（字符串）
- `params`: 模板参数（字典）
- `raw_message`: 自定义消息或渲染消息（字符串）
- `details`: 详细信息（任意类型）

### to_json -> str

将异常转换为JSON字符串格式。

**返回**：

**str**，异常的JSON字符串表示，使用UTF-8编码（ensure_ascii=False）。
