# S_18 SubAgent 预设与生命周期

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/subagents/`（8 文件）、`openjiuwen/harness/subagent_lifecycle.py`、`openjiuwen/harness/manifest/harness_elements.py`（subagent 构建器） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的**预设子代理**及其生命周期辅助：五类预置 agent 的配置构建、
创建、任务资源生命周期。`S_10` 是异步运行时（状态机 / 容量 / snapshot），本 spec 是
预设的**声明 + 创建 + 同步生命周期**。

具体覆盖：

- `subagents/browser_agent.py`：`build_browser_agent_config` / `create_browser_agent`。
- `subagents/code_agent.py`：`build_code_agent_config` / `create_code_agent`。
- `subagents/research_agent.py`：`build_research_agent_config` / `create_research_agent`。
- `subagents/verification_agent.py`：`build_verification_agent_config` /
  `create_verification_agent`。
- `subagents/mobile_gui_agent.py`：`build_mobile_gui_agent_config` /
  `create_mobile_gui_agent`。
- `subagents/explore_agent.py` / `plan_agent.py`：explore / plan 预设（`S_12` 的
  `build_explore_subagent` / `build_plan_subagent` 消费）。
- `subagent_lifecycle.py`：`prepare_subagent_task_resources` / `cleanup_subagent_task_resources`。
- `subagents/__init__.py` 导出面（`__all__` 10 符号）。

不在本规约范围内：
- 异步运行时（spawn/wait/status/snapshot）—— `S_10`。
- 子代理工具面（`SubagentSpawnTool` 等）—— `S_05`。
- manifest catalog 注册（`S_12`）与 rail 挂载（`S_04`）。

## 不变量

1. **五类预设 + 两个辅助**：预设 agent 共 5 类——browser / code / research /
   verification / mobile_gui；辅助 preset 由 manifest 的 `build_explore_subagent` /
   `build_plan_subagent` / `build_general_purpose_subagent` 提供。每类有
   `build_*_agent_config`（纯配置）+ `create_*_agent`（实例化）成对 API。
2. **`subagents/__init__.py` 的 `__all__` 是公开面**（10 个 build/create 函数）；
   辅助 preset 不在 `__all__`（走 `manifest` catal登记，`S_12`）。
3. **配置构建是纯函数**：`build_*_agent_config` 不含副作用、只读输入
   （`model` / `subagents` / `workspace` 等），产出 `SubAgentConfig`（`S_01` 配置形态）；
   `create_*_agent` 包装 `SubAgentConfig` → `DeepAgent`。
4. **任务资源生命周期辅助唯一**：`prepare_subagent_task_resources(subagent)` /
   `cleanup_subagent_task_resources(subagent)`（`subagent_lifecycle.py`）——`getattr`
   探测可选钩子（`prepare_task_resources` / `cleanup_task_resources`），awaitable 则
   await；cleanup **绝不允许遮蔽子代理结果**（异常仅 `logger.exception`）。
5. **code agent 装配约束**：`build_code_agent_config` 自带内置 plan agent 注入
   （`_inject_builtin_plan_agents`，`_has_agent` 防重复）+ 必带 rail 合并
   （`_merge_rails_with_required`）+ coding memory 目录解析（`_resolve_coding_memory_dir`）；
   这些是 code preset 的硬语义，新增 preset 不得绕过。
6. **browser agent 约束**：`build_browser_agent_config` 经
   `_resolve_runtime_settings` 解析浏览器运行时；`_browser_model_with_temperature(model, temp)`
   设置浏览器模型温度；`_coerce_browser_instance` 归一实例形态。
7. **同步创建路径**：`create_*_agent(config, ...)` 是同步（复用 `create_deep_agent` 的
   同步构造，`S_01` 不变量 3）；`enable_subagent_runtime` 时才进 `S_10` 的异步控制面。
8. **manifest 侧预设与 `subagents/` 预设同源**：`S_12` 的 `build_*_subagent` 是
   `subagents/` 预设的 catalog 注册形态；二者共享 `SubAgentSpec` 装配语义，不新造预设。

## 接口契约

```python
def build_browser_agent_config(...) -> SubAgentConfig
def create_browser_agent(...) -> DeepAgent
def build_code_agent_config(...) -> SubAgentConfig
def create_code_agent(...) -> DeepAgent
def build_research_agent_config(...) -> SubAgentConfig
def create_research_agent(...) -> DeepAgent
def build_verification_agent_config(...) -> SubAgentConfig
def create_verification_agent(...) -> DeepAgent
def build_mobile_gui_agent_config(...) -> SubAgentConfig
def create_mobile_gui_agent(...) -> DeepAgent

# subagent_lifecycle.py
async def prepare_subagent_task_resources(subagent: Any) -> None
async def cleanup_subagent_task_resources(subagent: Any) -> None
```

错误 / 返回语义：

- 预设备缺失必填输入（如 code agent 的 workspace）→ 抛 `ValueError` 族（构建期）。
- 同名子代理重复注入 → `_has_agent` 去重（不重复添加内置 plan agent）。
- `cleanup_subagent_task_resources` 内部异常 → `logger.exception` 后继续（不遮蔽结果）。

## 数据结构

### 预设一览

| preset | 构建 | 创建 | 关键装配 |
|---|---|---|---|
| browser | `build_browser_agent_config` | `create_browser_agent` | 浏览器 runtime settings、模型温度 |
| code | `build_code_agent_config` | `create_code_agent` | 内置 plan agent 注入、必带 rails、coding memory 目录 |
| research | `build_research_agent_config` | `create_research_agent` | 研究工具组 |
| verification | `build_verification_agent_config` | `create_verification_agent` | 验证 rail / 工具 |
| mobile_gui | `build_mobile_gui_agent_config` | `create_mobile_gui_agent` | GUI 操作工具组 |
| explore / plan / general_purpose | manifest `build_*_subagent` | （经 catalog） | 探索 / 计划 / 通用 |

### 生命周期钩子

| 钩子 | 时机 | 失败语义 |
|---|---|---|
| `prepare_task_resources` | 子代理 invoke 前 | 失败向上（invoke 中断） |
| `cleanup_task_resources` | 子代理结束后 | 仅记日志，不遮蔽结果 |

## 与其它 spec 的关系

- 异步执行进 `S_10` 控制面；spawn 类型名（`"browser_agent"` / `"verification_agent"`）
  与 `S_16` 的 sticky 白名单一致。
- 预设经 `manifest` catalog 注册 —— `S_12`；`SubAgentConfig` / `DeepAgentSpec.subagents`
  字段 —— `S_01`（`SubAgentSpec` 解析成 `SubAgentConfig`，装配见 `S_13`）。
- `SubagentRail` 挂载 / `create_subagent` 装配 —— `S_04` / `S_02`。
- `create_*_agent` 复用 `create_deep_agent` 构造流 —— `S_01`。
