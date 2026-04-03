# openjiuwen.core.foundation.tool.form_handler

The `openjiuwen.core.foundation.tool.form_handler` module provides form data processing mechanisms, supporting multiple form types (such as file forms), and implementing flexible form processing extensions through the strategy pattern.

## class FormHandler

```python
class FormHandler(ABC)
```

Abstract base class for form handlers, all concrete form handlers should inherit from this class.

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

Abstract method that executes specific form data processing logic.

**Parameters**:

* **form**(aiohttp.FormData): Form object to add data to.
* **form_data**(Dict[str, any]): Data dictionary to add to the form, where keys are parameter names and values can be of any type.
* **\*\*kwargs**: Other parameters.

**Returns**:

**aiohttp.FormData**, the processed form data object.

## class DefaultFormHandler

```python
class DefaultFormHandler(FormHandler)
```

Generic form handler for processing simple key-value form data, converting values to strings and adding them to the form.

### handle

```python
async def handle(
    self,
    form: aiohttp.FormData,
    form_data: Dict[str, any],
    **kwargs
) -> aiohttp.FormData
```

Executes generic form processing logic, adding key-value pair data to the form.

**Parameters**:

* **form**(aiohttp.FormData): Form object to add data to.
* **form_data**(Dict[str, any]): Data dictionary to add to the form, where keys are parameter names and values can be of any type.
* **\*\*kwargs**: Other parameters.

**Returns**:

**aiohttp.FormData**, form data object containing key-value pairs.


## class FormHandlerManager

```python
class FormHandlerManager
```

Form handler manager, using singleton pattern, for managing and retrieving various form handlers.

### register

```python
def register(self, handler_type_value: str, handler_class: Type[FormHandler])
```

Registers a form handler.

**Parameters**:

* **handler_type_value**(str): Handler type identifier.
* **handler_class**(Type[FormHandler]): Handler class, must inherit from `FormHandler`.

### register_default_handler

```python
def register_default_handler(self, handler_class: Type[FormHandler])
```

Registers the default form handler.

**Parameters**:

* **handler_class**(Type[FormHandler]): Default handler class, must inherit from `FormHandler`.

### get_handler

```python
def get_handler(self, handler_type: str) -> FormHandler
```

Gets a registered form handler.

**Parameters**:

* **handler_type**(str): Handler type identifier.

**Returns**:

**FormHandler**, the corresponding form handler class. If not found, returns the default handler.

## Usage Examples

### 1. Using Default Handler to Process Simple Forms

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandlerManager
import aiohttp

# Get form handler manager instance
handler_manager = FormHandlerManager()

# Get default handler
handler_class = handler_manager.get_handler("default")

# Prepare form data
form_data = {
    "username": "test_user",
    "age": 25,
    "active": True
}

# Create form object
form = aiohttp.FormData()

# Execute form processing
form = await handler_class().handle(form=form, form_data=form_data)

# Use processed form data
async with aiohttp.ClientSession() as session:
    async with session.post("https://api.example.com/submit", data=form) as response:
        result = await response.json()
```

### 2. Registering Custom Form Handler

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandler, FormHandlerManager
import aiohttp

# Create custom form handler
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

# Get form handler manager instance
handler_manager = FormHandlerManager()

# Register custom handler
handler_manager.register("json", JsonFormHandler)

# Use custom handler
handler_class = handler_manager.get_handler("json")
form = aiohttp.FormData()
form = await handler_class().handle(form=form, form_data={"config": {"key": "value"}})
```

### 3. Registering Default Handler

```python
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandler, FormHandlerManager

# Create custom default handler
class CustomDefaultHandler(FormHandler):

    async def handle(
        self,
        form: aiohttp.FormData,
        form_data: Dict[str, any],
        **kwargs
    ) -> aiohttp.FormData:
        # Custom processing logic
        pass

# Get form handler manager instance
handler_manager = FormHandlerManager()

# Register as default handler
handler_manager.register_default_handler(CustomDefaultHandler)
```
