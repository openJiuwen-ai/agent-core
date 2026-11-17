# context_engine/docs

`openjiuwen.core.context_engine` 子系统的设计归档目录。代码看 `git log` / 模块内 [`AGENTS.md`](../AGENTS.md)；
本目录只承载**规约（specs）**与**特性变更（features）**。

## 目录用途

| 目录 | 用途 | 关联触发 |
|------|------|----------|
| `specs/` | 模块设计规约：跨处理器的契约、窗口语义、rail 边界、不变量。**长期有效**。 | 任何**模块设计规约变动**都必须同步更新对应 spec；新规约 = 新 spec 文件 |
| `features/` | 特性变更归档：一次完整设计/重构/新功能落地的来龙去脉。**只增不删**。 | 特性更新提交代码前归档；日期写进元信息 |
| `designs/` | 短叙事总览；指向用户-facing `Context Engine.md` 做教程 | 架构叙事变更时修订，不替代 specs |
| `bugfix/` | 可选：复杂 bugfix 决策留档（非强制每条 bug 都归档） | 根因跨模块或易复发时新增 |

`specs/` 描述当前态，`features/` 描述变更过程。spec 改了通常对应一份 feature 解释 why。

## 命名规约

```
specs/    S_NN_<slug>.md
features/ F_NN_<slug>.md
designs/  <slug>.md（无 S_/F_ 前缀）
```

- 前缀 `S_` / `F_` 便于 grep
- `NN` 两位递增序号（本目录已占用 `01`–`06`；新 spec 用 `07` 起）
- `<slug>` 小写短横线英文
- 文件名末尾不带日期——日期写在文档元信息表

不允许：跳号（除非显式删除 spec——features 不允许删）、大写 slug、无 `S_`/`F_` 的散落 spec/feature markdown。

## 内容约定

### specs/ 文档结构（建议骨架）

```markdown
# <Spec 名称>

## 元信息
| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/core/context_engine/... |
| 最近一次修订日期 | YYYY-MM-DD |
| 关联 feature | F_NN_<slug>.md（可选） |

## 范围 / 边界
## 不变量
## 接口契约
## 数据结构
## 与其它 spec 的关系
```

### features/ 文档结构（建议骨架）

```markdown
# <Feature 名称>

## 元信息
| 项 | 值 |
|---|---|
| 日期 | YYYY-MM-DD |
| 范围 | <修改路径> |
| 测试基线 | pytest 路径或摘要 |
| Refs | #<issue> |

## 背景
## 决策
## 拒绝的方案
## 验证
## 已知遗留
```

**「决策」与「拒绝的方案」是 features 核心**——commit message 写 what，feature 写 why-not。

## 提交约定

涉及本目录（`F_*` / `S_*` / `designs/`）的特性改动，**特性代码、测试代码、文档必须拆成三个连续提交**：

1. **提交 1 — 特性代码**：`feat(context): <实现>`（或 `fix` / `refactor`）。
2. **提交 2 — 测试**：`test(context): ...`。
3. **提交 3 — 文档**：`docs(context): ...`（本次全部 `F_*` 新增与 `S_*` 修订）。

三提交须**连续紧邻**于同一 PR/工作单元。细则与双向同步见 [`../AGENTS.md`](../AGENTS.md)「设计文档归档与双向同步」。

## 与用户 API 文档的关系

- 用户教程：`docs/en|zh/.../Context Engine.md`
- API 参考：`docs/en|zh/.../openjiuwen.core/context_engine*`

本 archive **不**复制参数表与 processor 用法大全；spec 只写 agent 改代码需要的**契约与不变量**。API 行为变更时由作者同步官方 API 页。

## 与其它 AGENTS.md 的关系

- [`../AGENTS.md`](../AGENTS.md)：模块入口、铁律、双仓表、测试路径
- [`../../harness/rails/context_engineer/AGENTS.md`](../../../harness/rails/context_engineer/AGENTS.md)：rail L2 索引（可选短规约）
- 本文件：只讲归档结构与命名，**不重复** spec 正文

## 反模式

- 把 commit message 粘贴当 feature 归档
- 用 docs 代替单测作为行为真相源
- 契约改了不更新 `S_*`
- 一次设计拆成多份碎 feature（一次连贯变动 = 一份 `F_*`）
- 在 spec 元信息写 commit hash
