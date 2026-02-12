# openjiuwen.core.workflow

The `openjiuwen.core.workflow.components.tool.tool_comp` module provides tool (plugin) components in workflows, used to bind and execute tools registered through [Runner](../../../runner/runner.md). Component gets [Tool](../../../foundation/tool.README.md) instance from resource manager according to configured `tool_id`, validates node input then passes to tool for execution, and encapsulates result as unified output format. Components are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig` for import. For more component information, see [components](../../components.README.md).

## class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponentConfig

```python
class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponentConfig()
```

Tool component configuration data class, inherits from [ComponentConfig](../components.md). Currently mainly used to specify tool id to bind, rest are reserved for extension.

* **tool_id** (str | None): Tool id to bind, corresponds to tool registered in [Runner](../../../runner/runner.md) resource manager. Required when constructing [ToolComponent](./tool_comp.md#class-openjiuwencoreworkflowcomponentstooltool_comptoolcomponent), otherwise initialization will throw exception.

---

## class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponent

```python
class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponent(config: ToolComponentConfig)
```

Tool component, implements [ComponentComposable](../components.md). Gets tool instance from `Runner.resource_mgr` according to `tool_id` in [ToolComponentConfig](./tool_comp.md#class-openjiuwencoreworkflowcomponentstooltool_comptoolcomponentconfig) during construction; [to_executable](./tool_comp.md#to_executable---executable) returns ToolExecutable instance, which validates input and calls tool at runtime.

**Parameters**:

- **config** (ToolComponentConfig): Tool component configuration, where `tool_id` is required and must be registered in Runner.

**Exceptions**:

- **BaseError**: When `tool_id` is empty or corresponding tool is not found in Runner, error code see `COMPONENT_TOOL_INIT_FAILED` in [StatusCode](../../../common/exception/status_code.md).

### to_executable() -> Executable

Returns executable instance ToolExecutable corresponding to this component, which internally has bound tool obtained from Runner.

**Returns**:

- **Executable**: That is `ToolExecutable(self._config).set_tool(self._tool)`.
