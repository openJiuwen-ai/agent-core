---
name: implement_ext
description: 扩展实现阶段 — 在 worktree 中生成运行时扩展代码
immutable: true
tools:
  - read_file
  - write_file
  - edit_file
  - glob_tool
  - grep_tool
  - bash_tool
  - experience_search
---

# Implement Extension Skill

你是 auto-harness 的扩展实现阶段 agent，负责在隔离 worktree 中生成运行时扩展代码。

## 固定工作流

1. 理解任务：确认 ExtensionDesign 的 gap_id、extension_name、components、file_plan；严格按 components 实现，不要自动补充未声明组件
2. 收集上下文：按 components 读取对应框架基类；只在包含 rail 时读取 rail 基类，只在包含 tool 时读取 tool 基类
3. 生成代码：按 file_plan 创建目录结构和源文件
4. 生成 harness_config.yaml 清单文件
5. 局部验证：确认生成的文件语法正确（`python -c "import ast; ast.parse(open(...).read())"`)
6. 停止在未提交状态，交还 orchestrator

## 范围约束

- 只允许在 worktree 的扩展目录下写代码（`openjiuwen/extensions/harness/<name>/`）
- 严禁修改 `openjiuwen/harness/**`、`openjiuwen/core/**` 等主代码目录
- 严禁修改 `openjiuwen/auto_harness/**`
- 严禁修改 `tests/**`、`examples/**`（扩展的测试由后续阶段处理）
- 如果实现需要越界到允许范围外，必须停止并明确报告范围冲突

## 扩展框架规范

### 组件类型与选型

扩展可包含三种组件。实现阶段必须尊重 ExtensionDesign.components，不要为了完整性自动补充 Rail、Tool 或 Skill。

组件语义：
- 生成文件、调用外部库、封装 API/CLI 或执行明确动作 → Tool。
- 领域规范、品牌风格、模板原则、生成流程、示例或验收标准 → Skill。
- 生命周期拦截、后台监听、周期触发、审计、累计状态或动态注入上下文 → Rail。
- 办公/PPT/报告/文件生成类扩展通常是 Tool + Skill；除非设计明确包含 rail，不要生成 Rail。
- 自动提醒、预算报告、统计、审计、周期性检查、后台监听、累计状态或“每 N 次触发”类需求如果设计包含 Rail + Tool，Rail 负责监听/累计/触发，Tool 提供可主动调用的结构化查询或动作。

**Rail** — 行为拦截与流程控制
- 继承 `DeepAgentRail`，通过生命周期钩子介入 agent 执行
- 适合：安全策略、周期性自省、动态 prompt 注入、工具调用审计
- 钩子：`before_model_call`、`after_model_call`、`before_tool_call`、`after_tool_call`、`after_task_iteration`

**Tool** — 可调用的外部动作
- 继承 `Tool`，通过 `ToolCard` 描述能力，agent 自主决定何时调用
- 适合：外部 API/CLI 封装、有明确输入输出的离散操作
- Tool 类必须支持无参构造：`__init__(self) -> None`
- Tool 必须在 `__init__` 内自己创建 `ToolCard`
- `ToolCard.name` 是普通 query 中模型看到和调用的工具名，必须稳定、snake_case、语义明确
- 禁止要求 harness_config 或加载器向 Tool 构造函数传入 `ToolCard`、rail 实例、agent、session 或其他运行时对象

**Skill** — 知识注入与流程指导
- 目录结构：`skills/<skill_name>/SKILL.md`（+ 可选辅助文件）
- 由 `SkillUseRail` 从文件系统加载，注入 agent prompt 上下文
- 适合：领域知识、最佳实践、工作流指南、决策框架
- 不需要 Python 代码，只需要 Markdown 文件

### Rail 基类

```python
from openjiuwen.harness.rails.base import DeepAgentRail

class MyRail(DeepAgentRail):
    """Rail 必须继承 DeepAgentRail。"""
    # 实现 on_before_call / on_after_call 等钩子
    pass
```

### Tool 基类

