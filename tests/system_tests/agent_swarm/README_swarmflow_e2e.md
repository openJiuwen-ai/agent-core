# Swarmflow 端到端（E2E）用例：编写 / 运行 / 定位指南

这份文档沉淀**从 team 入口验证 swarmflow 全功能**的 E2E 经验——怎么写、怎么跑、怎么
盯日志定位问题。配套设计归档见 `openjiuwen/agent_teams/docs/features/F_39_swarmflow-e2e-hardening.md`
与 `docs/specs/S_18_swarmflow-engine-and-worker.md`。

## 这套 E2E 是什么

| 文件 | 作用 |
|---|---|
| `agent_team_swarmflow_e2e.py` | 主用例：leader 经 `swarmflow` 工具跑 `resources/party_planner.py`，覆盖**全部原语**（无状态 `agent` / 有状态 `agent_session` / 无状态 `human` / 有状态 `human_session` / `pipeline` / `parallel` / 嵌套 `workflow`）。自动应答 human 提问，断言 6 阶段 + 完成。 |
| `agent_team_swarmflow_concurrent_e2e.py` | 并发回归：`resources/concurrent_invites.py` 用 `parallel` 并发发起 3 个嵌套子工作流，断言全部跑完（防 `wf_depth` 并发跳过回归）。复用主用例的 `_run_team` / `_SwarmflowProbe`。 |
| `agent_team_swarmflow_budget_e2e.py` | 预算回归：`resources/budget_guard.py` 一路跑到 token 天花板。断言**真实**用量（来自 `usage_metadata`，非估算）终止了脚本、且不是撞循环上界。复用主用例的 `_run_team` / `_SwarmflowProbe`。见 `F_66`。 |
| `config_swarmflow.yaml` | 最小 team spec：`enable_swarmflow: true` + leader/teammate，模型走 `${API_BASE}`/`${*_API_KEY}`/`${MODEL_NAME}` 占位符。 |
| `config_swarmflow_budget.yaml` | 同上 + `swarmflow_budget: 6000`（token 硬天花板）。 |
| `resources/*.py` | swarmflow 脚本（被测对象，非测试代码）。嵌套子工作流用 `__file__` 定位同目录兄弟，**与 cwd 无关**。 |

**核心思路**：不直接调引擎，而是从**公共 team facade** `Runner.run_agent_team_streaming`
驱动 leader，让它真的去调 `swarmflow` 工具——这才是"从 team 入口"的端到端。

## 怎么跑

需要**真实模型端点**（默认从 `tests/system_tests/config_llm_local.yaml` 读 qwen flash）。

```bash
source .venv/bin/activate
export PYTHONPATH=.:$PYTHONPATH
python tests/system_tests/agent_swarm/agent_team_swarmflow_e2e.py
python tests/system_tests/agent_swarm/agent_team_swarmflow_concurrent_e2e.py
```

- **依赖**：team 无条件注入 observability rail，需装 `opentelemetry`：
  `uv sync --extra observability`（不装则 leader 构建即 `ModuleNotFoundError: opentelemetry.sdk`）。
- **保留现场**：`SWARMFLOW_E2E_TEARDOWN=0` 跑则不 `Runner.stop()`、保留 journal / scratch / team.db 供事后查。
- **退出码**：`0` = PASS，`1` = 校验失败（脚本自校验，可直接进 CI 的 smoke）。

## 关键设计约定（踩坑沉淀，照抄即可）

写 team-entry + 真实小模型的 E2E，这几条是反复踩出来的，新写同类用例请遵循：

1. **每次唯一 `session_id`**（`f"swarmflow_e2e_{uuid4().hex[:8]}"`）。
   swarmflow journal 按 `(team, session_id, workflow名)` 寻址；固定 id 重跑会**静默 resume**
   上次缓存替身，前序阶段被跳过、可能掩盖回归仍报 PASS。唯一 id 强制全程 live。

2. **chdir 到 gitignore 的 scratch 目录 + 短相对脚本路径**。
   - team 运行时会把脚手架（`AGENT.md` / `memory/` / `skills/` / `logs/` …）写进 **cwd**；
     在 `.e2e_workdir/` 里跑，污染集中、被 `.gitignore` 兜住，不脏测试树。
   - leader 要把 `script_path` 原样填进工具调用，**长绝对路径会被小模型篡改**
     （实测 `alan_workspace/agent-core` → `alan-core`）。给它 `../resources/xxx.py` 这种短相对
     路径最稳；嵌套子工作流自己用 `__file__` 定位，不受 cwd 影响。

3. **启动看门狗**（`_START_DEADLINE_S`）：runtime ready 后若迟迟没 `workflow_started`，
   判定 leader 没能拉起工作流（路径错等），快速失败而非干等到超时。

4. **等 leader 播报完再 stop**（`_NARRATION_QUIESCE_S` 静默判定）。
   工作流结果经异步工具"完成注入"回灌 leader（`harness.send`）；若在注入在途时 stop，
   会撞关停竞态。`workflow_completed` 后等 leader stream 静默再收尾——既让它真播报，
   也避开竞态。（框架层另有 `_ack` 守卫兜底任意外部早停，见 F_39。）

