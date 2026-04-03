# Context Evolver

Context Evolver 是 openjiuwen agent-core 的记忆管理扩展，为智能体提供从过去交互中学习并检索相关知识的能力。它实现了三种先进的记忆算法：**ACE**（Agentic Context Engineering）、**RB**（Reasoning Bank）和 **ReMe**（Remember Me, Refine Me）。

## 概述

此扩展使智能体能够从过去的交互中学习，并检索相关知识以增强未来的响应。它实现了三种记忆算法：

- **ACE (Agentic Context Engineering)**：使用 `content` 和 `section` 字段进行结构化记忆存储，基于 Playbook 组织
- **RB (Reasoning Bank)**：使用 `title`、`description` 和 `content` 字段进行面向知识的记忆存储，支持丰富的描述
- **ReMe (Remember Me, Refine Me)**：使用 `when_to_use` 和 `content` 字段，结合向量检索与基于 LLM 的重排序和重写，实现智能记忆管理

## 快速开始

要进行实践介绍，请运行快速入门示例：

```bash
cd __CLONE_DIR__\agent-core
python -m examples.context_evolver.quickstart
```

`quickstart.py` 脚本演示：
1. **验证配置**：检查 API 密钥和模型设置
2. **创建记忆服务**：使用您选择的算法初始化 TaskMemoryService
3. **添加记忆**：以算法特定格式存储知识库项目
4. **创建记忆增强型智能体**：设置具有自动记忆注入功能的 ContextEvolvingReActAgent
5. **使用记忆检索进行查询**：调用具有自动记忆增强功能的智能体
6. **从交互中学习**：总结轨迹以提取并存储新记忆
7. **高级功能**：演示具有记忆感知并行处理（MATTS）的 HotpotQA 多跳推理

### 前置条件

