# Harness 开发文档约定

原规则迁自 `openjiuwen/harness/docs/AGENTS.md`。Harness 的长期文档位于仓库根 `.agents/agent-docs/specs/harness/` 与 `.agents/agent-docs/features/harness/`；代码看 `git log` / 模块内 AGENTS.md。

## 目录用途

| 目录 / 文件 | 用途 | 关联触发 |
|---|---|---|
| `specs/` | 模块设计规约：跨子模块的契约、协议、边界、不变量。**长期有效**，描述"系统是什么样"。 | 任何**模块设计规约变动**都必须同步更新对应 spec；新规约 = 新 spec 文件 |
| `features/` | 特性变更归档：一次完整的设计/重构/新功能落地的来龙去脉。**只增不删**，描述"为什么这样改"。 | 每次特性更新提交代码前都必须归档；归档日期写进文档元信息 |

`specs/` 是状态快照（描述当前），`features/` 是变更日志（描述过程）。当 spec 改了，通常对应一份 feature 文档解释"为什么改成这样"。

## 命名与位置

- spec：`.agents/agent-docs/specs/harness/S_NN_<slug>.md`，沿用已有编号；新增时取历史最大已用编号加一，不复用旧号。
- feature：`.agents/agent-docs/features/harness/F_YYYYMMDD_HHMM_<slug>.md`，时间采用 Asia/Singapore，精确到分钟。
- slug 使用小写英文与短横线，创建后标识不变；完整名称重名时确认是否同一记录，否则细化主题，不能覆盖。
- 已有文件不重命名。修订 spec 更新最近修订日期，feature 记录归档日期；不分配新的 feature 递增号。

## 内容约定

### specs/ 文档结构（统一骨架）

原入口引用的 `spec-design-methodology.md` 并不存在；使用 [spec-template.md](spec-template.md) 作为当前写作参考，保留以下模块要求：
`元信息 → 范围/边界 → 不变量 → 接口契约 → 数据结构 → 与其它 spec 的关系`。
**硬要求**：
- 「关联模块」路径必须真实存在，引用符号必须能在代码里 grep 到。
- 「不变量」编号，供其它章节/其它 spec 引用。
- spec 之间**只引用、不包含**——不在本 spec 里重复别份 spec 的内容。
- 只描述**当前**系统，不写"未来要做"。

### features/ 文档结构（统一骨架）

`元信息(日期/范围/测试基线/Refs) → 背景 → 数据结构/状态机 → 决策 → 拒绝的方案 → 验证 → 已知遗留`。
**"决策"和"拒绝的方案"是核心**——缺它们等于没归档。

## 提交约定

涉及本目录文档（`F_*` / `S_*`）更新的特性改动，**拆成三个连续紧邻提交**：

1. **提交 1 — 特性代码**：实现，`feat(harness): ...`（或 `fix` / `refactor` 等对应 type）。
2. **提交 2 — 测试代码**：本次新增 / 改动的单测，`test(harness): ...`。
3. **提交 3 — 文档**：本次涉及的 `features/F_YYYYMMDD_HHMM_*.md` 新增与 `specs/S_NN_*.md` 修订，`docs(harness): ...`。

三个提交必须连续紧邻、同一工作单元内落地。scope / footer 等其余约定见 `openjiuwen/harness/AGENTS.md` 的「提交约定」。这是硬约束。

## 与其它 AGENTS.md 的关系

- 仓库根 `AGENTS.md`：仓库级入口 + 公共 API + 提交约定。
- `openjiuwen/harness/AGENTS.md`：harness 模块入口索引 + 公开 API + 架构铁律 + **本目录的归档强制约束**。
- `openjiuwen/harness/rails/AGENTS.md`：rails 子模块本地规则（rail 基类、事件路由、priority）。
- 本 reference 保留 harness 专属归档要求，通用开发记忆与路径约定见上级 `SKILL.md`；模块设计内容写入对应 spec / feature。

## 反模式

- **把 commit message 复制粘贴当 feature 归档**：feature 要写 commit 写不下的东西（拒绝的方案、设计思路、未来 follow-up）。
- **靠 docs 维护代码行为**：实现行为是代码 + 单测的事；docs 描述意图与决策，不是 source of truth。
- **不更新 specs**：模块契约改了但对应 spec 没改，下次重构时读 spec 的人会被误导。
- **N 多个小 feature 文档**：一次连贯的设计变动归档为**一份** feature 文档，不要拆碎。
- **在元信息里写 commit hash**：spec / feature 元信息只写日期（`YYYY-MM-DD`）。

