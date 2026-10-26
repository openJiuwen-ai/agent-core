# tool_management/docs

**Harness 工具管理**（`openjiuwen/harness/tool_management/`）的设计归档目录。代码历史看 `git log`；模块入口规则见 [`../AGENTS.md`](../AGENTS.md)。仓库 L0：[`AGENTS.md`](../../../../AGENTS.md)。

## 目录用途

| 目录 | 用途 | 何时动 |
|------|------|--------|
| `specs/` | 长期契约：边界、不变量、装配。**当前态。** | 任何模块设计 / 公开行为变更 → 更新或新增 `S_NN_*` |
| `designs/` | 跨切面图与叙事总览（单独不作规范源）。 | 架构转向；从 spec 链接 |
| `features/` | 特性级变更日志（`F_NN_*`）。**只增不删。** | 决策、拒绝方案、验证基线 |
| `bugfix/` | 可选：非 feature 级 fix 短笔记（团队选用时）。 | 少见；契约变更优先 `F_*` |

`specs/` = 系统**是什么**；`features/` = **为什么**这样改。

## 命名规约

```
specs/     S_NN_<slug>.md
features/  F_NN_<slug>.md
designs/   <descriptive_slug>.md   （无 S_/F_ 前缀）
```

- `NN` — 该目录内两位递增序号；勿复用编号。
- `<slug>` — 小写英文、短横线分隔。
- 日期写在文档**元信息表**，不写进文件名。
- 元信息**勿写** commit hash（仅 `YYYY-MM-DD`）。

当前 spec：`specs/` 下 `S_01` … `S_06`。

## Spec 骨架（建议）

```markdown
# <标题>

## 元信息
| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/harness/tool_management |
| 最近一次修订日期 | YYYY-MM-DD |
| 关联 feature | F_NN_<slug>.md（可选） |

## 范围 / 边界
## 不变量
## 接口契约
## 执行路径或数据结构
## 修改检查单
## 与其它 spec 的关系
```

## Feature 骨架（建议）

须含 **决策** 与 **拒绝的方案**——不要复制 commit message 全文。

## 提交约定（scope：`tool-mgmt`）

变更触及 `F_*` 或 `S_*` 时，在同一 PR / 工作单元内拆成 **三个连续提交**：

1. **实现** — `feat(tool-mgmt): …` / `fix(tool-mgmt): …` / `refactor(tool-mgmt): …`
2. **测试** — `test(tool-mgmt): …`
3. **文档** — `docs(tool-mgmt): …`（新增 `F_*` + 任何 `S_*` 修订）

勿把大段文档 diff 与特性代码混在同一 commit。Footer：`Refs: #<issue>`（适用时）；issue 号不明须问用户。

## 与其它文档的关系

| 位置 | 角色 |
|------|------|
| [`../AGENTS.md`](../AGENTS.md) | L1 索引、铁律、双仓表、测试命令 |
| `openjiuwen/harness/docs/` | Harness 级 feature（如 OTEL ask-user）——**非** tool-mgmt |
| `docs/en\|zh/...` | 终端用户 Development Guide——仅引用 |
| jiuwenswarm `agents/harness/common/tool_management/` | Fork 管辖：`tools_search` / `invoke_tool` / `DisabledToolsRail` |

## 反模式

- 仅用 commit message 充当 feature 归档。
- 把 markdown 当运行时真相源（代码 + 单测优先）。
- 改 progressive / MCP / resilience 行为却不更新对应 `S_*`。
- 把 swarm fork 工具名写成 agent-core 默认就存在。
