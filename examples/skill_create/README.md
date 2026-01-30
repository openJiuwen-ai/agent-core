# Skill Creator

使用 LLM 智能生成和优化 AI Agent Skills。

## 快速开始

```python
from openjiuwen.dev_tools.skill_creator import SkillCreator
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

# 配置模型客户端
client_config = ModelClientConfig(
    client_provider="SiliconFlow",  # 支持 OpenAI, SiliconFlow
    api_key="your-api-key",
    api_base="siliconflow_url",
    verify_ssl=False,
)

# 配置模型请求参数
request_config = ModelRequestConfig(
    model="Pro/zai-org/GLM-4.7",
    temperature=0.7,
)

# 创建 SkillCreator 实例
creator = SkillCreator(
    model_client_config=client_config,
    model_request_config=request_config,
)

# 创建新 Skill
skill = await creator.generate(
    mode="create",
    name="my-new-skill",
    description="A skill for handling data analysis tasks",
    output_path="./skills",
    skill_type="workflow"
)

# 优化现有 Skill（默认全面优化）
result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill",
    auto_apply=True  # 自动保存更改
)

# 指定优化方向进行针对性优化
result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill",
    optimization_direction="优化 workflow 流程，使其更加清晰简洁，并增加错误处理步骤",
    auto_apply=True
)

# 查看优化结果
for change in result.changes:
    print(f"  ✓ {change}")
```

## API 参考

### SkillCreator()

创建 SkillCreator 实例，需要传入模型配置。

```python
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

client_config = ModelClientConfig(
    client_provider: str,    # 提供商：OpenAI, SiliconFlow
    api_key: str,            # API 密钥
    api_base: str,           # API 地址
    verify_ssl: bool,        # 是否验证 SSL，默认 True
)

request_config = ModelRequestConfig(
    model: str,              # 模型名称
    temperature: float,      # 温度参数，默认 0.7
)

creator = SkillCreator(
    model_client_config=client_config,
    model_request_config=request_config,
)
```

### creator.generate()

统一的生成/优化方法，通过 `mode` 参数控制操作类型。

```python
result = await creator.generate(
    mode: str,                          # "create" 创建新 Skill，"optimize" 优化现有 Skill
    
    # 创建模式参数 (mode="create")
    name: str,                          # Skill 名称 (hyphen-case)
    description: str,                   # Skill 描述/需求
    output_path: str,                   # 输出目录
    skill_type: str = "workflow",       # 类型：workflow/task/reference/capabilities
    
    # 优化模式参数 (mode="optimize")
    skill_path: str,                    # Skill 目录路径
    optimization_direction: str = None, # 优化方向说明（可选）
    auto_apply: bool = False,           # 是否自动保存更改
)
```

**返回值：**
- 创建模式 (`mode="create"`): 返回 `SkillContent`
- 优化模式 (`mode="optimize"`): 返回 `SkillOptimizationResult`

### 创建模式参数说明

**skill_type 参数说明：**

| 类型 | 适用场景 | 结构特点 |
|------|----------|----------|
| `workflow` | 顺序流程、步骤明确的任务 | Overview → Step 1 → Step 2 → Step 3... |
| `task` | 工具集合、多种操作 | Overview → Quick Start → Task 1 → Task 2... |
| `reference` | 标准规范、指南文档 | Overview → Guidelines → Specifications → Usage |
| `capabilities` | 多功能集成系统 | Overview → Core Capabilities → Feature 1 → Feature 2... |

**创建示例：**
```python
# 创建数据分析流程 Skill（顺序步骤）
skill = await creator.generate(
    mode="create",
    name="data-analysis",
    description="Analyze Excel files step by step",
    output_path="./skills",
    skill_type="workflow"  # 适合有明确步骤的流程
)

# 创建文件处理工具集 Skill（多种操作）
skill = await creator.generate(
    mode="create",
    name="file-processor",
    description="Various file processing operations",
    output_path="./skills",
    skill_type="task"  # 适合多种独立操作
)

# 创建编码规范 Skill（参考文档）
skill = await creator.generate(
    mode="create",
    name="coding-standards",
    description="Company coding guidelines",
    output_path="./skills",
    skill_type="reference"  # 适合规范文档
)
```

### 优化模式参数说明

**optimization_direction 参数说明：**
- 不提供时，按照默认规则进行全面优化（description、workflow、移除占位符等）
- 提供时，按照用户指定的方向进行针对性优化

**优化示例：**
```python
# 默认全面优化
result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill"
)

# 指定优化方向
result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill",
    optimization_direction="优化 workflow 流程，使其更加清晰简洁"
)

result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill",
    optimization_direction="改进 description，突出数据分析和报表生成能力"
)

result = await creator.generate(
    mode="optimize",
    skill_path="./skills/my-skill",
    optimization_direction="增加错误处理步骤，并添加更多代码示例",
    auto_apply=True
)

# 优化结果
# result.original    - 原始内容
# result.optimized   - 优化后内容
# result.changes     - 变更列表
```

## 目录结构

```
skill_creator/
├── __init__.py          # 模块入口
├── base.py              # 基础数据结构
├── skill_creator.py     # 核心实现
├── use_example/         # 使用示例
│   └── skill_create_use.py
└── README.md            # 文档
```

## LLM 配置

使用 `openjiuwen.core.foundation.llm` 模块的配置类：

- `ModelClientConfig`: 模型客户端配置
  - `client_provider`: 支持 `OpenAI`, `SiliconFlow`
  - `api_base`: API 地址
  - `api_key`: API 密钥
  - `verify_ssl`: 是否验证 SSL

- `ModelRequestConfig`: 模型请求配置
  - `model`: 模型名称
  - `temperature`: 温度参数
