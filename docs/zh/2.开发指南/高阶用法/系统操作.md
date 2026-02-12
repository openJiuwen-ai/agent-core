# 系统操作（SysOperation）
SysOperation 是一个系统操作抽象层，统一封装了 文件系统（FS）、代码执行（Code） 和 命令行（Shell） 三类能力，并通过一致的接口支持在 Local 与 Sandbox 模式之间无缝切换，使上层 Agent 与业务逻辑保持统一的调用方式。
通过 

`SysOperationCard` 进行配置，并由 `Runner.resource_mgr` 统一管理。其核心特性包括：

- **跨平台支持**：`LOCAL` 模式兼容 Windows、Linux 和 macOS，自动处理路径分隔符及平台特定的执行细节
- **无状态单实例**：注册后在进程内复用同一实例，确保资源高效利用


SysOperation 提供两种调用方式，满足不同场景的需求：

- **直接调用**：在业务代码中直接获取 SysOperation 实例并调用其方法，常用于封装高级能力（如 [Skills](./技能与系统操作.md)）。
- **作为工具调用**：通过 Tool ID 获取 `LocalFunction` 工具实例。这是 **Agent 调用系统的标准路径**，便于将系统操作能力直接继承到 Agent 中。

---

## 1. 注册到资源管理器

### 1.1 配置与注册

使用 `SysOperationCard` 定义配置，并通过 `Runner.resource_mgr.add_sys_operation()` 注册到资源管理器。注册时会自动将所有子操作的方法提取为 `LocalFunction` 工具并注册到工具管理器中。

```python
import asyncio
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig

async def main():
    await Runner.start()

    # 1. 定义配置
    card = SysOperationCard(
        id="my_sys_op",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(
            work_dir="./workspace",
            shell_allowlist=["echo", "ls", "dir", "python", "pip", "git"]
        )
    )

    # 2. 注册到资源管理器
    result = Runner.resource_mgr.add_sys_operation(card)
    assert result.is_ok()

asyncio.run(main())
```

**配置说明**：

