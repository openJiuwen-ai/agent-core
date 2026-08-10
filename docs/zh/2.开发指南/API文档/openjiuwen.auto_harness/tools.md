# openjiuwen.auto_harness.tools

Auto Harness Agent 工具集模块，提供经验搜索等只读工具。工具通过 `ToolMetadataProvider` 注册元数据，支持中英文描述，供 Agent 在运行时按需调用。

子模块：
- `experience_search_tool`：经验搜索工具及元数据提供者

---

## class openjiuwen.auto_harness.tools.experience_search_tool.ExperienceSearchMetadataProvider

```python
class ExperienceSearchMetadataProvider(ToolMetadataProvider):
    """Metadata provider for ExperienceSearchTool."""
```

`ExperienceSearchTool` 的元数据提供者，负责提供工具名称、描述和输入参数 schema。支持中英文双语描述。

### get_name() -> str

```python
def get_name(self) -> str
```

返回工具名称。

**返回**：固定返回 `"experience_search"`。

---

### get_description(language: str = 'cn') -> str

```python
def get_description(self, language: str = 'cn') -> str
```

返回工具描述文本。

**参数**：
* **language**(`str`)：语言代码，支持 `"cn"` 和 `"en"`，默认 `"cn"`。

**返回**：对应语言的工具描述。

---

### get_input_params(language: str = 'cn') -> Dict[str, Any]

```python
def get_input_params(self, language: str = 'cn') -> Dict[str, Any]
```

返回工具输入参数的 JSON Schema。包含 `query`（必填，搜索关键词）和 `limit`（可选，最大返回条数，默认 5）两个参数。

**参数**：
* **language**(`str`)：语言代码，支持 `"cn"` 和 `"en"`，默认 `"cn"`。

**返回**：符合 JSON Schema 规范的参数字典。

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

只读经验搜索工具。每次调用时创建 `ExperienceStore` 实例执行关键词搜索，返回匹配的经验记录摘要。

**参数**：
* **experience_dir**(`str`)：经验存储目录路径。
* **agent_id**(`Optional[str]`)：关联的 Agent 标识，默认 `None`。
* **language**(`str`)：语言代码，默认 `"cn"`。

### invoke(inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput

```python
async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput
```

执行经验搜索。从 `inputs` 中提取 `query` 和 `limit` 参数，调用 `ExperienceStore.search` 并返回结果。

**参数**：
* **inputs**(`Dict[str, Any]`)：输入参数，包含 `query`（必填）和 `limit`（可选，默认 5）。
* **kwargs**(`Any`)：额外关键字参数。

**返回**：`ToolOutput` 实例，成功时 `data` 为经验摘要列表（含 `type`、`topic`、`summary`、`outcome` 字段），失败时包含错误信息。

---

### stream(inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]

```python
async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]
```

流式执行经验搜索。内部调用 `invoke` 并 yield 单次结果。

**参数**：
* **inputs**(`Dict[str, Any]`)：输入参数，与 `invoke` 相同。
* **kwargs**(`Any`)：额外关键字参数。

**返回**：异步迭代器，yield `ToolOutput` 结果。
