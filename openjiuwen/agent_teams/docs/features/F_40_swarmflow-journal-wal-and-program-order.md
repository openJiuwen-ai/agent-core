# Swarmflow Journal:program-order 落盘 + 崩溃durable WAL + 原子/异步 I/O

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-17 |
| 范围 | `workflow/engine/journal.py`(program-order 序列化 + WAL + 原子写 + aiofiles 异步 + 写/删分离)、`workflow/engine/runner.py`(WAL 路径派生 + `await load/finalize`)、`workflow/engine/primitives.py`(`await journal.use` 4 处);文档 `workflow/AGENTS.md` 铁律1 + `S_18` 修正 |
| 测试基线 | 新增 `tests/unit_tests/agent_teams/workflow/test_journal.py`(program-order / 字节稳定 / WAL append-崩溃残留 / 仅-WAL 恢复 / WAL 覆盖 journal / hit 不重写 / finalize 删 WAL / save 保留 WAL / 原子无 temp / torn 行容错 / 不一致保留);`workflow/` + `harness/` 131 passed;真实 qwen flash E2E PASSED(journal 13 行 program-order、WAL 终态删除) |
| Refs | #751 |

## 背景

journal 落地后(`F_38`)在使用中暴露三个问题,均由实跑 + 评审发现:

1. **行序不可读**:`save` 按 `sorted(self.used)`(JSON 字符串字典序)落盘,文件呈
   `call→par→pipe→wf` 分组,与脚本执行顺序无关,难读。
2. **无崩溃 durability**:journal 只在 `save`(run 末尾)一次性写盘。进程中途崩溃 → 没机会
   `save` → 已完成的(昂贵 LLM)调用缓存全丢,resume 无从恢复。
3. **写盘不耐崩溃 + 阻塞事件循环**:`write_text` 非原子(崩溃中途留半截 journal,`load` 解析半行
   抛异常);且同步磁盘 I/O 阻塞共享事件循环(swarmflow 在 leader 进程内与其它团队协程同 loop)。

## 决策

1. **program-order 落盘**(`_program_order`)。`save` 改按**结构序号**排序:把每段 call-path
   `(kind, ordinal, *sub)` 拍平成 `ordinal + 整数子索引` 的数值元组(丢 kind、跳 `wf` 的 name),
   depth-first 即脚本执行序。序号由程序结构决定、与并发完成时序无关,故文件**既逐行可读如执行流、
   又字节稳定可 diff**。不选"按完成顺序 append":并发分支完成时序随机 → 同脚本两次跑行序不同、
   无法 diff,违背"resume 确定、不读 wall-clock"。

2. **WAL(write-ahead log)崩溃恢复**。journal sidecar `<journal>.wal`:`use` 对**新鲜**记录
   (cache-miss,`prior.get(ks) is not record`)立即 append;`load` 先读 journal 再用 WAL
   覆盖/补全(WAL 较新 last-wins),**容忍尾部半行**。进程中途崩溃仍可从 WAL 恢复;journal 缺失/
   不完整时纯靠 WAL 恢复。cache-hit 复用 prior 对象(已 durable)不重写,WAL 只记增量。

3. **写/删分离 + 终态删 WAL(不变量)**。`save` 是**纯写、绝不删 WAL**,可重复调(供未来 mid-run
   checkpoint);新增 `finalize` = `save` + **校验 `used ⊆ 已落盘 journal`(key+sig)后才删 WAL**。
   `run_workflow` 仅在 `_exec_loaded` **正常返回后**(异常/取消都会跳过)调 `finalize`,所以 WAL
   只在 workflow 真正跑完时删。校验用 `used ⊆ saved`(不是 `WAL ⊆ saved`),故脚本改动后的陈旧 WAL
   条目不会阻塞清理;不一致(部分/损坏写)则保留 WAL 兜底。

