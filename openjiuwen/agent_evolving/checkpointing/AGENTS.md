# Agent Evolving Checkpointing

本目录同时包含 Trainer 的训练状态 checkpoint 和 Skill 经验的 `EvolutionStore`。二者只是
同处本目录，不共享数据模型或恢复协议。

## 模块地图

| 文件 | 职责 |
|---|---|
| `manager.py` | checkpoint 保存策略、operator snapshot 与恢复编排 |
| `state.py` | `EvolveCheckpoint` 持久化状态模型 |
| `store_file.py` | checkpoint 的 JSON 文件 save/load |
| `evolution_store.py` | Skill 目录解析、IO facade、semantic lock 与公共持久化入口 |
| `store_records.py` | `evolutions.json` record 事务、合并、评分和 simplify 写操作 |
| `store_projection.py` | `SKILL.md` 索引、经验详情和脚本索引的派生投影 |
| `store_archive.py` | Skill 创建以及底层 archive、clear、list 辅助 |
| `skill_package.py` | 安全打包/解包与 pristine Skill 分享 |
| `types.py` | `EvolutionRecord`、`EvolutionPatch`、`EvolutionLog` 与 usage stats |

## Trainer Checkpoint

- checkpoint 同时保存 Agent/operator 状态、训练进度和 updater 状态；新增字段时 save/load、
  默认值、版本兼容和 resume 测试必须成对更新。
- 恢复以持久化 checkpoint 为准，不从日志或当前 operator 偶然状态猜测进度。
- `FileCheckpointStore` 的路径和序列化格式属于兼容边界；变更前检查公开文档和已有文件读取。

## EvolutionStore 布局

```text
<skills-root>/<skill>/
├── SKILL.md                         # 原始 Skill + 自动 Evolution Index block
├── evolutions.json                  # 经验事实源
├── evolution/*.md                   # 自动生成的 narrative detail
├── evolution/scripts/_index.md      # 自动生成的脚本索引
├── evolution/scripts/*              # 持久化脚本资产
└── archive/                         # 配对历史版本
```

- 路径解析统一走 `EvolutionStore`；调用方不要拼接 skill、projection 或 archive 路径。
- 所有路径必须位于配置的 skills base dirs 下。保留 `SysOperation` IO 边界，不绕过宿主文件
  系统能力或安全校验。
- 普通 Skill 与 team Skill 通过 `subject_kind` 共用 store。查找、创建、读写和 archive 必须
  传递同一 kind，不能复制 team-only 持久化实现。

## Record 事务与投影

- `evolutions.json` 是单一事实源；`SKILL.md` Evolution Index、`evolution/*.md` 与脚本索引
  都是派生输出，文件头标记为 generated 的内容不能直接编辑。
- append 在 skill semantic lock 内完成完整事务：准备脚本、写 log、重建投影任一步失败，都
  恢复旧 log、`SKILL.md` 和 projection 文件。不要拆开这条事务边界。
- merge、delete、refine、mark-applied 和 score 更新也必须从 `EvolutionStore` facade 进入
  semantic lock；影响投影内容的操作写 log 后重新渲染，仅更新 score/usage stats 时只写事实源。
- 同一 skill 的 read-modify-write 保留 semantic lock；单纯新增文件级 atomic replace 不能
  防止两个协程基于同一旧版本覆盖彼此。
- score/usage stats 更新必须保留未被选中的 record，按 record ID 精确写入；经验是否呈现和
  如何评价由 `experience/` 决定，store 不推断业务使用。

## Archive、rebuild 与 sharing

- 完整配对生命周期由 `experience/archive.py` 的 `EvolutionArchiveService` 在 store facade
  之上协调；不要让 checkpointing helper 和 experience service 各自生成不兼容的版本号。
- archive 是同一版本的 `SKILL.md + evolutions.json` 完整配对。创建、恢复、裁剪不能留下
  只能恢复一半的版本。
- rebuild 只有在配对归档成功后才能清空当前经验记录；archive 失败时保留当前状态。
- rollback 恢复配对后重新生成 projection，不能把历史派生文件直接当事实源复制回来。
- 分享包排除本地 `evolutions.json`、archive 和 projection 状态，并从 `SKILL.md` 移除自动
  Evolution Index；解包必须拒绝绝对路径、目录穿越和不安全 skill name。

## 修改与测试

- Trainer checkpoint：运行对应 manager/store/resume 测试。
- EvolutionStore：运行 `uv run pytest tests/unit_tests/agent_evolving/checkpointing -q`。
- record 或 projection 变化必须覆盖 append、merge、delete、score、脚本、写失败回滚和索引
  幂等；archive 变化必须覆盖配对、裁剪、rollback 与失败保留当前状态。
