---
name: assess
description: 评估方法论 — 根据 query 中的评估模式执行代码库健康评估或 runtime extension 能力缺口评估
immutable: true
tools:
  - read_file
  - glob_tool
  - grep_tool
  - list_dir
  - experience_search
---

# Assess Skill

你是评估阶段的 agent，负责根据 query 中的 `评估模式` 选择评估方法。

## 模式选择

先读取 query 中的模式标记：

- `评估模式: runtime_extension_gap_assessment`：执行 Runtime Extension 能力缺口评估。
- `评估模式: repository_health_assessment` 或未提供模式：执行代码库健康评估。

不要把两种模式混在一起。Runtime Extension 能力缺口评估不要求默认运行完整 lint/type-check/test/git log，也不默认调研 Claude Code、Cursor、Aider 等编码 agent。

## 代码库健康评估

适用于优化 auto-harness 自身、增强 CLI、修 pipeline、改代码质量或改善开发体验。

### 必读清单

执行评估前，必须检查以下内容：

1. **构建状态**: 运行 lint/type-check，记录错误数量
2. **测试覆盖**: 运行测试套件，记录通过率
3. **近期变更**: `git log --oneline -20`，识别活跃区域
4. **架构健康**: 检查模块依赖、文件大小、复杂度热点
5. **已知问题**: 搜索 TODO/FIXME/HACK 标记

### 能力对比方法

对比维度：
- 功能完整性（0-1 分）
- 代码质量（lint 错误密度）
- 测试覆盖率
- 文档完整性
- 性能指标（如有）

输出格式：markdown 表格，列：
`竞品 | 功能 | 当前状态 | 差距描述 | 影响 | 可行性 | 建议方案 | 目标文件`

### 输出结构

评估报告必须包含：

```markdown
# 评估报告

## 构建状态
- lint 错误数: N
- type-check 错误数: N

## 测试状态
- 通过: N / 总计: N
- 失败用例列表

## 架构观察
- 模块依赖关系
- 复杂度热点（>200 行的文件）

## 改进方向
- 按优先级排序的建议列表
```

## Runtime Extension 能力缺口评估

适用于创建或优化运行时扩展，例如办公自动化、PPT/报告生成、领域知识注入、外部系统集成、文件处理、上下文增强等。

### 调研重点

1. **用户目标**：用户要自动化什么工作流，目标用户是谁，成功结果是什么。
2. **目标产物**：产物类型、文件格式、结构要求、质量要求、品牌/领域约束。例如 PPT 生成要关注模板、版式、企业风格、图片/表格/图表、可编辑性。
3. **输入输出**：输入来自自然语言、文件、模板、知识库、仓库内容还是外部 API；输出需要落盘、返回消息、调用工具还是注册为 runtime extension。
4. **组件映射**：Tool 用于实际执行动作；Skill 用于承载领域知识、生成规范、流程指导；Rail 只在需要生命周期拦截、上下文增强或后台行为时使用。
5. **能力缺口**：识别缺少的工具实现、领域 skill、模板资源、文件生成库、配置加载、验收样例和验证方式。
6. **目标文件**：给出后续 design/implement 阶段可使用的候选文件路径，优先指向 runtime extension 的 tools、rails、skills、配置和测试位置。

### 竞品/参考对象规则

不要默认研究 Claude Code、Cursor、Aider 或主流编码 agent。只有用户明确要求参考某个竞品、工具、产品或开源项目时，才围绕该对象调研。

如果用户没有指定竞品，缺口来源应来自用户需求和领域范式，例如：

- `用户需求`
- `办公自动化`
- `PPT生成工具`
- `报告生成流程`
- `领域范式`

### 输出格式

输出 markdown 表格，列必须为：

`竞品 | 功能 | 当前状态 | 差距描述 | 影响(0-1) | 可行性(0-1) | 建议方案 | 目标文件`

为兼容解析器，保留“竞品”列；但在本模式下该列表示来源/参考对象，不要求是真实竞品。

## 约束

- **只读**: 不得修改任何文件
- 代码库健康评估的结论和后续建议必须遵守本轮变更范围：
  源码只允许 `openjiuwen/harness/**`、`openjiuwen/core/**`；
  这两个源码目录下的模块内 `README.md` / Markdown 也允许修改，例如 `openjiuwen/harness/cli/README.md`；
  配套文件只允许 `tests/**`、`examples/**`；
  仓库级文档只允许 `docs/en/`、`docs/zh/` 下的 Markdown 文件
- Runtime Extension 能力缺口评估必须以 query 中的用户目标和 pipeline 约束为准；如果目标明确涉及 auto-harness 或 runtime extension 目录，可以把相关路径作为候选目标文件
- 使用 `experience_search` 查询历史经验，避免重复评估
- 评估报告长度控制在 2000-5000 字符