4. **原子写**。`save` 写 `<journal>.tmp` 后 `os.replace` 原子改名,崩溃中途只可能留旧或新 journal,
   绝不留半截。`load`/`_discard` 解析坏行跳过(`_parse_records`),纵有半行也不崩。

5. **异步 I/O 用 `aiofiles`**。`load`/`use`/`save`/`finalize` 全 async,journal/WAL 读写经
   `aiofiles` 不阻塞事件循环;WAL append 由 `asyncio.Lock` 串行化防并发交错。`os.replace`/
   `Path.unlink` 是快元数据 syscall,保持同步。

   > 配套修正一条**不严谨的旧铁律**:`workflow/AGENTS.md` 铁律1 / `S_18` 原写"engine 只依赖
   > stdlib + pydantic/jsonschema"。真实意图是"engine **不耦合 agent_teams 业务模块**"(为独立
   > 单测 + 与上游 dw/wf 同步);"仅 stdlib" 是给 **swarmflow 脚本**(外部用户代码)的约束,不是
   > 引擎的。engine 可用通用三方库(如 aiofiles)。已据此修正两处文档。

## 拒绝的方案

- **按完成顺序 append 当行序**:并发非确定 → 文件不可 diff、测试 flaky;时间序由带时间戳的
  `jiuwen_console.log` 承担,journal 是内容寻址缓存不是时间日志。改 sort key 才对(见决策 1)。
- **WAL fire-and-forget(后台 flush)**:崩溃可能丢掉刚算出、还没落盘的记录 —— 正是 WAL 要保护的,
  自相矛盾。故 append 必须 await 到落盘。
- **`save` 直接删 WAL(不分离)**:若将来加 mid-run checkpoint 复用 `save`,会在 checkpoint 处误删
  WAL。拆出 `finalize` 专管终态删除,从 API 上杜绝。
- **`asyncio.to_thread` 做异步写**:owner 明确否决。`aiofiles` 是项目既有依赖、更地道;且引擎并无
  "仅 stdlib" 限制(见决策 5 的铁律修正)。
- **non-atomic `write_text` + 删 WAL**:崩溃中途留半截 journal,`load` 解析半行会崩,反而更脆。改
  temp+`os.replace` 原子写 + load 容错。

## 验证

- `test_journal.py`(11 例):program-order 序、字节稳定(乱序插入同样产物)、WAL 崩溃残留(不 save
  仍留 WAL)、仅-WAL 恢复、WAL 覆盖 journal、hit 不重写 WAL、`finalize` 删 WAL、`save` 保留 WAL、
  原子写无残留 `.tmp`、torn 行容错、journal 缺记录则保留 WAL。
- 直跑实证:写 2 条后不 save(模拟崩溃)→ WAL 留 2 行;二次仅靠 WAL 恢复 prior + 命中 + 新增;
  `finalize` 后 WAL 删除。
- `workflow/` + `harness/` 131 passed;真实 qwen flash party_planner E2E PASSED,journal 13 行
  program-order、终态 WAL 已删。

## 已知遗留

- **fsync 未做**:append/save 只 `flush()`(到 OS 缓冲),防**进程崩溃**够用;防**断电/OS 崩溃**需
  `os.fsync`,代价是每次写的 fsync 延迟。首期不做(swarmflow 场景进程崩溃是主要威胁)。
- **`os.replace`/`unlink` 仍同步**:元数据 syscall 通常 µs 级;极端慢 FS 上仍可能微阻塞,但
  `aiofiles` 不封装 rename/unlink,且不可用 `to_thread`(owner 否决),暂保持同步。
