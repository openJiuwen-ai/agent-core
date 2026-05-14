# agent_teams/docs

`openjiuwen.agent_teams` 子系统的设计归档目录。代码看 `git log` / 模块内 CLAUDE.md，
本目录只承载**两类长期文档**——规约（specs）与特性变更（features）。

## 目录用途

| 目录 | 用途 | 关联触发 |
|---|---|---|
| `specs/` | 模块设计规约：跨子模块的契约、协议、边界、不变量。**长期有效**，描述"系统是什么样"。 | 任何**模块设计规约变动**都必须同步更新对应 spec 文档；新规约 = 新 spec 文件 |
| `features/` | 特性变更归档：一次完整的设计/重构/新功能落地的来龙去脉。**只增不删**，描述"为什么这样改"。 | 每次特性更新提交代码前都必须归档；commit hash 写进文档元信息 |

`specs/` 是状态快照（描述当前），`features/` 是变更日志（描述过程）。当 spec 改了，
通常对应一份 feature 文档解释"为什么改成这样"。

## 命名规约

```
specs/    S_NN_<slug>.md
features/ F_NN_<slug>.md
```

- 前缀 `S_` / `F_` 表明文档类型，grep / IDE 列表可视识别
- `NN` 是两位**递增序号**（`01`、`02`、…），目录内已有 N 个文档时新文档编号 `N+1`；不复用、不回填空缺
- `<slug>` 短横线分隔的英文小写 slug，描述主题（如 `coordination-protocol-cleanup`、`session-state-machine`）
- 文件名末尾不带日期——日期写在文档元信息表里

不允许：
- 序号跳号（除非中间文档被显式删除——但 features 不允许删除）
- 大写字母、下划线（`_` 仅用于前缀分隔）、空格
- 未带 `S_` / `F_` 前缀的散落 markdown

## 内容约定

### specs/ 文档结构（建议骨架）

```markdown
# <Spec 名称>

## 元信息
| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/agent_teams/<path> |
| 最近一次修订日期 | <YYYY-MM-DD> |
| 关联 feature | F_NN_<slug>.md（可选） |

## 范围 / 边界
（这个规约管什么、不管什么）

## 不变量
（系统在任意时刻必须为真的事实）

## 接口契约
（公共 API 形态、参数语义、错误语义）

## 数据结构
（关键状态字段及其生命周期）

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
| 测试基线 | <pytest 结果> |
| Refs | #<issue> |

## 背景
（这个改动为什么必要——上下文、历史、痛点）

## 数据结构 / 状态机
（如果改动触及数据模型，画清楚）

## 决策
（选了什么方案，每条决策对应代码层面的体现）

## 拒绝的方案
（评估过但没选的方案 + 为什么没选——避免后人重蹈覆辙）

## 验证
（测试 / lint / 行为基线）

## 已知遗留
（这次没做但下次应该做的 follow-up）
```

可以根据情况增减小节，但 **"决策"和"拒绝的方案"是 features 的核心**，缺它们等于
没归档——commit message 已经写了 what，feature 文档要写 why-not。

## 与其它 CLAUDE.md 的关系

- `openjiuwen/agent_teams/CLAUDE.md`：模块入口索引 + 公开 API + 架构铁律 + **本目录的归档强制约束**
- `openjiuwen/agent_teams/<subdir>/CLAUDE.md`：子模块本地规则；如果某条规则跨子模块，落到 `specs/` 而不是塞到一份子目录 CLAUDE.md
- 本 `docs/CLAUDE.md`：只讲归档结构与命名规约，**不重复模块设计内容**——内容写在对应 `S_*` / `F_*` 文档里

## 反模式

- **把 commit message 复制粘贴当 feature 归档**：feature 要写 commit 写不下的东西（拒绝的方案、设计思路、未来 follow-up），不是 commit 的拷贝
- **靠 docs 维护代码行为**：实现行为是代码 + 单测的事；docs 描述意图与决策，不是 source of truth
- **不更新 specs**：如果模块契约改了但对应 spec 没改，下次重构时读 spec 的人会被误导。提交前自查
- **N 多个小 feature 文档**：一次连贯的设计变动归档为**一份** feature 文档，不要拆碎；多 commit 落地的特性，元信息里把 commit hash 都列上