## 怎么观测 / 定位问题

### 日志在哪
`logging.yaml` 把 console sink 重定向到 **`./logs/jiuwen_console.log`（相对 cwd，写时解析）**，
**不在 stdout**。因为用例 chdir 到 `.e2e_workdir/`，所以实际路径是：

```
tests/system_tests/agent_swarm/.e2e_workdir/logs/jiuwen_console.log
```

终端只看得到 import 期的早期日志，之后全进文件——别以为"卡住了"。

### 盯日志的纪律
- **后台跑 + 实时 tail**，别用 `cmd | tail`（会缓冲到进程结束才出，像假死）。
- **超 ~30s 没预期进展就去读完整日志/traceback 定位**，不要被动等超时。
- `swarmflow` 进度事件流是定位主线，grep `kind` 看时序：
  ```bash
  grep -ao "'kind': '[a-z_]*'" .e2e_workdir/logs/jiuwen_console.log
  # workflow_started → phase → agent_started/completed → human_prompt/replied → workflow_completed/failed
  ```

### "假卡死"的真凶
`consume()`（迭代 leader stream）里抛了异常被吞 → `probe.done` 永不触发 → 一直等到
`_RUN_TIMEOUT_S`。用例用 `asyncio.wait({done_task, consume_task}, FIRST_COMPLETED)`：
consume 一崩就立刻暴露 `consume_task.exception()`，不再干等。新写用例务必保留这个"既等完成、
也等驱动任务暴露异常"的结构。

### 人在回路（human）怎么自动答
human turn 经 `WORKFLOW_PROGRESS(kind="human_prompt")` 带 `correlation_id` 冒泡；
用例从 `TeamMonitor.workflow_events()` 抓到后，走**公共入向路径**回复：

```python
Runner.interact_agent_team(
    HumanAgentMessage(body=answer, sender="user", target=f"swarmflow:{corr}"),
    team_name=..., session_id=...,
)
```

### 本轮定位到的真实问题（案例库）
| 症状 | 根因 | 定位手法 |
|---|---|---|
| leader 构建即崩，无 LLM 日志 | 缺 `opentelemetry.sdk`（observability rail 无条件注入） | 最小复现脚本直接 `run_agent_team_streaming` 打 chunk，看到 `NativeHarness(...)` 构建 traceback |
| 「审批」阶段 `workflow_failed` | `human()` 不接受 `label`（demo 用了 `label=`） | grep `workflow_failed` 的 `text` 字段拿到异常串 |
| `structured_output` 被调几百次 | 工具 ack 无"停止"信号，小模型反复重发 | 统计 `tool_calls=[structured_output(` 次数 vs schema turn 数 |
| `parallel` 里并发 `workflow()` 只跑 1 个 | `wf_depth` 是共享计数器、误把并发当递归 | 数 `agent_completed` label 数 vs 期望；查 `nested workflow depth > 1` 跳过日志 |
| 关停 `InvalidStateError` 打崩 supervisor | 完成注入 send 的 ack future 被 stop 取消后又 `set_result` | grep `supervisor crashed` 看 traceback 落到 `_on_send` |

## 预期 PASS 基线

模型取 `config_llm_local.yaml` 的 `models:` 首项（不要在测试里写死模型名——该文件换端点时会漂）。

- 主用例：`verified: phases=6 human_prompts=7 human_replies=7` → `E2E PASSED`；
  `structured_output` 调用 ≈ 17 次（每轮 ~1 次）；`supervisor crashed` / `InvalidStateError` = 0。
- 并发用例：`verified: 3 concurrent sub-workflows ran` → `CONCURRENCY E2E PASSED`；
  `nested workflow depth > 1` 跳过 = 0。
- 预算用例（`agent_team_swarmflow_budget_e2e.py`，deepseek flash 实测）：
  `verified: rounds=9 workers=9 spent=6148/6000 (683 tokens/round)` → `[budget] E2E PASSED`；
  日志里应有**一条** `token budget exhausted (…/6000); finishing agent round`（rail 就地掐停跨线的
  那个 worker）。**每轮 ~400-1000 token 是这个用例的命脉**——它证明数字来自
  `usage_metadata` 而非按长度估算（同一轮估算只会给 ~50）。`spent` 略微越过 6000 是设计使然：
  末次调用返回后才入账。改动 `swarmflow_budget` 需同步重估轮数，务必让它远低于
  `budget_guard.py` 的 30 轮上界——跑满上界即代表预算根本没生效。

## 关联文档

- `openjiuwen/agent_teams/docs/features/F_39_swarmflow-e2e-hardening.md` —— 本轮四项修复的来龙去脉。
- `openjiuwen/agent_teams/docs/specs/S_18_swarmflow-engine-and-worker.md` —— 引擎 / worker / 会话契约。
- `openjiuwen/agent_teams/workflow/AGENTS.md` —— swarmflow 模块地图与四条铁律。
