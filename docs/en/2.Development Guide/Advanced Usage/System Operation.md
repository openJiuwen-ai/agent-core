# System Operation (SysOperation)
SysOperation is a system operation abstraction layer that provides a unified encapsulation of File System (FS), Code Execution (Code), and Command Line (Shell) capabilities. It supports seamless switching between Local and Sandbox modes via a consistent interface, ensuring a unified invocation style for upper-layer Agents and business logic.

Configured via `SysOperationCard` and managed by `Runner.resource_mgr`. Its core features include:

- **Cross-platform Support**: The `LOCAL` mode is compatible with Windows, Linux, and macOS, automatically handling path separators and platform-specific execution details.
- **Stateless Single Instance**: Once registered, it reuses the same instance within the process, ensuring efficient resource utilization.

SysOperation provides two invocation methods to meet the needs of different scenarios:

- **Direct Call**: Directly obtain a SysOperation instance in business code and call its methods. Commonly used for encapsulating advanced capabilities (such as [Skills](./Skills%20and%20System%20Operations.md)).
- **As a Tool Call**: Obtain a `LocalFunction` tool instance via Tool ID. This is the **standard path for Agents to call the system**, making it easy to directly inherit system operation capabilities into the Agent.

---

## 1. Registration to Resource Manager

### 1.1 Configuration and Registration

Define the configuration using `SysOperationCard` and register it via `Runner.resource_mgr.add_sys_operation()`. During registration, methods of all sub-operations are automatically extracted as `LocalFunction` tools and registered in the tool manager.

```python
import asyncio
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig

async def main():
    await Runner.start()

    # 1. Define configuration
    card = SysOperationCard(
        id="my_sys_op",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(
            work_dir="./workspace",
            shell_allowlist=["echo", "ls", "dir", "python", "pip", "git"]
        )
    )

    # 2. Register to Resource Manager
    result = Runner.resource_mgr.add_sys_operation(card)
    assert result.is_ok()

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

**Configuration Details**:

- [SysOperationCard](../API%20Docs/openjiuwen.core/sys_operation/sys_operation.md#class-sysoperationcard): System operation configuration card. `id` uniquely identifies the instance; subsequent retrieval and removal depend on this ID.
- [OperationMode](../API%20Docs/openjiuwen.core/sys_operation/sys_operation.md#class-operationmode): Execution mode. Currently supports `LOCAL`.
- [LocalWorkConfig](../API%20Docs/openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig): Configuration for Local mode.
  - `work_dir`: Path to the working directory. Relative paths for file operations are resolved based on this directory.
  - `shell_allowlist`: Allowlist of Shell command prefixes, limiting reachable commands. Set to `None` for no restriction.


### 1.2 Removal

Remove registered SysOperation and its associated tools via `remove_sys_operation`:

```python
result = Runner.resource_mgr.remove_sys_operation(sys_operation_id="my_sys_op")
```

---

## 2. Direct Call

Retrieve a `SysOperation` instance via `Runner.resource_mgr.get_sys_operation()`, then access its sub-operation interfaces. This method is suitable for orchestrating logic directly in Python code and serves as the foundation for [Custom Skills](./Skills%20and%20System%20Operations.md).

### 2.1 File System Operations

```python
# Get SysOperation instance
sys_op = Runner.resource_mgr.get_sys_operation("my_sys_op")

# Get file system operation interface
fs = sys_op.fs()

# Write file
write_res = await fs.write_file("hello.txt", "Hello OpenJiuwen!", mode="text")
if write_res.code == 0:
    print(f"Write successful: {write_res.data.path}, Size: {write_res.data.size} bytes")

# Read file
read_res = await fs.read_file("hello.txt", mode="text")
if read_res.code == 0:
    print(f"File content: {read_res.data.content}")

# Read first N lines
read_res = await fs.read_file("hello.txt", head=10)

# List files in directory
list_res = await fs.list_files(".", recursive=True, file_types=[".txt", ".py"])
if list_res.code == 0:
    for item in list_res.data.list_items:
        print(f"  {item.name} ({item.size} bytes)")

# Search files
search_res = await fs.search_files(".", pattern="*.py")
```

For more parameter details on file system operations, see [BaseFsOperation API Docs](../API%20Docs/openjiuwen.core/sys_operation/fs.md).

### 2.2 Code Execution Operations

```python
code_op = sys_op.code()