1. 安装 Docker（参见下方 [基础设施配置](#基础设施配置)）
2. 启动 Milvus 实例（参见下方 [基础设施配置](#基础设施配置)）
3. 在 `.env` 文件中创建并配置 API 凭证
4. 在 `config.yaml` 文件中配置算法设置

## 基础设施配置

### 1. 安装 Docker

运行 Milvus 容器化服务需要先安装 Docker。

#### Linux（Ubuntu / Debian）

```bash
# 卸载旧版本 Docker
sudo apt-get remove -y docker docker-engine docker.io containerd runc

# 安装依赖
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# 添加 Docker 官方 GPG 密钥及软件源
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装 Docker Engine 及 Compose 插件
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 允许非 root 用户运行 Docker（执行后需重新登录）
sudo usermod -aG docker $USER

# 验证安装
docker --version
docker compose version
```

#### Windows

1. 从 [https://docs.docker.com/desktop/install/windows-install/](https://docs.docker.com/desktop/install/windows-install/) 下载 **Docker Desktop for Windows**。
2. 运行安装程序（`Docker Desktop Installer.exe`）并按向导操作。
3. 建议选择 **WSL 2 后端**，或在提示时启用 Hyper-V。
4. 安装完成后启动 Docker Desktop，并在 PowerShell 中验证：

```powershell
docker --version
docker compose version
```

#### macOS

1. 从 [https://docs.docker.com/desktop/install/mac-install/](https://docs.docker.com/desktop/install/mac-install/) 下载 **Docker Desktop for Mac**。
2. 打开 `.dmg` 文件，将 Docker 拖入 `Applications` 文件夹并启动。
3. 在终端中验证：

```bash
docker --version
docker compose version
```

---

### 2. 使用 Docker 安装 Milvus

Milvus Standalone 是推荐用于开发和小规模生产的单节点部署方式，使用 Docker Compose 管理三个服务：**Milvus**、**etcd** 和 **MinIO**。

#### 第一步 — 下载官方 Compose 文件

**Linux / macOS**

```bash
wget https://github.com/milvus-io/milvus/releases/download/v2.6.2/milvus-standalone-docker-compose.yml \
    -O docker-compose.yml
```

**Windows（PowerShell）**

```powershell
Invoke-WebRequest `
    -URI "https://github.com/milvus-io/milvus/releases/download/v2.6.2/milvus-standalone-docker-compose.yml" `
    -OutFile "docker-compose.yml"
```

#### 第二步 — 启动 Milvus

```bash
docker compose up -d
```

确认三个容器均已运行：

```bash
docker compose ps
```

预期输出：

```
NAME                IMAGE                               STATUS
milvus-standalone   milvusdb/milvus:v2.6.2             Up (healthy)
milvus-etcd         quay.io/coreos/etcd:v3.5.5         Up (healthy)
milvus-minio        minio/minio:RELEASE.2023-03-13...  Up (healthy)
```

Milvus gRPC 现已在 `localhost:19530` 上提供服务。

#### 第三步 — 验证连通性（可选）

```bash
# 确认端口 19530 处于监听状态
docker exec milvus-standalone \
    python3 -c "from pymilvus import connections; connections.connect(); print('OK')"
```

#### 停止与重启 Milvus

```bash
# 停止（数据保留在 Docker 卷中）
docker compose down

# 重新启动
docker compose up -d

# 删除容器及所有数据
docker compose down --volumes --remove-orphans
```

#### 连接器所需的环境变量

在下一节创建的 `.env` 文件中添加 Milvus 连接配置：

```env
# Milvus 向量数据库
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_COLLECTION=vector_nodes
```

---

## 配置

配置从两个文件加载：

### 1. `.env` 文件（凭证）

在`openjiuwen\extensions\context_evolver\`创建包含敏感设置的 `.env` 文件：

```env
# API 配置
API_KEY=your-openai-api-key
API_BASE=https://api.openai.com/v1

# 模型配置
MODEL_NAME=gpt-5.2
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536

# 可选：LLM 参数
LLM_TEMPERATURE=0.7
LLM_SEED=42
LLM_SSL_VERIFY=false
```

### 2. `config.yaml` 文件（算法设置）

在`openjiuwen\extensions\context_evolver\`创建包含算法设置的 `config.yaml` 文件：

```yaml
# 算法选择: ACE (Agentic Context Engineering) or RB/REASONINGBANK (ReasoningBank) or REME
RETRIEVAL_ALGO: "REME" # ACE/RB/REME
SUMMARY_ALGO: "REME" # ACE/RB/REME
MANAGEMENT_ALGO: "REME" # ACE/RB/REME

USE_GOLDLABEL: true 

# MaTTS Configuration (Memory-aware Test-Time Scaling)
# Only applicable when ALGO=RB
MATTS_DEFAULT_K: 3 # Default scaling factor
MATTS_DEFAULT_TEMPERATURE: 0.9 # Default temperature for parallel scaling
MATTS_DEFAULT_MODE: "parallel"  # Options: none/parallel/sequential/combined

# ACE
USE_GROUNDTRUTH: true
MAX_PLAYBOOK_SIZE: 50

# RB
TOPK_QUERY: 1

# REME
# Retrieval
TOPK_RETRIEVAL: 10
LLM_RERANK: true
TOPK_RERANK: 5
LLM_REWRITE: true

# Extraction
MEMORY_VALIDATION: true
EXTRACT_BEST_TRAJ: true
EXTRACT_WORST_TRAJ: true
EXTRACT_COMPARATIVE_TRAJ: true

# Management (Pending Implementation)
MEMORY_DEDUPLICATION: true
MEMORY_UPDATE: true
DELETE_USAGE_THRESHOLD: 5
DELETE_UTILITY_THRESHOLD: 0.5

# Ours (Pending Implementation)
COMBINED_MATTS_PROMPT: "diversity" # Options : diversity/refine

# Logging
LOG_LEVEL: "INFO"
```

## 主要特性

- **多算法支持**：根据用例选择 ACE、ReasoningBank 和 ReMe 算法
- **语义记忆检索**：基于语义相似性和基于 LLM 的重排序检索相关记忆
- **轨迹总结**：从智能体交互中提取学习内容并自动存储为新记忆
- **MATTS 扩展**：用于多跳查询并行/顺序处理的记忆感知测试时扩展
- **按用户记忆管理**：每个用户隔离的记忆集合，支持添加/清除/检索操作
- **自动记忆注入**：无需代码更改即可用检索到的记忆增强智能体提示
- **向量存储集成**：内置语义相似性搜索的向量存储
- **文件持久化**：将记忆保存到 JSON 文件或从 JSON 文件加载，实现持久存储

## 架构

```
openjiuwen/extensions/context_evolver/
├── context_evolving_react_agent.py     # 记忆增强型 ReActAgent 子类
├── __init__.py                         # 公共 API 导出
├── config.yaml                         # 默认配置文件
├── service/
│   └── task_memory_service.py # 核心记忆服务（检索/摘要）
├── retrieve/task/             # 检索算法（ACE, RB, ReMe）
├── summary/task/              # 摘要算法（ACE, RB, ReMe）
├── schema/                    # 数据模型（memory, trajectory, io_schema）
├── tool/                      # 工具（如 wikipedia_tool）
└── core/                      # 核心工具（context, ops, vector store, file_connector）

# 快速入门示例
examples/context_evolver/
└── quickstart.py              # 新用户快速入门示例

# 测试位置：
tests/unit_tests/extensions/context_evolver/
├── test_retrieve_flow.py      # 检索流程测试
├── test_summary_flow.py       # 摘要流程测试
├── test_quickstart.py         # 快速入门测试
└── test_file_connector.py     # 文件连接器测试

```

## 组件

### ContextEvolvingReActAgent

一个 `ReActAgent` 子类，在处理查询前自动检索相关记忆。（示例代码可在 examples/context_evolver/quickstart.py中找到）

```python
from openjiuwen.extensions.context_evolver.service.task_memory_service import TaskMemoryService
from openjiuwen.extensions.context_evolver.context_evolving_react_agent import ContextEvolvingReActAgent, create_memory_agent_config
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.extensions.context_evolver.service.task_memory_service import AddMemoryRequest

# 步骤 1：创建记忆服务（共享实例）
memory_service = TaskMemoryService()

# 步骤 2：添加记忆（使用算法特定参数）
await memory_service.add_memory(
    user_id="test_user",
    request=AddMemoryRequest(
        when_to_use="当被问及数据库优化时",
        content="始终在频繁查询的列上使用索引。对于高流量应用考虑使用连接池。",
    ),
)

# 步骤 3：创建智能体卡片
agent_card = AgentCard(
    id="memory-react-agent",
    name="memory-react-agent",
    description="具有自动记忆注入的 ReActAgent"
)

# 步骤 4：创建 ContextEvolvingReActAgent 实例
agent = ContextEvolvingReActAgent(
    card=agent_card,
    user_id="test_user",
    memory_service=memory_service,
    inject_memories_in_context=True,  # 自动将记忆添加到提示中
)

# 步骤 5：使用辅助函数进行配置
agent_config = create_memory_agent_config(
    model_provider="OpenAI",
    api_key="your-api-key",
    api_base="https://api.openai.com/v1",
    model_name="gpt-5.2",
    system_prompt="你是一个有帮助的软件工程助手。"
                 "使用提供的记忆上下文来增强你的回答。",
)
agent.configure(agent_config)

# 步骤 6：使用自动记忆检索进行调用
result = await agent.invoke({
    "query": "如何优化我的数据库查询？",
})
output = result.get("output", "无输出")
memories_used = result.get("memories_used", 0)

print("\n" + "=" * 60)
print(f"   查询：'如何优化我的数据库查询？'")
print(f"   使用的记忆数：{memories_used}")
# 处理 Windows 控制台的 Unicode 编码
safe_output = output.encode('ascii', 'replace').decode('ascii')
print(f"   收到的响应：{safe_output}")


# 验证结果
test_passed = True
if not output or output == "无输出":
    print("\n   [失败] 未收到输出")
    test_passed = False
else:
    print("\n   [通过] 收到响应")
print("=" * 60)

```

#### 关键方法

| 方法 | 描述 |
|--------|-------------|
| `invoke(inputs, session)` | 使用自动记忆检索和注入调用智能体 |


### TaskMemoryService

处理记忆操作的核心服务。使用 openjiuwen 核心库：

- `openjiuwen.core.foundation.llm.model_clients.openai_model_client` 用于 LLM 调用
- `openjiuwen.core.retrieval.embedding.api_embedding` 用于嵌入

```python
from service.task_memory_service import TaskMemoryService, AddMemoryRequest

# 使用默认 config.yaml
service = TaskMemoryService(
    llm_model="gpt-5.2",
    embedding_model="text-embedding-3-small",
    api_key="your-api-key",
    retrieval_algo="ReMe",
    summary_algo="ReMe",
)

# 使用自定义配置文件路径
service = TaskMemoryService(
    llm_model="gpt-5.2",
    embedding_model="text-embedding-3-small",
    api_key="your-api-key",
    retrieval_algo="ReMe",
    summary_algo="ReMe",
    config_path="/path/to/custom/config.yaml",  # 可选：从自定义配置文件加载
)

# 检索
result = await service.retrieve(user_id, query)

# 摘要
result = await service.summarize(user_id, matts, query, trajectories, label)

# 添加记忆
result = await service.add_memory(
    user_id,
    request=AddMemoryRequest(
        content="记忆内容",
        # 算法特定字段：
        # - ReMe: when_to_use
        # - ReasoningBank: title, description
        # - ACE: section
    )
)
```

## 记忆算法

### ACE (Agentic Context Engineering)
- 使用 `content` 和 `section` 字段存储记忆
- 最适合：具有明确使用条件的行动导向型记忆
- 基于 Playbook 的组织方式

### RB (Reasoning Bank)
- 使用 `title`、`description` 和 `content` 字段存储记忆
- 最适合：具有丰富描述的知识导向型记忆
- 支持来源归属

### ReMe (Remember Me, Refine Me)
- 结合向量检索与基于 LLM 的重排序和重写
- 支持多阶段检索管道
- 最适合：需要语义理解的复杂查询

## 运行测试

测试位于 `tests/unit_tests/extensions/context_evolver/`。导航到项目根目录：

```bash
cd __CLONE_DIR__\agent-core
```

然后使用 pytest 运行测试：

```bash

# 运行特定测试文件
python -m pytest tests/unit_tests/extensions/context_evolver/test_retrieve_flow.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_summary_flow.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_file_connector.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_quickstart.py -v
```

## 与 openjiuwen 集成

此扩展与 openjiuwen agent-core 框架集成：

1. **作为智能体子类**：使用 `ContextEvolvingReActAgent` 进行自动记忆注入
2. **作为服务**：在自定义智能体中直接使用 `TaskMemoryService`
3. **作为 CLI 工具**：使用 `main.py` 进行独立记忆操作

## 许可证

版权所有 (c) 华为技术有限公司 2025。保留所有权利。
