# Swarmflow worker 成员名稳定化与 isolation 入签名

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-11 |
| 范围 | `workflow/engine/journal.py`（`call_signature` 折入 isolation）、`workflow/engine/backends/{base,mock}.py`（`run` 增 `call_key`）、`workflow/engine/primitives.py`（`_call_backend` 传 journal key）、`workflow/backends/team_worker_backend.py`（`_next_member_name` 哈希后缀）、`tools/locales/descs/{cn,en}/workflow/swarmflow.md`（复用语义，见 F_39 修订）、测试 `test_pause_resume.py` / `test_journal.py` / `test_worker_backend.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 全量通过（290 passed）；真实 LLM ST 三场景 38/38（WAL compaction 16 + worktree 同 run 复用 13 + isolation 签名 8，本地不提交） |
| Refs | #—（关联 issue「缓存命中特性优化：wal 导致 journal 膨胀、worktree 命中」；WAL compaction / 孤儿对账见 F_40 / F_39 修订） |

## 背景

issue 的 worktree 命中一半，根因在 worker 成员名的**计数器后缀**：resume 会新建 `TeamWorkerBackend`，计数器从 0 起算，而缓存命中的 `agent()` 不进 backend、不消耗计数器槽位。推演一遍：run 内三个串行 agent，agent-1 完成（成员名 `{run}-a-0`），agent-2 被打断（`{run}-b-1`）。pause 后 resume，脚本重放，agent-1 命中缓存直接返回，agent-2 miss 进 backend，计数器还是 0，铸出 `{run}-b-0`。成员名漂移 → worktree slug 跟着变 → `GitBackend.create` 的 fast-recovery（已存在的 worktree 直接 `existed=True` 返回）找不到原 slug → 被打断 worker 的脏 worktree 留在盘上无人认领，续跑 worker 在全新 worktree 里从零开始。同 run 复用根本接不上。

SDD-0005（swarm-design-docs 归档）§4.3 同时指出 `call_signature` 不含 `isolation` 的正确性缺口：脚本把某个 `agent()` 从无隔离改成 `isolation='worktree'` 后，同 run_id relaunch 时签名不变会命中旧缓存，用户的编辑被静默忽略、worktree 不创建。

## 决策

1. **成员名后缀从计数器换成 call-path key 的哈希派生**。`_next_member_name(opts, call_key)`：`{run_prefix}-{label_slug}-{sha256(call_key)[:12]}`。引擎的 call-path key 是结构位置的纯函数——`agent()` 在缓存判定之前计算 key（命中也消耗 ordinal 槽位），同脚本重放时 key 序列逐字相同；parallel 分支各有独立作用域，并发不串。同 run 重放 → 同 key → 同成员名 → 同 slug → fast-recovery 复用。跨 run（新 run_id）→ run_prefix 不同 → 新 worktree，F_47 的隔离语义原样保留。哈希取 12 hex（48 位），单 run 一千 worker 量级碰撞概率 ~1.8e-9。计数器保留为 `call_key` 缺席时的回退（直接调用 backend 的测试场景）。

2. **`AgentBackend.run` 增 `call_key: str | None = None` 关键字参数**。引擎在 `_call_backend` 把 journal key 传进去；`call_key` 是引擎域数据（结构位置标识），不是业务耦合，不违反 engine 业务无关铁律。opts 袋不塞这个键——opts 是用户可见的白名单校验面，也是 journal 记录的持久化内容。默认 None 保持协议兼容，MockBackend 等不关心它的实现照常忽略。

3. **isolation 有值时折入 `call_signature`，无值时字节不变**。`identity = {k: opts.get(k) for k in ("label", "phase", "model")}`；`if opts.get("isolation"): identity["isolation"] = opts["isolation"]`。存量缓存（无 isolation 的调用）签名逐字节保持，不发生全量失效；带 isolation 的调用签名改变——这正是修复本身。isolation 是当前唯一影响执行语义的 options 键（model 已在签名、timeout 只约束时延、agent_type 是占位后端不读），所以只折它一个。同 `history` 的「仅在非空时参与」纪律。

4. **复用语义只到同 run_id**。跨 run 复用（slug 去 run_id 化）明确不做：新 run_id 意味着全新执行上下文，上一 run 的脏工作区不应被继承。上一 run 的遗留由孤儿对账处理（见 F_39 修订）：干净删、脏留 leader。

## 拒绝的方案

- **跨 run worktree 复用（slug 去 run_id 化）**：省每 worker 200-500ms 创建开销，但引入「上一 run 脏状态被本 run 继承」的语义问题（脚本作者不知道工作区不干净），且并发唯一性与跨 run 可复用性对 slug 的要求冲突。用户裁决新 run_id 即新 worktree。
- **per-label 计数器替代全局计数器**：parallel 扇出的同 label 分支会错配到彼此的 worktree，只在 label 唯一的前提下成立。
- **isolation 无条件折入签名**（不判空）：会让全部存量缓存一次性失效，违背 `history` 参数当年确立的字节稳定纪律。

## 验证

- `test_member_name_is_stable_across_backend_instances_with_same_call_key`：同 call_key 跨 backend 实例铸出相同成员名；不同 key 不同名；无 key 回退计数器。
- `test_backend_receives_stable_call_key_across_resume`：引擎级——run1 pause 后 resume，backend 侧观察到的 B 调用 call_key 与 run1 相同（成员名稳定性的来源）。
- `test_call_signature_byte_stable_without_isolation`：无 isolation 时与旧公式逐字节相同（独立展开的 legacy 参照）。
- `test_call_signature_folds_isolation_when_set`：设 isolation 签名改变；显式 None 等同缺席。
- `test_resume_hits_untouched_call_and_reruns_edited_isolation`：同 run relaunch 下，给 A 加 isolation 后 A miss 重跑（编辑生效）；无编辑对照用例确认 A 仍命中（存量缓存不失效）。
- 真实 LLM ST 场景 B：pause 中断 isolated worker → resume 后第二条 "Created worktree" 日志路径与 pause 前逐字相同（fast-recovery 复用）；relaunch（seal 后新 run_id）后路径不同（F_47 语义保留）。场景 C：pause 后改脚本加 isolation、同 run_id relaunch，agent-1 的 CACHE 判定序列为 MISS → MISS（修复前为 MISS → HIT），worktree 真实创建。

## 已知遗留

- 命中重放不重建 worktree 的完整修复（下游引用 worktree 文件产物时命中路径不校验存在性）：工具描述已警示「worker 产出走返回值交付」，完整修法（缺失降级 miss）留待后续。
- `agent_session` / `human_session` 对 `isolation` 静默忽略（会话路径不建 worktree 也不报错）；应 fail-fast，本次不动。
- `AvatarSessionManager` 的会话成员名（`wf-sess-{slug}-{n}`）同样是计数器派生、不跨冷恢复持久；会话不走 worktree，无本文档的复用诉求，记录在案。