- [SysOperationCard](../API文档/openjiuwen.core/sys_operation/sys_operation.md#class-sysoperationcard)：系统操作配置卡片。`id` 用于唯一标识该实例，后续获取和移除均依赖此 ID。
- [OperationMode](../API文档/openjiuwen.core/sys_operation/sys_operation.md#class-operationmode)：运行模式。当前支持 `LOCAL`（本地模式）。
- [LocalWorkConfig](../API文档/openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig)：本地模式配置。
  - `work_dir`：工作目录路径，文件操作的相对路径将以此目录为基准解析。
  - `shell_allowlist`：Shell 命令前缀白名单，限制可执行的命令范围。设为 `None` 表示不限制。


### 1.2 移除

通过 `remove_sys_operation` 移除已注册的 SysOperation 及其关联的所有工具：

```python
result = Runner.resource_mgr.remove_sys_operation(sys_operation_id="my_sys_op")
```

---

## 2. 直接调用

通过 `Runner.resource_mgr.get_sys_operation()` 获取 `SysOperation` 实例，然后访问其子操作接口。这种方式适合在 Python 代码中直接编排逻辑，也是实现 [自定义技能](./技能与系统操作.md) 的底层基础。

### 2.1 文件系统操作

```python
# 获取 SysOperation 实例
sys_op = Runner.resource_mgr.get_sys_operation("my_sys_op")

# 获取文件系统操作接口
fs = sys_op.fs()

# 写入文件
write_res = await fs.write_file("hello.txt", "Hello OpenJiuwen!", mode="text")
if write_res.code == 0:
    print(f"写入成功: {write_res.data.path}, 大小: {write_res.data.size} 字节")

# 读取文件
read_res = await fs.read_file("hello.txt", mode="text")
if read_res.code == 0:
    print(f"文件内容: {read_res.data.content}")

# 读取前 N 行
read_res = await fs.read_file("hello.txt", head=10)

# 列出目录下的文件
list_res = await fs.list_files(".", recursive=True, file_types=[".txt", ".py"])
if list_res.code == 0:
    for item in list_res.data.list_items:
        print(f"  {item.name} ({item.size} bytes)")

# 搜索文件
search_res = await fs.search_files(".", pattern="*.py")
```

更多文件系统操作的参数说明，参见 [BaseFsOperation API 文档](../API文档/openjiuwen.core/sys_operation/fs.md)。

### 2.2 代码执行操作

```python
code_op = sys_op.code()

# 执行 Python 代码
code_res = await code_op.execute_code("print('Hello from Python')", language="python")
if code_res.code == 0:
    print(f"stdout: {code_res.data.stdout}")
    print(f"exit_code: {code_res.data.exit_code}")
```

更多代码执行操作的参数说明，参见 [BaseCodeOperation API 文档](../API文档/openjiuwen.core/sys_operation/code.md)。

### 2.3 Shell 命令操作

```python
shell_op = sys_op.shell()

# 执行 Shell 命令（命令需在白名单中）
shell_res = await shell_op.execute_cmd("echo Hello from Shell")
if shell_res.code == 0:
    print(f"stdout: {shell_res.data.stdout}")
```

更多 Shell 操作的参数说明，参见 [BaseShellOperation API 文档](../API文档/openjiuwen.core/sys_operation/shell.md)。

### 2.4 返回结果

所有操作方法均返回 [BaseResult](../API文档/openjiuwen.core/sys_operation/result.md#class-baseresult) 的子类。通用字段如下：

- `code`：状态码。`0` 表示成功，非 `0` 表示失败。
- `message`：状态消息。
- `data`：业务数据，具体类型取决于调用的方法。

```python
res = await fs.read_file("test.txt")
if res.code == 0:
    # 成功，访问 data
    print(res.data.content)
else:
    # 失败，查看错误信息
    print(f"操作失败: {res.message}")
```

更多结果类型说明，参见 [Result API 文档](../API文档/openjiuwen.core/sys_operation/result.md)。

---

## 3. 通过 Tool ID 调用

注册 SysOperation 后，其所有方法会自动被包装为 `LocalFunction` 工具并注册到资源管理器中。每个工具都有一个唯一的 Tool ID，格式为 `{card_id}.{op_type}.{method_name}`。

这是 Agent 模式下的**标准使用方式**：系统操作注册后，其关联工具会自动进入资源管理器的工具库。开发者通过 Tool ID 获取工具卡片（Card）或配置，并将其添加到 Agent 的 `ability_manager` 中，从而使 Agent 获得对系统环境的操作权限。

### 3.1 获取 Tool ID

有两种方式获取 Tool ID：

```python
card = SysOperationCard(id="my_sys_op")

# 方式一：通过 card 的快捷属性（推荐）
tool_id = card.fs.read_file          # -> "my_sys_op.fs.read_file"
tool_id = card.shell.execute_cmd     # -> "my_sys_op.shell.execute_cmd"
tool_id = card.code.execute_code     # -> "my_sys_op.code.execute_code"

# 方式二：通过静态方法生成
tool_id = SysOperationCard.generate_tool_id("my_sys_op", "fs", "read_file")
```

通过 `Runner.resource_mgr.get_tool()` 获取工具实例：

```python
read_file_tool = Runner.resource_mgr.get_tool(card.fs.read_file)
```

### 3.2 调用工具

通过 `invoke` 方法执行非流式操作，传入参数字典：

```python
# 读取文件
res = await read_file_tool.invoke({"path": "hello.txt"})
print(res.data.content)

# 带可选参数
res = await read_file_tool.invoke({"path": "hello.txt", "mode": "text", "head": 5})

# 写入文件
res = await write_file_tool.invoke({"path": "output.txt", "content": "写入内容"})

# 执行 Shell 命令
res = await execute_cmd_tool.invoke({"command": "echo hello"})

# 执行 Python 代码
res = await execute_code_tool.invoke({"code": "print('hello')", "language": "python"})
```

### 3.3 流式调用

对于流式方法（如 `read_file_stream`），通过 `stream` 方法获取异步迭代器：

```python
read_stream_tool = Runner.resource_mgr.get_tool(card.fs.read_file_stream)

async for chunk_res in read_stream_tool.stream({"path": "large_file.bin", "mode": "bytes", "chunk_size": 8192}):
    if chunk_res.code == 0:
        print(f"分片 {chunk_res.data.chunk_index}: {chunk_res.data.chunk_size} 字节")
```

### 3.4 添加到 Agent

将系统操作工具添加到 Agent 的典型方式是使用 `ability_manager.add()`。关于如何将这些工具串联到一个真实的 Agent 运行流程中，请参考 [4.2 集成到 Agent](#42-集成到-agent)。

```python
# 获取工具实例
tool = Runner.resource_mgr.get_tool(card.fs.read_file)

# 通过工具实例的 card 添加
agent.ability_manager.add(tool.card)
```

---

## 4. 完整示例
### 4.1 基础用法
以下是一个包含注册、直接调用（SDK 模式）和通过 Tool ID 调用（工具模式）的完整闭环示例：

```python
import asyncio
import os
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig

async def main():
    # 启动 Runner
    await Runner.start()

    try:
        # ---- 1. 注册 ----
        card = SysOperationCard(
            id="demo_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir="./workspace")
        )
        result = Runner.resource_mgr.add_sys_operation(card)
        assert result.is_ok()

        # ---- 2. 直接调用 (SDK 模式) ----
        # 这种方式适合在 Python 代码中直接编排逻辑
        sys_op = Runner.resource_mgr.get_sys_operation("demo_op")
        fs = sys_op.fs()

        await fs.write_file("test.txt", "Hello World", prepend_newline=False)
        read_res = await fs.read_file("test.txt")
        print(f"直接调用读取结果: {read_res.data.content}")

        # ---- 3. 通过 Tool ID 调用 (工具模式) ----
        # 这种方式将操作视为一个原子工具，支持 invoke/stream 调用
        read_tool = Runner.resource_mgr.get_tool(card.fs.read_file)
        tool_res = await read_tool.invoke({"path": "test.txt"})
        print(f"工具调用读取结果: {tool_res.data.content}")

        # Shell 工具示例
        shell_tool = Runner.resource_mgr.get_tool(card.shell.execute_cmd)
        shell_res = await shell_tool.invoke({"command": "echo Done"})
        print(f"Shell 工具输出: {shell_res.data.stdout.strip()}")

    finally:
        # ---- 清理 ----
        Runner.resource_mgr.remove_sys_operation(sys_operation_id="demo_op")
        await Runner.stop()

if __name__ == "__main__":
    os.makedirs("./workspace", exist_ok=True)
    asyncio.run(main())
```

### 4.2 集成到 Agent

以下展示了如何将 `SysOperation` 工具挂载到 `ReActAgent` 中，实现让 Agent 自主操作文件和执行代码：

```python
import asyncio
import os
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig


async def main():
    await Runner.start()

    try:
        # ---- 第一步：注册系统操作 ----
        sysop_card = SysOperationCard(
            id="my_local_sys",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                work_dir="./workspace",
                shell_allowlist=["echo", "ls", "python", "cat"]
            )
        )
        Runner.resource_mgr.add_sys_operation(sysop_card)

        # ---- 第二步：配置 ReActAgent ----
        agent_card = AgentCard(id="system_helper", name="系统助手")
        agent = ReActAgent(card=agent_card)

        cfg = (ReActAgentConfig()
               .configure_model_client(
                   provider="OpenAI",              # 替换为您的模型提供商
                   api_key="your_api_key",         # 替换为您的密钥
                   api_base="your_api_base",       # 替换为您的 API 地址
                   model_name="your_model_name",   # 替换为您的模型名称
               )
               .configure_prompt_template([
                   {"role": "system", "content": "你是一个严谨的系统助手。你可以阅读文件、运行代码和执行简单的命令来解决用户问题。"}
               ])
               .configure_max_iterations(5)
               )
        
        agent.configure(cfg)

        # ---- 第三步：挂载能力到 Agent ----
        rm = Runner.resource_mgr

        # 添加ToolCard 对象
        read_tool = rm.get_tool(sysop_card.fs.read_file)
        if read_tool:
            agent.ability_manager.add(read_tool.card)

        write_tool = rm.get_tool(sysop_card.fs.write_file)
        if write_tool:
            agent.ability_manager.add(write_tool.card)

        code_tool = rm.get_tool(sysop_card.code.execute_code)
        if code_tool:
            agent.ability_manager.add(code_tool.card)

        # ---- 第四步：运行任务 ----
        query = "请在当前目录下创建一个名为 math_task.txt 的文件，内容写入一行数字 '10, 20, 30'。然后读取该文件并用 Python 计算这些数字的总和。"

        print(f"--- 任务启动 ---")
        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": query, "conversation_id": "demo_001"}
        )

        print(f"\n--- Agent 回答 ---")
        print(result.get("output", "未获取到结果"))

    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_card.id)
        await Runner.stop()


if __name__ == "__main__":
    os.makedirs("./workspace", exist_ok=True)
    asyncio.run(main())
```
