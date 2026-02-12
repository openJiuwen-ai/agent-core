# openjiuwen.core.sys_operation.base

## class BaseOperation

```python
class BaseOperation(name: str,
                    mode: OperationMode,
                    description: str,
                    run_config: Union[LocalWorkConfig, SandboxGatewayConfig])
```

`BaseOperation`是系统操作的抽象基类。所有类型的系统操作需继承该类，并实现自定义操作逻辑。

**参数**：

- **name**(str)：操作的唯一标识名称，用于区分不同的操作实例，需保证命名唯一且具有语义性。
- **mode**([OperationMode](./sys_operation.md#class-operationmode))：操作的运行模式，为指定的运行模式枚举类型，决定操作的执行环境规则与运行方式。
- **description**(str)：操作的描述信息，用于说明该操作的功能、适用场景、执行逻辑等，提升可读性与可维护性。
- **run_config**(Union[[LocalWorkConfig](./sys_operation.md#class-localworkconfig), SandboxGatewayConfig], 可选)：操作的运行配置项，支持本地运行配置或沙箱网关运行配置两种类型，根据操作的实际运行环境传入对应配置实例，包含运行所需的环境参数、资源限制、网关连接信息等核心配置。其中，沙箱模式在当前版本暂不支持。

### list_tools

```python
list_tools() -> List[ToolCard]
```

获取该操作可用的工具列表。

**返回**：

**List[[ToolCard](../../foundation/tool.md#class-toolcard)]**，工具卡片列表。