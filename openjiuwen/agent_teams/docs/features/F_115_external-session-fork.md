# 外部 swarmflow 会话的 fork（provider 原生分支）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-21 |
| 范围 | `harness_protocol/models.py`、`harness_providers/{claudecode,codex}/harness.py`、`workflow/backends/{external_fork,external_turns,avatar_session_backend}.py`、`external/member_runtime.py`、`external/cli_agent/spawn.py` |
| 测试基线 | `test_external_fork.py`（4 个函数 / 7 个用例：拒绝不支持模式参数化、cut marker 选取与对账、Claude SDK 离线 fork 成功/失败、Codex `thread_fork` 带 entry env 与 `lastTurnId`）、`test_external_turns.py::test_turn_marker_follows_the_provider_cut_point`、`test_avatar_session_backend.py::test_external_fork_*` / `test_external_child_*` / `test_external_markers_*` / `test_external_member_is_registered_*`、`test_claudecode.py::test_assistant_message_carries_its_transcript_uuid`、`test_cli_workflow_runtime.py::test_workflow_runtime_forwards_seed_checkpoint`、`test_models_fork_modes.py` |
| 关联 | `F_114`（三方会话）、`F_81`（native fork）、`S_18` 会话契约 13、`S_27` |

## 背景

`F_114` 把三方会话的 `fork()` 一律拒绝。native fork 靠引擎侧对话镜像重建上下文；外部 CLI 的上下文在
provider 自己的会话文件 / 线程里，镜像重建必然丢工具调用记录，"看起来成功、实际残缺"比拒绝更糟。
两家 SDK 都有原生分支原语：Claude `fork_session(session_id, up_to_message_id)`（离线转写复制），
Codex `thread/fork(threadId, lastTurnId)`（app-server 内分支）。

## 决策

1. **能力位在 provider 卡片**：`HarnessCard.fork_modes`（默认空）；Claude / Codex 声明 `{full, before}`。
   `after` / compact 两族对三方永久拒绝——两家 SDK 都没有"丢头留尾"或压缩原语，模拟即残缺。
2. **capture 只记凭据，materialise 推迟到子会话首轮**。`fork()` 时引擎已知父成员名但子会话尚未存在；
   provider 的 checkpoint 作用域绑定 `agent_id`（`_restored_checkpoint_data` 校验），父指针不能直接给子用。
   所以 `capture_fork` 返回 `{"external_fork": {provider, parent_member, parent_pointer, mode, cut_marker, keep_rounds}}`，
   子会话首轮先 `reconcile` 再 `materialise_external_fork` 得到**子指针**，经 `seed_checkpoint` 写进子成员
   自己的 checkpoint，再以 `require_checkpoint` 续接。凭据不落 journal：续跑时脚本重跑 `fork()` 重新算凭据；
   子会话已持久化自己的指针时（`swarmflow_external_turns` 有 pointer）跳过物化直接续接，不产生第二条分支。
3. **切分点 = 轮标记**：驱动每轮记录 provider 侧 id——Codex `native_turn_id`，Claude 本轮最后一条 assistant
   消息的**转写 uuid**（`AssistantMessage.uuid`，非 API `message_id`；离线 `fork_session` 只认转写 uuid）。
   `before` 的 `keep_rounds=N` 取第 N 个标记；越界或标记缺失直接报错，
   不回退 full（native 的"越界降级 full"对外部不成立：标记表是唯一真相）。
4. **标记与指针随成员子会话持久化**（`swarmflow_external_turns`），并保留进程内镜像：全命中续跑后父行
   不存在时仍可 fork；镜像在本 run 内权威，落盘失败只降级为"跨进程不可 fork"。
5. **Codex 用短命客户端 fork**：SDK facade `AsyncCodex.thread_fork` 不暴露 `last_turn_id`，直接构造底层 `AsyncCodexClient`（`start` → `initialize` → `thread_fork(threadId, {lastTurnId})` → `close`）。
   同一 app-server 配置（`codex_bin`/`cwd`）保证线程可寻址。
6. **子会话 provider 必须等于父**：凭据带 provider，`open_session` 校验；native 快照 `fork_data` 落到外部
   会话同样拒绝。

## 拒绝的方案

- 引擎镜像降级：静默残缺。
- 复用父 checkpoint 给子：provider 作用域校验拒绝，且父子共享指针会互相推进。
- `resume_session_at` + `fork_session=True` 在线切分（Claude）：需要父会话在线；离线 `fork_session` 无此约束且 C5 已确认为纯文件操作。

## 已知遗留 / 待实测

- **C5-1 Codex 跨进程寻址**：短命客户端能否 fork 另一进程 app-server 内的父线程——实测失败则把
  `CodexHarness.card.fork_modes` 置空（报错引导），不阻塞其余交付。
- **C5-3 Claude 转写落盘窗口**：父轮结束到 JSONL 可见的延迟；过长则 materialise 前加等待 / 重试。
- 子会话首轮的 Claude 累计 usage 基线沿用 `F_114` 遗留（重建驱动从 0 起算）。
- 子会话 `cwd`：`isolation="worktree"` 的子会话取自己的 worktree，Claude 离线 fork 的 `directory` 需为父转写
  所在项目目录——当前取子 cwd 回退 entry.cwd，父子 worktree 不同时可能找不到父转写（报错，不静默）。