- **per-run 文件对按 run 数线性累积**(2026-09-16 review 复核确认):seal 后 relaunch 强制新
  run_id,每个 run 留一对 `journal-{run_id}.jsonl` + `wal/{run_id}.wal`(含完整 LLM 输出)。
  修订 3 的"膨胀问题消失"只对**单 run 内死记录堆积**成立;**文件数增长**是新膨胀面,清理仅
  `delete_team` 整树兜底。治理方案已设计未实施(L1 journal 覆盖收缩 / L2 配额淘汰 / L3 全局
  sweep,见 `doc/plan/2026-09/2026-09-15-wal-rolling-aging-design.md`),按 owner 决策留待
  下一阶段;实施前长寿命 session 磁盘占用 = O(runs × 平均 run 体积)。

## 修订 2026-09-11:load 时 compaction(WAL 只增不自清的止血)

> **本修订已被 2026-09-15 修订整体撤销**(见文末)——compaction 的共享文件重写与并发
> run 的 append 竞争,已随 per-run_id WAL 拆分一并移除。以下保留为决策记录。

原已知遗留"WAL 只增不自清"曾落地保留策略:`Journal.load` 重放完 WAL 后做一次
**compaction**——把 **sealed(终态)run 的 call 记录**从 WAL 里删掉。

- **为什么当时认为 sealed run 的 call 记录是纯死数据**:seal guard(`tool_swarmflow._seal_guard`)
  拦截 sealed run_id 强制 relaunch 换新 run_id(F_88);`get_cached(ks, sig, run_id)` 又要求
  run_id 精确匹配(F_87)。两条合起来 = sealed run 的 call 记录**永远不可能再被命中**。反复
  relaunch(每次强制新 run_id)+ 从不 finalize 的 session,WAL 里会按轮累积同 (key,sig) 不同
  run_id 的重复记录——issue「wal 导致 journal 膨胀」的实证根因。
- **撤销原因(2026-09-15 ST 实证)**:共享 WAL 文件 + 无文件级锁,三个并发竞争成立——
  A(compaction 的 `os.replace` 用旧快照覆盖并发 run 已 flush 的 append)、B(finalize 的
  `wal.unlink()` 删整个文件,连带删除仍在跑的并发 run 的记录,crash-durability 承诺失效)、
  C(journal `save` 的 `os.replace` 互相覆盖)。`_wal_lock` 是实例级 `asyncio.Lock`,只保护
  同 run 内 parallel/pipeline 分支,不跨 run。compaction 恰是竞争 A 的载体。
- **验证**:`test_journal.py` 曾新增 3 例 compaction 用例,撤销时已移除。

## 修订 2026-09-15:撤销 compaction + WAL/journal 按 run_id 拆分 + WAL 永不主动删除

review(ST 复现三个并发竞争)后的一次方向修正,三个决策:

1. **移除 `_compact_wal`,回到"WAL 只增不自清"且更进一步——WAL 永不主动删除**。
   WAL 是日志,应像日志一样自然老化,不被 finalize 或 compaction 主动删除:
   - `finalize` 只做 `save()`(写 journal 快照),不再调 `_discard_wal_if_durable`、不 `unlink`。
   - 清理兜底是 `delete_team` 整树删除;未来若需清理,按日志 rolling 策略(按时间/大小淘汰
     旧 WAL 文件),不按 run 终态删。
   - compaction 的定性错误:它说并发 append 落在"读与 replace 之间"等同"已容忍的 torn 行"
     ——但 torn 行是**该调用重算**,compaction 丢的是**已 flush 返回(durable 承诺已成立)的
     记录**,破坏的是 durability 承诺本身,不是一个量级。

