# openjiuwen.core.sys_operation.base

## class BaseOperation

```python
class BaseOperation(name: str,
                    mode: OperationMode,
                    description: str,
                    run_config: Union[LocalWorkConfig, SandboxGatewayConfig])
```

`BaseOperation` is the abstract base class for system operations. All types of system operations need to inherit from this class and implement custom operation logic.

**Parameters**:

- **name** (str): Unique identifier name for the operation, used to distinguish different operation instances. Must be unique and semantic.
- **mode** ([OperationMode](./sys_operation.md#class-operationmode)): Operation execution mode, a specified operation mode enumeration type that determines the execution environment rules and execution method.
- **description** (str): Description information for the operation, used to explain the operation's functionality, applicable scenarios, execution logic, etc., improving readability and maintainability.
- **run_config** (Union[[LocalWorkConfig](./sys_operation.md#class-localworkconfig), SandboxGatewayConfig], optional): Operation runtime configuration items, supporting local runtime configuration or sandbox gateway runtime configuration. Pass the corresponding configuration instance based on the operation's actual runtime environment, including core configurations such as environment parameters, resource limits, gateway connection information, etc. Note: Sandbox mode is currently not supported in this version.

### list_tools

```python
list_tools() -> List[ToolCard]
```

Get a list of available tools for this operation.

**Returns**:

**List[[ToolCard](../../foundation/tool.md#class-toolcard)]**, list of tool cards.
