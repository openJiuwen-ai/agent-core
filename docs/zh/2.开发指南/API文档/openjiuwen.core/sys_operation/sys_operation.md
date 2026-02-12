# openjiuwen.core.sys_operation

## class OperationMode

定义了系统操作执行过程中不同模式的标识。

**参数**：

* **LOCAL**：表示系统操作在本地环境中执行。
* **SANDBOX**：表示系统操作在隔离的沙箱环境中执行。其中，沙箱模式在当前版本暂不支持。

## class LocalWorkConfig

表示系统操作在本地环境中执行所使用的本地工作环境配置。

**参数**：

* **shell_allowlist**(List[str], 可选)：允许执行的Shell命令前缀白名单。默认值：`["echo", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3", "npm", "node", "git", "cat", "type", "mkdir", "md", "rm", "rd", "cp", "copy", "mv", "move", "grep", "find", "curl", "wget", "ps", "df", "ping"]`。如果值为`None`，表示允许执行所有Shell命令，需要注意，这是不满足安全要求的，所以不建议设置为`None`。
* **work_dir**(str, 可选)：本地工作目录路径。默认值：`None`。

## class SysOperationCard

```python
class SysOperationCard(BaseCard)
```

`SysOperationCard`是系统操作配置卡片的数据类，继承于`BaseCard`。

**参数**：

* **mode**([OperationMode](#class-operationmode), 可选)：运行模式。默认值：`OperationMode.LOCAL`。
* **work_config**([LocalWorkConfig](#class-localworkconfig), 可选)：本地工作配置。默认值：`None`。
* **gateway_config**(SandboxGatewayConfig, 可选)：沙箱网关配置。默认值：`None`。
* **fs**：快速获取文件系统工具 ID 的入口。
* **shell**：快速获取 Shell 工具 ID 的入口。
* **code**：快速获取代码执行工具 ID 的入口。
* **其它参数**：支持动态获取。例如通过 `card.browser.navigate` 获取浏览器操作的工具 ID。

**样例**：

```python
>>> card = SysOperationCard(id="sys_op")
>>> print(card.fs.read_file)  # 快速获取工具 ID
'sys_op.fs.read_file'
```

**方法**：

### generate_tool_id

```python
@staticmethod
generate_tool_id(card_id: str, op_type: str, method_name: str) -> str
```

静态方法。生成系统操作工具的唯一标识符（Tool ID）。

**参数**：

* **card_id**(str)：操作卡片的 ID。
* **op_type**(str)：操作类型，如 `fs`、`shell`、`code` 等。
* **method_name**(str)：具体的方法名称。

**返回**：

**str**，格式为 `{card_id}.{op_type}.{method_name}` 的工具 ID 字符串。

**样例**：

```python
>>> SysOperationCard.generate_tool_id("sys_op", "fs", "read_file")
'sys_op.fs.read_file'
```

## class SysOperation

```python
class SysOperation(card: SysOperationCard)
```

系统操作入口类，提供对文件、代码和Shell操作的访问。

**参数**：

* **card**([SysOperationCard](#class-sysoperationcard))：系统操作配置卡片。

### fs

```python
fs() -> BaseFsOperation
```

获取文件系统操作实例。

**返回**：

**[BaseFsOperation](./fs.md#class-basefsoperation)**，对应的文件系统操作实例。

### code

```python
code() -> BaseCodeOperation
```

获取代码执行操作实例。

**返回**：

**[BaseCodeOperation](./code.md#class-basecodeoperation)**，对应的代码执行操作实例。

### shell

```python
shell() -> BaseShellOperation
```

获取Shell执行操作实例。

**返回**：

**[BaseShellOperation](./shell.md#class-baseshelloperation)**，对应的Shell执行操作实例。

**样例：**

```python
>>> import asyncio
>>> from openjiuwen.core.sys_operation.sys_operation import SysOperation, >>> SysOperationCard, OperationMode, LocalWorkConfig
>>> 
>>> async def main():
>>>     # 1. 初始化配置（本地模式）
>>>     # 设置工作目录并配置允许的 Shell 命令白名单
>>>     config = LocalWorkConfig(
>>>         work_dir="./workspace",
>>>         shell_allowlist=["echo", "ls", "dir", "python"]
>>>     )
>>>     
>>>     # 2. 创建 SysOperation 实例
>>>     card = SysOperationCard(mode=OperationMode.LOCAL, work_config=config)
>>>     sys_op = SysOperation(card)
>>> 
>>>     # 3. 文件系统操作 (FS)
>>>     print("--- 文件系统操作 ---")
>>>     fs = sys_op.fs()
>>>     
>>>     # 写入文件
>>>     # 注意：如果目录不存在，write_file 会尝试自动创建父目录
>>>     write_res = await fs.write_file("test.txt", "Hello OpenJiuwen!", mode="text")
>>>     if write_res.code == 0:
>>>         print(f"写入成功: {write_res.data.path}")
>>>     else:
>>>         print(f"写入失败: {write_res.message}")
>>>     
>>>     # 读取文件
>>>     read_res = await fs.read_file("test.txt", mode="text")
>>>     if read_res.code == 0:
>>>         print(f"文件内容: {read_res.data.content}")
>>> 
>>>     # 4. 代码执行操作 (Code)
>>>     print("\n--- 代码执行操作 ---")
>>>     code_op = sys_op.code()
>>>     
>>>     # 执行 Python 代码
>>>     code = "print('Hello from Code Operation')"
>>>     code_res = await code_op.execute_code(code, language="python")
>>>     if code_res.code == 0:
>>>         print(f"代码输出: {code_res.data.stdout.strip()}")
>>>     else:
>>>         print(f"代码执行失败: {code_res.message}")
>>> 
>>>     # 5. Shell 命令操作 (Shell)
>>>     print("\n--- Shell 命令操作 ---")
>>>     shell_op = sys_op.shell()
>>>     
>>>     # 执行 Shell 命令
>>>     # 注意：命令必须在 shell_allowlist 中允许
>>>     shell_res = await shell_op.execute_cmd("echo Hello from Shell Operation")
>>>     if shell_res.code == 0:
>>>         print(f"Shell 输出: {shell_res.data.stdout.strip()}")
>>>     else:
>>>         print(f"Shell 执行失败: {shell_res.message}")
>>> 
>>> if __name__ == "__main__":
>>>     asyncio.run(main())
```