# Execute Python code
code_res = await code_op.execute_code("print('Hello from Python')", language="python")
if code_res.code == 0:
    print(f"stdout: {code_res.data.stdout}")
    print(f"exit_code: {code_res.data.exit_code}")
```

For more parameter details on code execution operations, see [BaseCodeOperation API Docs](../API%20Docs/openjiuwen.core/sys_operation/code.md).

### 2.3 Shell Command Operations

```python
shell_op = sys_op.shell()

# Execute Shell command (must be in allowlist)
shell_res = await shell_op.execute_cmd("echo Hello from Shell")
if shell_res.code == 0:
    print(f"stdout: {shell_res.data.stdout}")
```

For more parameter details on Shell operations, see [BaseShellOperation API Docs](../API%20Docs/openjiuwen.core/sys_operation/shell.md).

### 2.4 Returned Results

All operation methods return a subclass of [BaseResult](../API%20Docs/openjiuwen.core/sys_operation/result.md#class-baseresult). Common fields include:

- `code`: Status code. `0` indicates success, non-zero indicates failure.
- `message`: Status message.
- `data`: Business data, specific type depends on the invoked method.

```python
res = await fs.read_file("test.txt")
if res.code == 0:
    # Success, access data
    print(res.data.content)
else:
    # Failure, check error message
    print(f"Operation failed: {res.message}")
```

For more details on result types, see [Result API Docs](../API%20Docs/openjiuwen.core/sys_operation/result.md).

---

## 3. Call via Tool ID

After registering SysOperation, all of its methods are automatically wrapped as `LocalFunction` tools and registered in the resource manager. Each tool has a unique Tool ID formatted as `{card_id}.{op_type}.{method_name}`.

This is the **standard usage in Agent mode**: registered SysOperation tools automatically enter the resource manager's tool library. Developers retrieve tool cards or configurations via Tool ID and add them to the Agent's `ability_manager`, granting the Agent operational permissions over the system environment. This is demonstrated in detail in [Building ReActAgent](../Agents/Building ReActAgent.md).

### 3.1 Retrieve Tool ID

There are two ways to retrieve a Tool ID:

```python
card = SysOperationCard(id="my_sys_op")

# Method 1: Via card shortcut properties (Recommended)
tool_id = card.fs.read_file          # -> "my_sys_op.fs.read_file"
tool_id = card.shell.execute_cmd     # -> "my_sys_op.shell.execute_cmd"
tool_id = card.code.execute_code     # -> "my_sys_op.code.execute_code"

# Method 2: Via static method generation
tool_id = SysOperationCard.generate_tool_id("my_sys_op", "fs", "read_file")
```

Retrieve the tool instance via `Runner.resource_mgr.get_tool()`:

```python
read_file_tool = Runner.resource_mgr.get_tool(card.fs.read_file)
```

### 3.2 Invoking Tool

Execute non-streaming operations via the `invoke` method, passing an argument dictionary:

```python
# Read file
res = await read_file_tool.invoke({"path": "hello.txt"})
print(res.data.content)

# With optional parameters
res = await read_file_tool.invoke({"path": "hello.txt", "mode": "text", "head": 5})

# Write file
res = await write_file_tool.invoke({"path": "output.txt", "content": "Sample content"})

# Execute Shell command
res = await execute_cmd_tool.invoke({"command": "echo hello"})

# Execute Python code
res = await execute_code_tool.invoke({"code": "print('hello')", "language": "python"})
```

### 3.3 Streaming Invocations

For streaming methods (e.g., `read_file_stream`), obtain an async iterator via the `stream` method:

```python
read_stream_tool = Runner.resource_mgr.get_tool(card.fs.read_file_stream)

async for chunk_res in read_stream_tool.stream({"path": "large_file.bin", "mode": "bytes", "chunk_size": 8192}):
    if chunk_res.code == 0:
        print(f"Chunk {chunk_res.data.chunk_index}: {chunk_res.data.chunk_size} bytes")
```

### 3.4 Adding to Agent (ability)

The typical way to add a SysOperation tool to an Agent is via `ability_manager.add()`. For details on how to chain these tools into a real Agent execution flow, see [4.2 Integration into Agent](#42-integration-into-agent).

```python
# Get tool instance
tool = Runner.resource_mgr.get_tool(card.fs.read_file)

