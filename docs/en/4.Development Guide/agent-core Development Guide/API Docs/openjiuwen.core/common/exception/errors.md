# openjiuwen.core.common.exception.errors

## class openjiuwen.core.common.exception.errors.BaseError

```python
class openjiuwen.core.common.exception.errors.BaseError(status: StatusCode, *, msg: Optional[str] = None, details: Optional[Any] =
  None, cause: Optional[BaseException] = None, **kwargs: dict[str, Any])
```

Unified exception base class for the openJiuwen framework. All exceptions within the framework should inherit from this class.

**Parameters**:

* **status** (StatusCode): Status code used to identify the specific type of exception and error message template. Required positional parameter.
* **msg** (Optional[str]): Custom error message. If provided, will override the default message rendered from the StatusCode template. Default value: None.
* **details** (Optional[Any]): Structured context information. Can be data of any type, used to provide additional error details. Default value: None.
* **cause** (Optional[BaseException]): Chained exception. Used to record the original exception that caused the current exception. Default value: None.
* ****kwargs** (dict[str, Any]): Template parameters. Used to fill placeholders in the StatusCode error message template.

### to_dict -> Dict[str, Any]

Convert the exception to dictionary format for API responses, RPC calls, or logging.

**Returns**:

**Dict[str, Any]**, a dictionary containing the following keys:
- `code`: Error code (integer)
- `status`: Status code name (string)
- `message`: Message rendered from template (string)
- `params`: Template parameters (dictionary)
- `raw_message`: Custom message or rendered message (string)
- `details`: Detailed information (any type)

### to_json -> str

Convert the exception to JSON string format.

**Returns**:

**str**, JSON string representation of the exception, using UTF-8 encoding (ensure_ascii=False).