2. **WAL 与 journal 都按 run_id 拆分**(并发竞争的根治):
   ```
   {team_home}/sessions/{session}/workflows/{name}/
   ├── script.py
   ├── journal-{run_id}.jsonl     ← 每 run 自己的快照(竞争 C 消失)
   └── wal/
       └── {run_id}.wal           ← 每 run 自己的 WAL(竞争 A/B 消失)
   ```
   - `paths.workflow_run_journal_path` / `workflow_run_wal_path` 是单一真相源;
     `_resolve_journal_path(script, team, session, run_id)` / `_resolve_wal_path(...)` 在
     swarmflow 集成层拼路径并建目录;engine `run_workflow` 新增 `wal_path` 入参(缺省回退
     legacy 共享 sidecar,engine 保持业务无关)。
   - **F_87 决策 2("run_id 进查询不进路径")不再矛盾**:F_87 当时的担忧是"路径变则 resume
     命中不了前缀",但 resume 本就携带 run_id(resume_id 即 run_id),per-run 路径由 run_id
     直接定位,命中反而更确定。跨 run_id(seal 后 relaunch)新 run_id → 新文件 → 全 miss,
     F_87 隔离语义原样保留。
   - seal guard(`tool_swarmflow._seal_guard`)同样按 `resume_id` 拼 per-run 路径读 seal 记录。
   - per-run_id 下不再有"跨 run 死记录堆积"——每 run 的 WAL 只含自己的记录,膨胀问题消失。

3. **journal.jsonl 保持干净**(save 只写 `self.used`,ST 实证):pause 不写 journal(raise 跳过
   finalize),pause 记录只活 WAL;最后正常完成的 run 把自己的 used 完整落进自己的
   `journal-{run_id}.jsonl`,无重复无多余。

- **验证**:`test_journal.py` 改 2 删 3 增 2(finalize 保留 WAL、load 字节不动、per-run 双
  journal 并发隔离);`test_runner.py` 增 per-run journal/wal 路径 3 例;`test_paths.py` 增
  per-run 布局 1 例。ST(`wal_concurrency` / `journal_residue` / `cache_opt`,本地不提交)
  断言随 per-run 路径适配。
## 修订 2026-09-16:老布局读侧兼容(legacy shared journal 只读种子)

per-run 拆分合入后的 review 遗留项:升级前 pause 中的老 session resume 时,per-run 路径
下没有任何文件,全 miss 重跑。本次补齐读侧兼容,三个决策:

1. **legacy 只读种子**:`Journal.load` 新增 `legacy_path` 参数,读取顺序为 legacy journal →
   legacy WAL → per-run journal → per-run WAL(last-wins,per-run 优先)。swarmflow 集成层
   `_resolve_legacy_resume(script, team, session, run_id)` 在 run_id 非空时返回共享
   `journal.jsonl` 路径(`paths.workflow_journal_path`),`run_swarmflow` 经 engine
   `run_workflow(…, legacy_resume=)` 透传;seal guard 同样带上 legacy 种子(老布局的
   seal 记录在共享 WAL 里,不读就拦不住)。
2. **写侧不迁移,legacy 文件冻结**:新记录只写 per-run 文件,共享文件升级后永不再写。
   拒绝"把老文件按 run_id 拆开迁移"——写侧迁移要动共享文件,重新引入刚根治的共享文件
   写竞争;且记录级 `get_cached(ks, sig, run_id)` 三重检查对混居的老文件天然隔离(异
   run_id 记录 miss),迁移无正确性收益,只有风险。
3. **同 run_id 照常 HIT**:老 session pause 的 run resume 后,其已完成前缀从共享文件种子
   命中缓存(不重跑、不重花钱),后续节点照常执行,最终快照落 per-run 文件。

- **验证**:`test_journal.py` 新增 3 例(legacy 种子 + 三重检查隔离 + per-run 优先 +
  legacy 冻结 + 新记录只进 per-run);`test_runner.py` 新增 2 例(`_resolve_legacy_resume`
  路径映射 / 无 META 返回 None)。ST `agent_team_swarmflow_legacy_compat_st.py`(本地
  untracked):真 LLM 跑 run1 → pause → 伪造老布局(共享 WAL 混居异 run 记录 + pause
  记录,删 per-run 文件)→ 同 run_id relaunch → 断言 fresh WAL 只有 node-2(HIT 证据)、
  legacy 文件 md5 冻结、快照 3 call + seal、异 run 结果未被服务、seal guard 不被 pause
  记录误触发。