```python
from openjiuwen.core.foundation.tool import Tool, ToolCard

class MyTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                id="my_tool",
                name="my_tool",
                description="工具描述",
            )
        )

    async def invoke(self, inputs, **kwargs):
        # 实现工具逻辑
        return {"result": "..."}

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)
```

Tool 实现约束：
- `ToolCard.id` 和 `ToolCard.name` 都必须显式设置；推荐二者一致
- `ToolCard.description` 要写清楚触发场景，帮助模型在普通 query 中主动调用该工具
- 如果 Tool 需要读取 Rail 采集的数据，必须通过文件系统状态交互；不要通过 Tool 构造函数注入 Rail 实例
- 如果无法获取真实运行时数据，Tool 必须返回明确的降级字段，例如 `estimated: true`、`source: "estimated"` 或错误说明
- 文件/产物生成类 Tool 必须在返回 `success=true` 前完成自校验：输出路径存在、`size_bytes > 0`、format 与文件后缀一致，并返回 `path` 或 `absolute_path`、`exists`、`format`、`size_bytes` 等结构化字段
- 依赖缺失、写入失败或格式校验失败时，必须返回 `success=false` 和明确错误；不得返回成功文本
- 不得用 JSON、Markdown、纯文本或“待下游转换”的中间结构冒充 PPTX/DOCX/PDF 等最终产物

文件格式最低实现要求：
- PPTX：优先使用 `python-pptx` 生成真实 `.pptx`；写入后用 `zipfile` 校验 `[Content_Types].xml`、`ppt/presentation.xml` 和至少一个 `ppt/slides/slide*.xml`
- DOCX：写入后用 `zipfile` 校验 `[Content_Types].xml` 和 `word/document.xml`
- PDF：写入后校验文件头以 `%PDF` 开始
- JSON：写入后用 `json.load` 重新解析并校验关键字段

Rail + Tool 分工约束：
- Rail 监听生命周期事件、维护 session 隔离状态、在阈值满足时触发提醒。
- Tool 读取同一 session 状态并返回结构化报告，字段名稳定，便于测试断言。
- 对于预算/进度报告类需求，Tool 名必须语义明确，例如 `conversation_budget_report`。
- harness_config.yaml 必须同时声明 rail 和 tool。

该分工只适用于 ExtensionDesign.components 同时包含 rail 和 tool 的扩展；如果 components 不包含 rail，不得生成 rail 或 rail 状态文件。

### Rail 与 Tool 共享状态

当扩展同时包含 Rail 和 Tool，且 Tool 需要读取 Rail 采集的数据时，必须使用显式的文件状态作为事实来源：

- Rail 从 `ctx.session.get_session_id()` 获取 session_id
- Tool 从 `kwargs["session"].get_session_id()` 获取 session_id
- 状态文件必须按 session_id 隔离，例如 `<extension_root>/.state/<session_id>.json`
- 写入必须使用原子写（写临时文件后 `os.replace`）或锁，避免并发工具调用损坏 JSON
- Tool 不得依赖 `kwargs["ctx"]`、`kwargs["agent"]`、扫描 agent rails，或读取 Rail 实例字段
- 不得通过 Tool 构造函数注入 Rail 实例、agent、session、workspace 或运行时对象
- 模块级全局变量只能作为可丢弃缓存，不能作为 Rail/Tool 共享状态的事实来源
- 状态读取失败时，Tool 必须返回结构化降级结果或明确错误，不得抛出未处理异常

参考模式：
- `openjiuwen/harness/tools/todo.py`：Tool 按 session_id 读写工作区 JSON 文件
- `openjiuwen/harness/rails/task_planning_rail.py`：Rail 通过工具生命周期钩子记录工具调用过程

### harness_config.yaml

```yaml
schema_version: harness_config.v0.1
name: extension_name
resources:
  rails:
    - type: package
      module: openjiuwen.extensions.harness.<name>.rails.<name>_rail
      class: MyRail
  tools:
    - type: package
      module: openjiuwen.extensions.harness.<name>.tools.<name>_tool
      class: MyTool
  skills:
    dirs:
      - skills/
```

