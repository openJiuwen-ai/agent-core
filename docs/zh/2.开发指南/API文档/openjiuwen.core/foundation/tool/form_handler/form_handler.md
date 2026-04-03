# openjiuwen.core.foundation.tool.form_handler

`openjiuwen.core.foundation.tool.form_handler` 模块提供了表单数据处理机制，支持多种表单类型（如文件表单），并通过策略模式实现了灵活的表单处理扩展。

## class FormHandler

```python
class FormHandler(ABC)
```

表单处理器抽象基类，所有具体的表单处理器都应继承此类。

### handle

```python
@abstractmethod
async def handle(
    self,
    form: aiohttp.FormData,
    form_data: Dict[str, any],
    **kwargs
) -> aiohttp.FormData
```

抽象方法，执行具体的表单数据处理逻辑。

**参数**：

* **form**(aiohttp.FormData)：要添加数据的表单对象。
* **form_data**(Dict[str, any])：要添加到表单的数据字典，键为参数名，值为任意类型的值。
* **\*\*kwargs**：其他参数。

**返回**：

**aiohttp.FormData**，处理后的表单数据对象。

## class DefaultFormHandler

```python
class DefaultFormHandler(FormHandler)
```

通用表单处理器，用于处理简单的键值对表单数据，将值转换为字符串并添加到表单中。

### handle

```python
async def handle(
    self,
    form: aiohttp.FormData,
    form_data: Dict[str, any],
    **kwargs
) -> aiohttp.FormData
```

执行通用表单处理逻辑，将键值对数据添加到表单中。

**参数**：

* **form**(aiohttp.FormData)：要添加数据的表单对象。
* **form_data**(Dict[str, any])：要添加到表单的数据字典，键为参数名，值为任意类型的值。
* **\*\*kwargs**：其他参数。

**返回**：

**aiohttp.FormData**，包含键值对的表单数据对象，与上面的form是同一个对象。


## class FormHandlerManager

```python
class FormHandlerManager
```

表单处理器管理器，采用单例模式，用于管理和获取各种表单处理器。

### register

```python
def register(self, handler_type_value: str, handler_class: Type[FormHandler])
```

注册表单处理器。

**参数**：

* **handler_type_value**(str)：处理器类型标识符。
* **handler_class**(Type[FormHandler])：处理器类，必须继承自 `FormHandler`。

### register_default_handler

```python
def register_default_handler(self, handler_class: Type[FormHandler])
```

注册默认表单处理器。

**参数**：

* **handler_class**(Type[FormHandler])：默认处理器类，必须继承自 `FormHandler`。

### get_handler

```python
def get_handler(self, handler_type: str) -> FormHandler
```

获取注册的表单处理器。

**参数**：

* **handler_type**(str)：处理器类型标识符。

**返回**：

**FormHandler**，对应的表单处理器类。如果未找到，返回默认处理器。

## 使用示例

### 1. 使用默认处理器处理简单表单

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandlerManager
import aiohttp

# 获取表单处理器管理器实例
handler_manager = FormHandlerManager()

# 获取默认处理器
handler_class = handler_manager.get_handler("default")

# 准备表单数据
form_data = {
    "username": "test_user",
    "age": 25,
    "active": True
}

# 创建表单对象
form = aiohttp.FormData()

# 执行表单处理
form = await handler_class().handle(form=form, form_data=form_data)

# 使用处理后的表单数据
async with aiohttp.ClientSession() as session:
    async with session.post("https://api.example.com/submit", data=form) as response:
        result = await response.json()
```

### 2. 注册自定义表单处理器

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandler, FormHandlerManager
import aiohttp

# 创建自定义表单处理器
class JsonFormHandler(FormHandler):

    async def handle(
        self,
        form: aiohttp.FormData,
        form_data: Dict[str, any],
        **kwargs
    ) -> aiohttp.FormData:
        import json
        for param_name, data in form_data.items():
            if data is None:
                continue
            form.add_field(
                name=param_name,
                value=json.dumps(data),
                content_type="application/json"
            )
        
        return form

# 获取表单处理器管理器实例
handler_manager = FormHandlerManager()

# 注册自定义处理器
handler_manager.register("json", JsonFormHandler)

# 使用自定义处理器
handler_class = handler_manager.get_handler("json")
form = aiohttp.FormData()
form = await handler_class().handle(form=form, form_data={"config": {"key": "value"}})
```

### 3. 注册默认处理器

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandler, FormHandlerManager

# 创建自定义默认处理器
class CustomDefaultHandler(FormHandler):

    async def handle(
        self,
        form: aiohttp.FormData,
        form_data: Dict[str, any],
        **kwargs
    ) -> aiohttp.FormData:
        # 自定义处理逻辑
        pass

# 获取表单处理器管理器实例
handler_manager = FormHandlerManager()

# 注册为默认处理器
handler_manager.register_default_handler(CustomDefaultHandler)
```
