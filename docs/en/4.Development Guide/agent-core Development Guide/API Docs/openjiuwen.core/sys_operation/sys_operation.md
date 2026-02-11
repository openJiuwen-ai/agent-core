# openjiuwen.core.sys_operation

## class OperationMode

Defines identifiers for different modes during system operation execution.

**Parameters**:

* **LOCAL**: Indicates that system operations are executed in a local environment.
* **SANDBOX**: Indicates that system operations are executed in an isolated sandbox environment. Note: Sandbox mode is currently not supported in this version.

## class LocalWorkConfig

Represents the local work environment configuration used when system operations are executed in a local environment.

**Parameters**:

* **shell_allowlist** (List[str], optional): Whitelist of allowed Shell command prefixes. Default value: `["echo", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3", "npm", "node", "git", "cat", "type", "mkdir", "md", "rm", "rd", "cp", "copy", "mv", "move", "grep", "find", "curl", "wget", "ps", "df", "ping"]`. If the value is `None`, it means all Shell commands are allowed. Note that this does not meet security requirements, so it is not recommended to set it to `None`.
* **work_dir** (str, optional): Local working directory path. Default value: `None`.

## class SysOperationCard

```python
class SysOperationCard(BaseCard)
```

`SysOperationCard` is the data class for system operation configuration card, inheriting from `BaseCard`.

**Parameters**:

* **mode** ([OperationMode](#class-operationmode), optional): Execution mode. Default value: `OperationMode.LOCAL`.
* **work_config** ([LocalWorkConfig](#class-localworkconfig), optional): Local work configuration. Default value: `None`.
* **gateway_config** (SandboxGatewayConfig, optional): Sandbox gateway configuration. Default value: `None`.
* **fs**: Shorthand for getting file system tool IDs.
* **shell**: Shorthand for getting Shell tool IDs.
* **code**: Shorthand for getting code execution tool IDs.
* **Others**: Supports dynamic access to other operation tool IDs. For example, `card.browser.navigate`.

**Example**:

```python
>>> card = SysOperationCard(id="sys_op")
>>> print(card.fs.read_file)  # Quickly get the Tool ID
'sys_op.fs.read_file'
```

**Methods**:

### generate_tool_id

```python
@staticmethod
generate_tool_id(card_id: str, op_type: str, method_name: str) -> str
```

Static method. Generates a unique identifier (Tool ID) for a system operation tool.

**Parameters**:

* **card_id** (str): The ID of the operation card.
* **op_type** (str): The type of operation, such as `fs`, `shell`, `code`, etc.
* **method_name** (str): The specific method name.

**Returns**:

**str**, the Tool ID string in the format `{card_id}.{op_type}.{method_name}`.

**Example**:

```python
>>> SysOperationCard.generate_tool_id("sys_op", "fs", "read_file")
'sys_op.fs.read_file'
```

## class SysOperation

```python
class SysOperation(card: SysOperationCard)
```

System operation entry class, providing access to file, code, and Shell operations.

**Parameters**:

* **card** ([SysOperationCard](#class-sysoperationcard)): System operation configuration card.

### fs

```python
fs() -> BaseFsOperation
```

Get file system operation instance.

**Returns**:

**[BaseFsOperation](./fs.md#class-basefsoperation)**, corresponding file system operation instance.

### code

```python
code() -> BaseCodeOperation
```

Get code execution operation instance.

**Returns**:

**[BaseCodeOperation](./code.md#class-basecodeoperation)**, corresponding code execution operation instance.

### shell

```python
shell() -> BaseShellOperation
```

Get Shell execution operation instance.

**Returns**:

**[BaseShellOperation](./shell.md#class-baseshelloperation)**, corresponding Shell execution operation instance.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.sys_operation.sys_operation import SysOperation, >>> SysOperationCard, OperationMode, LocalWorkConfig
>>> 
>>> async def main():
>>>     # 1. Initialize configuration (local mode)
>>>     # Set working directory and configure allowed Shell command whitelist
>>>     config = LocalWorkConfig(
>>>         work_dir="./workspace",
>>>         shell_allowlist=["echo", "ls", "dir", "python"]
>>>     )
>>>     
>>>     # 2. Create SysOperation instance
>>>     card = SysOperationCard(mode=OperationMode.LOCAL, work_config=config)
>>>     sys_op = SysOperation(card)

>>>     # 3. File system operations (FS)
>>>     print("--- File System Operations ---")
>>>     fs = sys_op.fs()
>>>     
>>>     # Write file
>>>     # Note: If directory does not exist, write_file will try to automatically create parent directories
>>>     write_res = await fs.write_file("test.txt", "Hello OpenJiuwen!", mode="text")
>>>     if write_res.code == 0:
>>>         print(f"Write successful: {write_res.data.path}")
>>>     else:
>>>         print(f"Write failed: {write_res.message}")
>>>     
>>>     # Read file
>>>     read_res = await fs.read_file("test.txt", mode="text")
>>>     if read_res.code == 0:
>>>         print(f"File content: {read_res.data.content}")

>>>     # 4. Code execution operations (Code)
>>>     print("\n--- Code Execution Operations ---")
>>>     code_op = sys_op.code()
>>>     
>>>     # Execute Python code
>>>     code = "print('Hello from Code Operation')"
>>>     code_res = await code_op.execute_code(code, language="python")
>>>     if code_res.code == 0:
>>>         print(f"Code output: {code_res.data.stdout.strip()}")
>>>     else:
>>>         print(f"Code execution failed: {code_res.message}")

>>>     # 5. Shell command operations (Shell)
>>>     print("\n--- Shell Command Operations ---")
>>>     shell_op = sys_op.shell()
>>>     
>>>     # Execute Shell command
>>>     # Note: Command must be allowed in shell_allowlist
>>>     shell_res = await shell_op.execute_cmd("echo Hello from Shell Operation")
>>>     if shell_res.code == 0:
>>>         print(f"Shell output: {shell_res.data.stdout.strip()}")
>>>     else:
>>>         print(f"Shell execution failed: {shell_res.message}")

>>> if __name__ == "__main__":
>>>     asyncio.run(main())
```
