# openjiuwen.auto_harness.tools

Auto Harness Agent toolset module, providing read-only tools such as experience search. Tools register metadata via `ToolMetadataProvider`, supporting bilingual descriptions, for agents to invoke on demand at runtime.

Submodules:
- `experience_search_tool`: Experience search tool and metadata provider

---

## class openjiuwen.auto_harness.tools.experience_search_tool.ExperienceSearchMetadataProvider

```python
class ExperienceSearchMetadataProvider(ToolMetadataProvider):
    """Metadata provider for ExperienceSearchTool."""
```

Metadata provider for `ExperienceSearchTool`, responsible for providing tool name, description, and input parameter schema. Supports bilingual (Chinese/English) descriptions.

### get_name() -> str

```python
def get_name(self) -> str
```

Return the tool name.

**Returns**: Always returns `"experience_search"`.

---

### get_description(language: str = 'cn') -> str

```python
def get_description(self, language: str = 'cn') -> str
```

Return the tool description text.

**Parameters**:
* **language**(`str`): Language code, supports `"cn"` and `"en"`, default `"cn"`.

**Returns**: Tool description in the corresponding language.

---

### get_input_params(language: str = 'cn') -> Dict[str, Any]

```python
def get_input_params(self, language: str = 'cn') -> Dict[str, Any]
```

Return the tool input parameter JSON Schema. Contains two parameters: `query` (required, search keyword) and `limit` (optional, maximum return count, default 5).

**Parameters**:
* **language**(`str`): Language code, supports `"cn"` and `"en"`, default `"cn"`.

**Returns**: A parameter dictionary conforming to the JSON Schema specification.

---

## class openjiuwen.auto_harness.tools.experience_search_tool.ExperienceSearchTool

```python
class ExperienceSearchTool(Tool):
    """Readonly experience search tool."""

    def __init__(
        self,
        experience_dir: str,
        agent_id: Optional[str] = None,
        language: str = 'cn',
    ) -> None
```

Read-only experience search tool. Creates an `ExperienceStore` instance on each invocation to perform keyword search, returning summaries of matching experience records.

**Parameters**:
* **experience_dir**(`str`): Experience storage directory path.
* **agent_id**(`Optional[str]`): Associated agent identifier, default `None`.
* **language**(`str`): Language code, default `"cn"`.

### invoke(inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput

```python
async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput
```

Execute the experience search. Extracts `query` and `limit` parameters from `inputs`, calls `ExperienceStore.search`, and returns the results.

**Parameters**:
* **inputs**(`Dict[str, Any]`): Input parameters, containing `query` (required) and `limit` (optional, default 5).
* **kwargs**(`Any`): Additional keyword arguments.

**Returns**: A `ToolOutput` instance; on success, `data` contains experience summaries (with `type`, `topic`, `summary`, `outcome` fields); on failure, contains error information.

---

### stream(inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]

```python
async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]
```

Execute the experience search in streaming mode. Internally calls `invoke` and yields the single result.

**Parameters**:
* **inputs**(`Dict[str, Any]`): Input parameters, same as `invoke`.
* **kwargs**(`Any`): Additional keyword arguments.

**Returns**: An async iterator, yielding `ToolOutput` results.
