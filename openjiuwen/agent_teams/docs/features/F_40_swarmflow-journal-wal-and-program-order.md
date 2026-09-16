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

## 修订 2026-09-11:load 时 compaction(WAL 只增不自清的止血)

原已知遗留"WAL 只增不自清"已落地保留策略:`Journal.load` 重放完 WAL 后做一次
**compaction**——把 **sealed(终态)run 的 call 记录**从 WAL 里删掉。

- **为什么 sealed run 的 call 记录是纯死数据**:seal guard(`tool_swarmflow._seal_guard`)
  拦截 sealed run_id 强制 relaunch 换新 run_id(F_88);`get_cached(ks, sig, run_id)` 又要求
  run_id 精确匹配(F_87)。两条合起来 = sealed run 的 call 记录**永远不可能再被命中**。反复
  relaunch(每次强制新 run_id)+ 从不 finalize 的 session,WAL 里会按轮累积同 (key,sig) 不同
  run_id 的重复记录——issue「wal 导致 journal 膨胀」的实证根因。
- **保留什么**:pause/seal 两类 run 级记录原样保留(pause 记录是 cold resume 找恢复点的依据、
  seal 记录是 seal guard 的读取对象);unsealed run 的 call 记录保留(该 run 可能还会 resume);
  无法解析的 torn 行字节原样保留(replay 本来就跳过它)。
- **不变量不破**:`finalize` 仍是唯一的整文件级 WAL 删除;compaction 只收缩"未来任何 load 都
  不可能 serve 的记录",崩溃 durability 语义不变。`load` 内先读后写、run 开始前执行,不与本次
  run 的 append 竞争;若同 session 并发了同名 run 的 append 恰好落在读与 replace 之间,最坏
  等同于已容忍的 torn 行(该调用下次 resume 重算),无正确性影响。
- **验证**:`test_journal.py` 新增 3 例——sealed call 记录被删而 pause/seal/unsealed 记录保留、
  无 seal 时 WAL 字节不动、torn 行在全删场景下幸存且 seal 记录仍可查;原 18 例全过。
  另有真实 LLM ST(`agent_team_swarmflow_cache_opt_st.py`,本地不提交)双场景复现:
  relaunch 后 WAL 与 journal 存在同 (key,sig) 不同 run_id 的记录(compaction 目标),场景 A
  11/11、场景 B 8/8 PASS。