# openjiuwen.core.workflow

`openjiuwen.core.workflow.components.tool.tool_comp` 模块提供工作流中的工具（插件）组件，用于绑定并执行通过 [Runner](../../../runner/runner.md) 注册的工具。组件根据配置的 `tool_id` 从资源管理器获取 [Tool](../../../foundation/tool.README.md) 实例，将节点输入校验后传给工具执行，并将结果封装为统一输出格式。组件通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig` 导入。更多组件说明见 [components](../../components.README.md)。

## class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponentConfig

```python
class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponentConfig()
```

工具组件的配置数据类，继承自 [ComponentConfig](../components.md)。当前主要用于指定要绑定的工具 id，其余为预留扩展。

* **tool_id**（str | None）：要绑定的工具 id，对应 [Runner](../../../runner/runner.md) 资源管理器中注册的工具。构造 [ToolComponent](tool_comp.md#class-openjiuwencoreworkflowcomponentstooltool_comptoolcomponent) 时必填，否则初始化会抛异常。

---

## class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponent

```python
class openjiuwen.core.workflow.components.tool.tool_comp.ToolComponent(config: ToolComponentConfig)
```

工具组件，实现 [ComponentComposable](../components.md)。在构造时根据 [ToolComponentConfig](tool_comp.md#class-openjiuwencoreworkflowcomponentstooltool_comptoolcomponentconfig) 的 `tool_id` 从 `Runner.resource_mgr` 获取工具实例；[to_executable](tool_comp.md#to_executable---executable) 返回ToolExecutable实例，由该可执行对象在运行时校验输入并调用工具。

**参数**：

- **config**（ToolComponentConfig）：工具组件配置，其中 `tool_id` 必填且必须在 Runner 中已注册。

**异常**：

- **BaseError**：当 `tool_id` 为空或 Runner 中找不到对应工具时，错误码参见 [StatusCode](../../../common/exception/status_code.md) 中的 `COMPONENT_TOOL_INIT_FAILED`。

### to_executable() -> Executable

返回本组件对应的可执行实例ToolExecutable，该实例内部已绑定从 Runner 获取的工具。

**返回**：

- **Executable**：即 `ToolExecutable(self._config).set_tool(self._tool)`。