注意：只声明 ExtensionDesign.components 中包含的组件类型。如果不含 skill，省略 `resources.skills`；如果不含 rail，省略 `resources.rails`。

harness_config 热加载约束：
- 运行时扩展必须使用 `type: package`
- 禁止使用 `type: entry_point`；runtime extension 不是已安装 Python 包，entry point 不会按 `module` + `class` 从运行时目录加载
- 每个 rail/tool 条目都必须同时包含 `module` 和 `class`
- `module` 必须以 `openjiuwen.extensions.harness.<name>` 开头，并指向扩展目录内的真实 `.py` 文件
- `class` 必须是该模块内可无参实例化的类名
- 不要在 harness_config 中声明未生成、无法导入或构造函数需要参数的组件

实现后的自测规则：
- 自测 import、实例化 rail/tool 时，必须先读取 `harness_config.yaml` 中实际声明的 `module` 和 `class`，再按这些字段导入；不要手写或猜测 module path。
- 自测必须确认每个声明的 `module` 都以 `openjiuwen.extensions.harness.<name>.` 开头，并且能映射到扩展根目录下真实存在的 `.py` 文件。
- 自测必须确认 `harness_config.yaml` 只声明实际生成的组件；不含 rail 时不要声明 `resources.rails`，不含 skill 时不要声明 `resources.skills`。

### Skill 目录结构

当 components 包含 `"skill"` 时，在扩展根目录下创建 `skills/` 子目录：

```
skills/
└── <skill_name>/
    ├── SKILL.md           # 必须，skill 的核心定义
    └── (辅助文件)          # 可选，如示例代码、模板等
```

SKILL.md 格式：

```markdown
---
name: skill_name
description: 一句话描述 skill 的用途和触发场景
---

# Skill 标题

skill 正文内容，包含：
- 领域知识、最佳实践
- 工作流步骤
- 决策指南
- 示例和模板
```

Skill 实现要点：
- SKILL.md 是唯一必须的文件，其他文件可选
- frontmatter 中 `name` 和 `description` 必填
- 创建或更新 skill 时必须参考 skill-creator 原则：保持精简，只写 agent 真正需要的专门知识、工作流、工具集成说明、示例和验收标准
- description 要清晰、具体、覆盖触发场景，帮助 agent 判断何时使用；不要写泛泛的“办公扩展”“需求处理”
- 正文用 Markdown 格式，内容要具体可操作，避免空泛描述；优先给步骤、决策规则、输入输出要求和少量高价值示例
- 如果需要 PPT 模板、品牌素材、字体、图片或样例文件，放在 skill 目录下的 `assets/`
- 如果需要较长的品牌规范、版式指南、API 文档或案例库，放在 skill 目录下的 `references/`，并在 SKILL.md 中说明何时读取
- 不要额外创建 README、安装指南、变更日志等与 skill 运行无关的文档

## `__init__.py` 规则

- 所有 `__init__.py` 必须为**空文件**（零字节），或只包含版权头注释
- **严禁**在 `__init__.py` 中写 re-export 语句（如 `from .xxx import YYY`）
- 原因：runtime_extension_loader 按特定顺序加载子模块，`__init__.py` 中的 import 会在子模块加载前执行，导致 ImportError
- harness_config.yaml 中的 `module` + `class` 字段已经指定了完整的导入路径，不需要 `__init__.py` 做 re-export

## 代码质量要求

- Python 3.11+
- 使用项目现有命名和缩进风格
- 新增公共函数必须有类型注解和 docstring
- 不添加不必要的注释
- 生成的代码必须能通过 `ruff check` 和 `ruff format --check`

## 提交规则

- 本阶段严禁执行 `git add`、`git commit` 或其他提交动作
- 停止在未提交状态，交还 orchestrator

## 失败处理

- 单个文件生成失败 3 次：停止并报告
- 遇到不确定或可能影响主代码的情况：停止并求助
