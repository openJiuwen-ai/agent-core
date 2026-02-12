# openjiuwen.core.common.exception.exception

## class openjiuwen.core.common.exception.exception.JiuWenBaseException

```python
openjiuwen.core.common.exception.exception.JiuWenBaseException(error_code: int, message: str)
```

openJiuwen框架定义的异常类，继承自Python内置Exception基类。

**参数**：

* **error_code**(int)：表示异常的错误码。
* **message**(str)：表示异常中的错误信息。

### error_code

```python
error_code
```

获取openJiuwen异常对象的错误码。

**返回**：

**int**，openJiuwen异常对象的错误码。

### message

```python
message
```

获取openJiuwen异常对象的错误信息。

**返回**：

**str**，openJiuwen异常对象的错误信息。