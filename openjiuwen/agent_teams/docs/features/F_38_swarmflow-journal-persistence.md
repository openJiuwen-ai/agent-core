# Swarmflow Resume Journal 落盘接线与 sessions/workflows 目录布局

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-08 |
| 范围 | `paths.py`（新增 `team_sessions_dir` / `team_session_dir` / `workflow_run_dir` / `workflow_journal_path` + `_safe_segment`）、`workflow/engine/loader.py`（新增 `load_workflow_meta`）、`workflow/runner.py`（`_resolve_journal_path` + `run_swarmflow` 接线）；附带修 `workflow/engine/primitives.py`（有状态会话路径对齐 `_BackendCallResult`，独立 `fix` 提交） |
| 测试基线 | 新增 `test_runner.py`(3) + `test_paths.py` 2 例 + `test_engine.py` 2 例；`workflow/` + `test_paths.py` 68 passed；`-k "swarmflow or workflow"` 72 passed / 0 failed |
| Refs | #751 |

## 背景

swarmflow 引擎层早已实现 content-addressed resume journal（`engine/journal.py`：
结构化 call-path 键 + sig，JSONL `load`/`save`），`run_workflow` 也暴露了 `resume`
（读旧）/ `journal_path`（写新）两个入参。**但生产链路从未接线**：
`SwarmflowTool.run_background` → `run_swarmflow` → `run_workflow` 一路不传这两个参数。

后果：`resume=None` → 每次冷启动、零 cache-hit；`journal_path=None` → 跑完不写文件；
journal 只作为 `Runtime.journal` 内存对象活一次 run，run 完即弃。唯一用到 journal 文件
持久化的只有引擎单测（传 `tmp_path`）。leader 跑的 swarmflow 因此完全不可重放。

## 数据结构 / 目录布局

在现有 `team_home(team_name)` 下与 `team-workspace/`、`workspaces/`、`team.db` 同层
新增 `sessions/`，按 session 再按 workflow 分桶：

```
{team_home}/sessions/{session_id}/workflows/{workflow_name}/journal.jsonl
```

路径管理全部集中在 `paths.py`（单一真相源铁律）：`team_sessions_dir` →
`team_session_dir` → `workflow_run_dir` → `workflow_journal_path`。

## 决策

1. **接线点在 `run_swarmflow`（业务层），不污染引擎**。`_resolve_journal_path`
   计算 journal 路径，把 `resume` 与 `journal_path` 指向**同一文件**传给 `run_workflow`。
   首跑文件不存在 → `Journal.load` 空 prior（冷启动）；跑完 `save` 写入；次跑命中 →
   cache-hit 短路。引擎 `run_workflow` 签名零改动，仅业务层多算一个路径。
2. **workflow_name 由 `META["name"]` 提供，且必填**。新增 `load_workflow_meta`
   （`engine/loader.py`，纯 AST + `ast.literal_eval`，复用 `_extract_meta`，**不**
   importlib 导入脚本）在调 `run_workflow` 前轻量取名；缺 name →
   `raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID)`。`run_workflow` 内部仍会正常
   load+import 执行——`load_workflow_meta` 只为提前拿名字，避免重复 import。
3. **路径段 sanitize 防穿越**。`workflow_name` 来自用户脚本 META（不可信），
   `_safe_segment` 把 `[^A-Za-z0-9_.-]` 折成 `_`、strip 首尾分隔符，杜绝 `..` / `/`
   逃逸父目录；`session_id` 同样 sanitize，空时回退 `"default"`（避免 `sessions//...`
   畸形路径）。`Journal.save` 不建父目录，故 `run_swarmflow` 先 `mkdir(parents=True)`。
4. **`preprocess_swarmflow`（MockBackend 离线预览）不落 journal**。预演只为生成 4 层
   预览，不应污染真实 journal。

## 拒绝的方案

- **在 engine `_extract_meta` 强制 name 必填**：会破坏 `preprocess` 与现有不带 name 的
  引擎测试，违反"不破坏"。必填校验放业务层 `run_swarmflow`，engine 保持 `.get("name")`。
- **用脚本文件名（stem）命名 workflow 目录**：用户选定用 `META["name"]` 必填——逻辑名
  比文件路径更稳定、更可控（评估见 plan 的命名问题）。
- **为取 name 而 `load_workflow_source` 两次（双重 importlib 导入）**：脚本顶层会被执行
  两遍，开销与副作用风险。改为 AST-only 的 `load_workflow_meta`。
- **journal 路径计算下沉进 engine**：违反 engine 业务无关铁律（engine 不懂 team 目录）。

## 验证

- `test_paths.py`：`workflow_journal_path` 布局正确；`_safe_segment` 对 `../../etc/passwd`、
  含空格/特殊字符的 name 做 sanitize，不穿越父目录。
- `test_engine.py`：`load_workflow_meta` 取到 name 且**不产生** `wf_flow__*` 的
  `sys.modules` 条目（未 import）；非纯字面量 META 报 `MetaError`。
- `test_runner.py`：`_resolve_journal_path` 三场景——映射到 `sessions/<sid>/workflows/
  <name>/journal.jsonl` 且父目录已建；空 session 回退 `default`；缺 name `raise_error`。

## 已知遗留

- **附带修复**：本特性接线时发现有状态会话路径（`AgentSession.send`/`_drive`）仍消费
  重构前的 3 元组，而 `_attempt_calls` 已返回 `_BackendCallResult`、`_make_record` 已收
  `_JournalRecordInput`——HEAD 上 F_37 重构的遗漏，使每个有状态会话/human turn 崩
  （`TypeError` on unpack）。作为独立 `fix(swarm)` 提交对齐，非本特性范围但同一工作单元修掉。
- **跨 run 的 journal 清理 / GC**：`sessions/<sid>/workflows/` 只增不删；长期需要一个
  保留策略或随 `delete_team` 清理（`team_home` 整树删除时已连带清掉）。首期不做。
- **avatar session checkpoint 与 journal 的协同**：journal 是 agent 调用级缓存，
  avatar `Session` checkpoint 是有状态会话上下文（见 `F_37`），两层正交；部分-hit 续跑
  仍依赖 avatar checkpoint 落地（`F_37` 已知遗留）。
