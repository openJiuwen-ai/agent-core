# harness specs 设计方法论（Spec Design Methodology）

> 本文档把 `openjiuwen/agent_teams/docs/specs/` 的规格设计理念提炼为一套**可复用方法**，
> 用于为任意 Python 模块（以 `openjiuwen/harness` 为第一个落地样例）设计解耦的 specs。
> 它回答三个问题：**一套好的 specs 长什么样、为什么、怎么写**。

## 0. 一句话

**一份 spec = 一个关注面（concern）。spec 之间只互相引用、绝不互相包含；架构上解耦到什么程度，
文档上就解耦到什么程度——文档结构是代码模块边界的镜像，边界外的一切都推到"与其它 spec 的关系"。**

## 1. 核心设计理念

### 1.1 文档解耦 = 架构解耦的镜像

`agent_teams` 的 24 份 specs 之所以能各写各的、长期不烂，是因为它们的**切分方式**与代码模块
边界一一对应：

| 理念 | 含义 | 代码表征 |
|---|---|---|
| **单一关注面** | 一份 spec 只定义一件事：要么是公开 API 表面，要么是运行时协议，要么是数据模型 | 一个 spec 只挂一个代码子模块（或一组相邻文件） |
| **边界外推** | spec 开头就写"**不管**什么"，把不属于自己的全部推给别的 spec | `范围/边界` 一节列出 `见 S_NN` |
| **不变量优先** | spec 的核心是"任意时刻必须为真"的硬事实，不是功能清单 | 可被测试/断言强制的事实 |
| **引用不包含** | spec 之间只出现关系（`见 S_NN`），内容绝不粘贴重复 | 同一符号只在一份 spec 里被完整定义 |
| **接口契约** | 公共 API 形态、参数/错误语义落在 spec，实现细节留在代码 | `__init__.py` 的 `__all__` = 公开表面 |
| **数据结构** | 关键状态字段及其生命周期独立成节 | `@dataclass`/pydantic 模型字段表 |

### 1.2 为什么 spec 之间必须"不耦合"

- **单一源头**：一个契约（比如 `DeepAgentConfig` 字段）只在一处完整定义，改契约只改一处。
- **可并行**：多人/多 agent 并行改不同 spec 永不冲突——它们没有共同文档片段。
- **可独立演进**：一个子模块重构不影响其它 spec 的语义，只更新自己的「与其它 spec 的关系」。
- **可验证**：每个不变量都能对应到具体测试或静态检查，spec 不是散文。

### 1.3 「相互结构」的正确读法

spec 之间不是靠"嵌套、拷贝、重复"来相互支撑，而是靠**关系引用**（graph，不是 tree）：

```
S_01 公开 API ──> 引用 ──> S_02 架构 ──> 引用 ──> S_03 任务循环
                        └──> 引用 ──> S_04 Rails
S_05 工具契约 ──> 引用 ──> S_04
```

每份 spec 的「与其它 spec 的关系」一节就是它的出边；整个 specs 目录是一个**有向无环图**，
环 = 设计缺陷（说明关注面没有切干净）。

## 2. 一份 spec 的标准骨架

```markdown
# S_NN <名称>

## 元信息
| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | <代码路径，必须真实存在> |
| 最近一次修订日期 | <YYYY-MM-DD> |
| 关联 feature | <F_NN 或 N/A> |

## 范围 / 边界
（管什么 + 不管什么；不管的部分用 `见 S_NN` 指出去向）

## 不变量
（任意时刻必须为真；每条最好是"可被测试/断言"的硬事实，
编号 1..n，方便引用）

## 接口契约
（公共 API 形态、参数语义、错误语义；只列真实存在的符号）

## 数据结构
（关键状态字段及其生命周期；表格：字段/设置时机/清空时机/备注）

## 与其它 spec 的关系
（本 spec 的出边；其它 spec 怎么引用本 spec 不用写）
```

## 3. 切分 specs 的决策规则

1. **一个代码子模块 = 一个 spec**（若子模块足够重要）。
2. 跨子模块的**公共协议**（如公开 API、配置流、事件流）单独成 spec。
3. 小到不值得独立成 spec 的**外围工具**也要单独成 spec——宁可多不可合并
   （这是 `harness` 落地的明确要求）。
4. **从属不覆盖**：如果 A 是 B 的实现细节，A 单独成 spec 时 B「不管」的部分写 `见 S_AA`。

### harness 落地切分（18 份）