# Add via tool instance card
agent.ability_manager.add(tool.card)
```

---

## 4. Complete Examples

### 4.1 Basic Usage

Here is a complete closed-loop example including registration, direct call (SDK mode), and call via Tool ID (Tool mode):

```python
import asyncio
import os
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig

async def main():
    # Start Runner
    await Runner.start()

    try:
        # ---- 1. Registration ----
        card = SysOperationCard(
            id="demo_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir="./workspace")
        )
        result = Runner.resource_mgr.add_sys_operation(card)
        assert result.is_ok()

        # ---- 2. Direct Call (SDK Mode) ----
        # Suitable for orchestrating logic directly in Python
        sys_op = Runner.resource_mgr.get_sys_operation("demo_op")
        fs = sys_op.fs()

        await fs.write_file("test.txt", "Hello World", prepend_newline=False)
        read_res = await fs.read_file("test.txt")
        print(f"Direct call read result: {read_res.data.content}")

        # ---- 3. Call via Tool ID (Tool Mode) ----
        # Treats the operation as an atomic tool supporting invoke/stream calls
        read_tool = Runner.resource_mgr.get_tool(card.fs.read_file)
        tool_res = await read_tool.invoke({"path": "test.txt"})
        print(f"Tool call read result: {tool_res.data.content}")

        # Shell tool example
        shell_tool = Runner.resource_mgr.get_tool(card.shell.execute_cmd)
        shell_res = await shell_tool.invoke({"command": "echo Done"})
        print(f"Shell tool output: {shell_res.data.stdout.strip()}")

    finally:
        # ---- Cleanup ----
        Runner.resource_mgr.remove_sys_operation(sys_operation_id="demo_op")
        await Runner.stop()

if __name__ == "__main__":
    os.makedirs("./workspace", exist_ok=True)
    asyncio.run(main())
```

---

### 4.2 Integration into Agent

The following shows how to mount `SysOperation` tools onto a `ReActAgent`, enabling the Agent to autonomously perform file operations and code execution:

```python
import asyncio
import os
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig


async def main():
    await Runner.start()

    try:
        # ---- Step 1: Register System Operation ----
        sysop_card = SysOperationCard(
            id="my_local_sys",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                work_dir="./workspace",
                shell_allowlist=["echo", "ls", "python", "cat"]
            )
        )
        Runner.resource_mgr.add_sys_operation(sysop_card)

        # ---- Step 2: Configure ReActAgent ----
        agent_card = AgentCard(id="system_helper", name="System Helper")
        agent = ReActAgent(card=agent_card)

        cfg = (ReActAgentConfig()
               .configure_model_client(
                   provider="OpenAI",              # Replace with your model provider
                   api_key="your_api_key",         # Replace with your key
                   api_base="your_api_base",       # Replace with your API URL
                   model_name="your_model_name",   # Replace with your model name
               )
               .configure_prompt_template([
                   {"role": "system", "content": "You are a precise system assistant. You can read files, run code, and execute simple commands to solve user problems."}
               ])
               .configure_max_iterations(5)
               )
        
        agent.configure(cfg)

        # ---- Step 3: Mount Capabilities to Agent ----
        rm = Runner.resource_mgr

        # Add ToolCard objects
        read_tool = rm.get_tool(sysop_card.fs.read_file)
        if read_tool:
            agent.ability_manager.add(read_tool.card)

        write_tool = rm.get_tool(sysop_card.fs.write_file)
        if write_tool:
            agent.ability_manager.add(write_tool.card)

        code_tool = rm.get_tool(sysop_card.code.execute_code)
        if code_tool:
            agent.ability_manager.add(code_tool.card)

        # ---- Step 4: Run Task ----
        query = "Please create a file named math_task.txt in the current directory with the content '10, 20, 30'. Then read this file and use Python to calculate the sum of these numbers."

        print(f"--- Task Started ---")
        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": query, "conversation_id": "demo_001"}
        )

        print(f"\n--- Agent Response ---")
        print(result.get("output", "No result obtained"))

    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_card.id)
        await Runner.stop()


if __name__ == "__main__":
    os.makedirs("./workspace", exist_ok=True)
    asyncio.run(main())
```
