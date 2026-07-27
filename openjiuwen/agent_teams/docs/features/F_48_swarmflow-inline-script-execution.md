# Swarmflow 内联 script 执行

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-01 |
| 范围 | `workflow/engine/loader.py`、`workflow/runner.py`、`workflow/tool_swarmflow.py`、`i18n.py`（`swarmflow.launched` 加 `script_path`）、`tools/locales/{cn,en}.py`、`tools/locales/descs/{cn,en}/swarmflow.md` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 108 passed |
| Refs | #751 |

## 背景

`SwarmflowTool` 的脚本来源是四选一（`script_path` / `script` / `name` / `resume_id`）。
此前**只有 `script_path`（磁盘文件）接通执行**，其余三源在 `invoke` 一律返回
"not supported yet"。这意味着即便是编排结构一眼看清的简单任务，也必须先把脚本写到磁盘、
再把路径传进来——多一趟落盘、命名、清理的摩擦。

本次接通**内联 `script`（源码字符串）**，让简单场景可以直接把脚本源码传进来跑，不必先落盘；
复杂场景仍走 `swarmskill-creator` 产出磁盘脚本 + `script_path`。`name` / `resume_id` 执行层
仍未接通（保持 "not supported yet"）。

## 决策

1. **内联源码落盘后复用 path-based 全链路，而非为字符串重写一套加载逻辑。**
   引擎 loader 的 META 提取、确定性 lint、importlib 导入全部是 path-based。新增
   `extract_workflow_meta(source)` 只把"从磁盘读 + parse + 提取 META"里的**纯内存部分**
   抽出来（`load_workflow_meta(path)` 改为读文件后转调它，行为不变），用于在落盘前从源码拿到
   `META.name` 算目录。真正的加载仍走 `load_workflow_source(path)`。

2. **落盘位置 = journal 同源目录 `.../workflows/{META.name}/script.py`**（`runner.materialize_swarmflow_script`）。
   选按 workflow name 的稳定目录、而非随机临时文件：路径随 name 确定 → 与 journal 同目录、同名脚本命中同一
   journal（内容寻址缓存），与 `script_path` 的 resume / 缓存语义**逐字一致**；文件在 `invoke` 写一次后长期存在，
   resume relaunch 直接从盘加载（不重复落盘）。

3. **落盘时机在 `invoke`（同步启动轮内、admit 之后），不在 `run_background`。** 这样启动回执
   （`swarmflow.launched`，`map_result` 同步返回）能带上**解析后的绝对 `script_path`**——leader 重跑 / 迭代
   直接传该路径、不必重发整段源码（用户诉求）。解析后的路径回填进 enriched inputs，`run_background` 与 resume
   relaunch 一律从盘加载、路径唯一。materialize 失败先 `release_workflow(ticket)` 再返回
   `ToolOutput(success=False)`，不泄漏 L1 配额。

4. **文件 I/O 用 `aiofiles`（异步）。** `materialize_swarmflow_script` 是 `async def`，用
   `aiofiles.os.makedirs` + `aiofiles.open(...).write(...)`，与 `engine/journal.py` 的异步写盘一致，
   不阻塞共享事件循环。调用方 `await`。

5. **`invoke` 校验放开**：`script_path`（磁盘）或 `script`（内联）二者其一即可通过；四源全缺返回
   "one of ... is required"；只给 `name` / `resume_id` 返回 "not supported yet"。i18n（cn/en）与工具
   描述（`descs/cn/swarmflow.md`）同步：`script` 标为已接通、简单场景优先。

## 拒绝的方案

- **临时文件（`tempfile`）落盘**：resume relaunch 时临时文件可能已被清理，且与 journal 目录分离，
  破坏"同名脚本 → 同 journal 缓存命中"的语义。稳定命名的 workflow 目录才对齐 `script_path`。
- **为字符串单独实现 `load_from_source`（从字符串 parse + lint + `exec`）**：importlib 需要真实文件
  路径才能给模块正确的 `__module__` / `sys.modules` 条目，字符串 `exec` 会丢模块身份，导致脚本里的
  pydantic / dataclass 解析出错。落盘复用 importlib 反而更简单、更正确。
- **在 `invoke` 内落盘**：`invoke` 期并发准入尚未走完，且 resume 绕过 `invoke`，会漏落盘；放
  `run_background` 一处覆盖首次 + resume 两条路径。
- **`asyncio.to_thread` 包一层同步 `write_text`**：函数本就是 async、仓库已有 `aiofiles` 标准，
  `to_thread` 是多余的一层。（本模块「异步 I/O 约定」已写进 `workflow/AGENTS.md`。）

## 验证

- `tests/unit_tests/agent_teams/workflow/` **108 passed**（含更新的 `test_swarmflow_leader.py`：
  `script` 移出 "not supported yet" 断言列表，新增内联 `script` 成功启动用例）。
- 手测：内联 `materialize_swarmflow_script` → loader 读回 META → 幂等（同源码同路径）→ 缺 `META.name`
  报错，全部通过。

## 已知遗留

- `name`（具名注册表）/ `resume_id`（续跑句柄）执行层仍未接通，`invoke` 继续明确拒绝。
- 内联 `script` 与 `script_path` 若 `META.name` 相同、内容不同，会共享同一 journal 目录并互相覆盖
  `script.py`——这与 `script_path` 既有的"journal 按 name 命名"碰撞行为一致，非新增问题，内容寻址
  journal 会对差异调用重跑。