| 序号 | spec | 代码模块 | 关注面 |
|---|---|---|---|
| S_01 | 公开 API 与构造流 | `__init__.py`, `factory.py`, `schema/config.py`, `extension_binder.py` | 公开表面 + Config→create→DeepAgent 单向流 |
| S_02 | DeepAgent 架构 | `deep_agent.py` | agent 生命周期、Runner 入口、事件装配 |
| S_03 | TaskLoop | `task_loop/` | 任务循环事件体系、handler/executor/coordinator |
| S_04 | Rails 契约 | `rails/` | rail 基类、事件路由、priority、两套拦截 |
| S_05 | 工具契约 | `tools/` | 工具注册/描述/tool_discovery/todo/plan-mode |
| S_06 | Prompts 与 i18n | `prompts/` | SystemPromptBuilder、语言、sections、attachments |
| S_08 | 安全引擎 | `security/` | PermissionEngine、tiered policy、file guard |
| S_09 | Workspace | `workspace/` | Workspace、directory_builder、cwd/project_root |
| S_10 | SubAgent 运行时 | `subagent_runtime/`, `subagents/`, `subagent_lifecycle.py` | 子代理 spawn/wait/control/status |
| S_11 | Goal 与评估 | `goal/` | Goal 状态机、评估器、stop 策略 |
| S_12 | Manifest 声明式装配 | `manifest/` | element/catalog/factory_ref 声明式装配 |
| S_13 | 资源包加载（Plugin/AgentTemplate） | `resources/`, `extension_binder.py` | 包加载/解析/绑定/回滚 |
| S_14 | LSP 集成 | `lsp/` | LSP manager/client/diagnostics |
| S_15 | CLI 与配置 | `cli/` | Click 入口、settings 三层优先级、agent/prompts/rails/ui |
| S_16 | KV Cache 亲和钩子 | `kv_cache/` | KVC 亲和判定、sticky 白名单、子会话键 |
| S_07 | Skill 库开关状态 | `skills/` | skills_state.json 解析、collect_disabled_skills |
| S_17 | Personal Context | `personal_context/` | 个人上下文图、pipeline、source metadata |
| S_18 | Subagent 预设与生命周期 | `subagents/`, `subagent_lifecycle.py` | 五类预设 agent + lifecycle 辅助 |

## 4. 写作红线（Do/Don't）

### Do
- 每个「关联模块」路径必须 `Test-Path` 通过（写完后验证）。
- 「不变量」每条加编号 `1.` `2.`…… 并在其它章节引用（`不变量 3`）。
- 「接口契约」只写**代码里真实存在**的符号；签名从 `def` / `class` 抄，不凭记忆。
- 「与其它 spec 的关系」每条写明**方向**（本 spec 依赖谁 / 谁引用本 spec）。
- 数据字段表至少覆盖：字段名、设置时机、清空时机、备注。

### Don't
- 不复制其它 spec 的内容（重复 = 未来漂移）。
- 不写 `commit hash`、不写只存在于历史版本的符号。
- 不外推"未来要做"的事——spec 是状态快照，描述**当前**系统。
- 不把 feature 的"为什么"塞进 spec——那是 `features/F_NN` 的职责。

## 5. 与 features 的对偶

| | specs | features |
|---|---|---|
| 语义 | 系统**是什么样**（状态快照） | 系统**为什么变成这样**（变更日志） |
| 生命周期 | 长期有效，随契约修订 | 只增不删 |
| 命名 | `S_NN_<slug>.md` | `F_NN_<slug>.md` |
| 编号 | 递增，不复用不跳号 | 同左 |
| 更新触发 | 模块契约、公共协议、不变量变化 | 每次特性更新提交前 |
| 缺了会怎样 | 下次重构读 spec 的 agent 被误导 | 设计上下文丢失 |

**归档节奏（harness 采用，承袭 agent_teams）**：特性改动 = 三个连续紧邻提交
① 代码 `feat(harness):` → ② 测试 `test(harness):` → ③ 文档 `docs(harness):`。
spec 修订走第 ③ 提交，与对应 feature 文档同批落地。

## 6. 验证清单（一份 spec 写完后自查）

- [ ] 关联模块路径全部存在
- [ ] 引用的符号在代码里 grep 得到
- [ ] 不变量编号连续、可被引用
- [ ] 没有把别份 spec 的内容粘贴进来
- [ ] 「与其它 spec 的关系」无环
- [ ] 元信息日期 = 今天
