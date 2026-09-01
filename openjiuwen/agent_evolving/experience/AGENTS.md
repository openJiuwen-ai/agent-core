# Agent Evolving Experience

Skill 与 team Skill 的在线经验生命周期服务。Rail 负责触发和宿主交互，本目录负责经验的
查询、生成结果编排、审批状态、结构化提交、使用评分和治理操作；磁盘布局归
`checkpointing/` 中的 `EvolutionStore` 管理。

## 模块地图

| 文件 | 职责 |
|---|---|
| `online_orchestrator.py` | 构建上下文、生成 update、local preview、暂存及 auto-approval |
| `skill_experience_manager.py` | 在线 pending、approve/reject/retry 与 team governance |
| `query.py` | 有界、只读的经验索引与详情查询 |
| `submission.py` | 已批准的 evolve/simplify 结构化提交 |
| `draft_schema.py` | subject、proposal、draft 与 simplify action 归一化 |
| `tracker.py` | 已呈现 BODY 经验的 session 队列、使用统计和周期评价 |
| `scorer.py` | 经验评价、分数更新计算和 simplify 建议生成 |
| `archive.py` | `SKILL.md + evolutions.json` 配对归档、恢复与裁剪 |
| `rebuild.py` | 归档后生成有界 rebuild context，不生成新 Skill 正文 |
| `common.py` | pending change 构造、部分提交和 simplify 执行原语 |
| `lifecycle.py` | local preview、pending commit 与 host-facing 结果值 |
| `types.py` | 在线经验上下文、proposal、approval request 与 apply result |

## 被动演进

```text
EvolutionSignal → SingleDimUpdater + SkillExperienceOptimizer
→ execute_updates → LocalApplyPreview → ExperienceManager.stage_apply_results
→ approval event 或 auto-approval → EvolutionStore
```

- updater/optimizer 只生成候选，local preview 不得写持久化状态。
- `requires_approval=True` 时只创建 caller-owned pending snapshot；批准前不能写
  `evolutions.json` 或任何投影。`False` 是调用方显式选择的 auto-approval，不是失败兜底。
- `ExperienceManager` 拥有在线 pending 和 governance 状态。只做查询或 Agent 工具提交的新
  调用方应依赖 `ExperienceQueryService` / `ExperienceSubmissionService`，不要仅为复用这些
  能力而依赖完整 Manager。
- `approve` 支持选择记录子集。单条 record 写入在 store 层原子化；批次部分失败时保留尚未
  写入的 approved tail 和原 `request_id` 供 retry，已成功记录不回滚。

## 主动 review 与提交

```text
prepare review scope → restricted review agent → reviewed proposals
→ main agent selection → EvolutionInterruptRail approval → SubmissionService persistence
```

- review scope 绑定当前 session 和 clean trajectory；review Agent 只读限定证据并提交结构化
  结果，不能直接持久化经验。
- `prepare_evolve_submission()` 先解析选中 proposal 并完成 draft 校验，不消费 review ref。
  只有提交成功后才调用 `consume_prepared_submission()`。
- `evolve_skill_experiences` 与 `simplify_skill_experiences` 的用户批准由
  `EvolutionInterruptRail` 在工具执行前处理；`auto_save` 只改变这一明确审批门。
- `subject.kind + subject.name` 是 Skill 与 team Skill 共用的身份边界。校验、store lookup 和
  pending snapshot 必须保留 subject kind，不能用同名目录猜测类型。

## 经验如何投入使用

- `evolutions.json` 是经验事实源。append、merge、delete、refine 等影响内容的操作通过
  `EvolutionStore` 重建 `SKILL.md` 的 Evolution Experiences 索引、`evolution/*.md` 详情和
  脚本投影；这些生成文件不能直接编辑。
- `BODY` 经验通过 `SKILL.md` 索引和详情文件渐进读取。Rail 只在实际呈现 record ID 后加入
  session 评价队列，并按 `eval_interval` 使用后续对话更新 usage stats 和 score。
- `DESCRIPTION` 经验在 `SkillUseRail` 绑定同一 `EvolutionStore` 时拼入 Skill description；
  EvolutionRail 不会自动建立这项绑定。
- `SCRIPT` 经验投影到 `evolution/scripts/`，使用前先读索引和源码；它是可适配的辅助资产，
  不是自动执行或强制步骤。
- 三种 target 都是经验记录及其投影，不等价于直接改写原始 Skill 正文。`ExperienceTracker`
  当前只评价 BODY record；不要把索引存在误判为已使用，也不要把所有 target 纳入同一评分。

## 治理铁律

- simplify 的 DELETE/MERGE/REFINE/KEEP 必须先按当前 store 内容校验 record refs，再经过主动
  工具审批或 team governance 审批；执行逐项计数并保留错误事实。
- rebuild 先归档完整 `SKILL.md + evolutions.json` 配对；只有配对归档成功后才能清空当前
  经验记录。`prepare_rebuild_context()` 只返回有界上下文，不生成或写入重建后的 Skill。
- `sharing/` 中的对象只是传输 wrapper。跨用户元数据不能写回本地 `EvolutionRecord`；上传
  Skill 包使用移除本地 Evolution Index 的 pristine `SKILL.md`。

## 修改与测试

- 改在线暂存/审批：运行 `tests/unit_tests/agent_evolving/experience/` 及
  `tests/unit_tests/harness/rails/evolution/test_skill_evolution_rail.py`、
  `test_team_skill_rail.py`、`test_evolution_interrupt_rail.py` 的相关用例。
- 改 query/submission/tool draft：同时检查 `tests/unit_tests/agent_evolving/tools/`。
- 改 tracker/scorer：覆盖未呈现记录、重复呈现、评价间隔、失败不阻断主流程和 score 更新。
