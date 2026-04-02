# openjiuwen.harness.workspace

## enum openjiuwen.harness.workspace.WorkspaceNode

```python
class WorkspaceNode(str, Enum)
```

Named nodes in the workspace directory tree.

| Value | Description |
|---|---|
| `ROOT` | The workspace root directory. |
| `SRC` | Source code directory. |
| `DOCS` | Documentation directory. |
| `TESTS` | Test directory. |
| `DATA` | Data directory. |
| `OUTPUT` | Output / artifacts directory. |
| `TEMP` | Temporary files directory. |

---

## class openjiuwen.harness.workspace.Workspace

Manages the directory layout used by the agent for file operations.

**Attributes**:

- **root_path** (str): Absolute path to the workspace root directory.
- **directories** (dict[str, str]): Mapping of [WorkspaceNode](#enum-openjiuwenharnessworkspaceworkspacenode) names to their resolved paths.
- **language** (str): Language code for workspace-related prompts. Default: `"en"`.

### method get_directory

```python
get_directory(node: WorkspaceNode | str) -> str | None
```

Return the resolved path for the given workspace node.

**Parameters**:

- **node** ([WorkspaceNode](#enum-openjiuwenharnessworkspaceworkspacenode) | str): The node to look up.

**Returns**:

**str | None**: The directory path, or `None` if the node is not configured.

### method get_node_path

```python
get_node_path(node: WorkspaceNode | str, *parts: str) -> str
```

Join additional path segments onto a workspace node's directory.

**Parameters**:

- **node** ([WorkspaceNode](#enum-openjiuwenharnessworkspaceworkspacenode) | str): The base node.
- ***parts** (str): Additional path segments to append.

**Returns**:

**str**: The resolved path.

### method set_directory

```python
set_directory(node: WorkspaceNode | str, path: str) -> None
```

Set or override the directory for a workspace node.

**Parameters**:

- **node** ([WorkspaceNode](#enum-openjiuwenharnessworkspaceworkspacenode) | str): The node to set.
- **path** (str): The directory path.

### method get_default_directory

```python
get_default_directory() -> str
```

Return the default working directory (typically `root_path`).

**Returns**:

**str**: The default directory path.

---

## function openjiuwen.harness.workspace.get_workspace_schema

```python
get_workspace_schema() -> dict
```

Return the JSON Schema describing the `Workspace` configuration format, suitable for use in prompt assembly or validation.

**Returns**:

**dict**: A JSON Schema dictionary.
