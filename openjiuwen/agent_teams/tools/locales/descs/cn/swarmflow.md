运行一个 swarmflow 编排脚本（多 agent 工作流）。脚本派生并协调一群 worker subagent —— 用于**全面**（分解后并行覆盖）、**确信**（独立视角对抗验证后再下结论）、或承接**单个上下文容纳不下的规模**（迁移、审计、深度研究）。脚本就是你编码这种结构的地方：**什么扇出、什么验证、什么综合**。

## 何时调用 / 何时不用
- **用**：复杂、多步、可并行 / 流水线、需对抗验证、大规模排序、根因排查、探索、分诊的任务——这类高价值、算力成本被证明合理的工作。
- **不用**：简单单 agent 就能完成的任务别强行编排——多数普通任务不需要一个 5 人评审团。先问自己"它真的需要更多算力吗？"
- **触发**：用户消息出现 `swarmflow` / `workflow` / `工作流` 关键字，或描述了确实需要多个 agent 并行 / 流水线协作的诉求。
- **混合式最佳**：决定编排之前，常常先**内联侦察**（列出相关文件、找到数据源、圈定范围）得出"工作清单"，再让脚本对其流水线处理。你不必在*任务*之前知道全貌——只需在*编排那一步*之前知道。

## 常见工作流形态（可跨轮串联）
- **理解（Understand）**：并行阅读相关子系统 → 结构化映射。
- **设计（Design）**：N 个独立方案的评审团 → 评分综合。
- **审查（Review）**：按维度发现 → 对抗验证（见下方骨架）。
- **研究（Research）**：多模态扫描 → 深度阅读 → 综合带引用的结论。
- **迁移（Migrate）**：发现改动点 → 逐个转换 → 验证。

复杂工作可**拆成多个脚本按阶段串联**（一个完成后再启下一个），或在一个脚本内用 `phase()` 分阶段。每读完一个阶段的结果，再决定下一阶段——你始终在环中，每个脚本是一次范围明确的扇出。

## 行为契约（务必遵守）
- 本工具**立即返回**——工作流在后台异步执行，**不要轮询**、不要反复调用。
- 各阶段（phase）进展会**自动**作为通知进入你的上下文；工作流**完成或失败时，最终结果自动回灌**给你，无需主动查询。
- 你处于**旁观角色**：脚本自主编排 worker 完成全部工作。收到进展通知时，用简洁自然语言向用户转述；两次通知之间安静等待是正常状态。
- **不要**自己 spawn 成员、`create_task` 或代替脚本编排——编排完全由脚本负责；也不要改写 worker 的中间结果。

## 脚本来源（四选一）与 args
- `script_path`（**当前可用**）：磁盘上的脚本文件路径。
- `script`（接口就位、执行推进中）：内联脚本源码。
- `name`（接口就位、执行推进中）：已保存 / 具名工作流。
- `resume_id`（接口就位、执行推进中）：上次运行的 run_id，断点续跑。
- `args`：传给脚本 `run(args)` 的**字符串**参数（如研究问题、目标路径）。

> 当前仅 `script_path` 接通执行；其余三者会以**明确错误**提示拒绝（绝不静默无操作）。无现成脚本时可用 `swarmskill-creator` skill 生成脚本，拿到路径后再调本工具；该 skill 不可用时如实告知用户、不要硬调或手搓。

## 脚本结构（Python）
脚本是一个 Python 模块：顶层 `META`（纯字面量）+ `async def run(args)`，从 `swarmflow` 导入原语。

```python
from swarmflow import agent, agent_session, human, parallel, pipeline, map_parallel, phase, log, workflow, budget, compact

META = {
    "name": "deep-research",
    "description": "一行描述，权限对话框里展示",
    "phases": [{"title": "Search", "detail": "并行检索"}, {"title": "Verify"}],  # 也可为纯字符串
}

async def run(args):
    phase("Search")
    hits = await agent(f"检索：{args}", schema=HITS)
    return {"answer": ...}  # 结构化结果即返回值，自动回灌
```

- `META` 必须是**纯字面量**——不能有变量、函数调用、f-string、字符串拼接（加载期静态提取）。
- `phases[].title` 与脚本里 `phase()` 调用的标题**精确匹配**；没有对应 `phase()` 的项自成一个进度组。
- **返回值语义**：worker 被告知"它的最终文本**就是**返回值"（不是给人看的消息），所以它返回**原始数据**。`run(args)` 的返回值（通常是 dict）即工作流最终结果，自动回灌给调用者。

## 编排原语（`from swarmflow import ...`）
- `await agent(prompt, *, schema=None, label=None, phase=None, model=None, isolation=None, agent_type=None, timeout=None)` —— 派生一次性 worker subagent。无 `schema` 返回文本；`schema` 给 JSON Schema dict 返回校验过的 dict、给 pydantic 模型返回模型实例（校验在工具调用层，不匹配时模型自动重试）；失败（重试耗尽 / 超 spawn 上限）返回 `None`——用 `compact()` 过滤。`isolation='worktree'`（独立 git worktree）、`agent_type`（具名专家 subagent）接口就位、执行推进中——传入不报错，但当前不改变执行。
- `agent_session(label=, phase=, instructions=, options=)` + `await s.send(prompt, *, schema=, notify=False)` —— **有状态**多轮 agent，跨轮记忆，第二轮无需重述第一轮上下文。`notify=True` 单向推送、返回 `None`。
- `await human(prompt, *, schema=)` / `human_session()` + `.send()` —— 一次性 / 有状态的**人类参与**（HITL），等真人不占并发 permit、不计 spawn 预算，可被 journal 重放。
- `await parallel([thunk, ...])` —— fork-join **栅栏**：并发跑、等齐才返回；分支抛错落 `None`，调用**永不抛**（用前 `compact` 过滤）。thunk 是 `lambda: agent(...)` 这样的零参可调用。
- `await pipeline(items, stage1, stage2, ...)` —— **无栅栏**流式：每个 item 独立穿过所有 stage（A 可在 stage3 而 B 还在 stage1）；每个 stage 回调收 `(prev, item, index)`——后续 stage 可用 `item`/`index` 标注工作，无需把上下文穿过 `prev`；某 stage 抛错只把该 item 落为 `None`、跳过它剩余 stage。
- `await map_parallel(items, fn)`（别名 `pmap`）—— 防闭包陷阱的 fan-out，`fn` 为 `async def fn(item)` 或 `fn(item, i)`，自动正确绑定每个 item。
- `phase(title)` / `log(message)` —— 进度（开启阶段 / 旁白行）。
- `await workflow(name_or_path, args)` —— 内联子工作流，**仅一层**（子工作流内再调会报错）；未知名 / 不可读路径 / 语法错抛错，可 try/except。
- `budget.total` / `budget.spent()` / `budget.remaining()` —— token 预算（见下）。
- `compact(xs)` / `flatten_filter(xs)` —— 纯列表 helper：去 falsy（None/''/0/[]）/ 展平一层并去 falsy。

## worker 能跑什么
每个 `agent()` / 会话派生的 worker 是一个团队成员实例（单轮或会话内多轮），**继承团队 teammate spec 的能力**：model、工具（tools）、skill、workspace、sys_operation。但它**没有团队协作工具**（不能 spawn 成员 / 建任务 / 发消息）——是个聚焦、用完即弃的执行单元。所以脚本可以让 worker 调用其 spec 配置的工具（检索、代码执行等）来真正干活；要结构化产物就传 `schema`。

## pipeline 还是 parallel（默认 pipeline）
**默认 `pipeline`**（无栅栏，墙钟 = 最慢的单条 item 链，而非逐阶段最慢之和）。只有当某阶段**需要上一阶段全部跨 item 的结果**时，栅栏（`parallel`）才正确：

- 在昂贵的下游工作前，对**全量结果集**去重 / 合并；
- 总数为 0 时**提前退出**（"0 个发现 → 整段跳过验证"）；
- 下一阶段的 prompt 要**横向比较**"其他所有发现"。

以下**不**构成用栅栏的理由：「我得先 flatten / map / filter」（放进 pipeline 的一个 stage 里做）、「这些阶段概念上独立」（那正是 pipeline 建模的，独立 ≠ 同步）、「代码更干净」（栅栏延迟是真实成本：5 个查找器最慢的是最快的 3 倍时，栅栏浪费快查找器 2/3 的空闲）。嗅探：若你 `parallel(...)` 后只是 flatten / map / filter 再 `parallel(...)`，中间那步不需要栅栏——改写成 `pipeline(items, stageA, 中间转换, stageB)`。拿不准就用 pipeline。

## 确定性约束（脚本编写规则）
- `META` 纯字面量。
- 加载期 lint **拒绝**非确定性来源：`time.time()` / `time.monotonic()` / `random.*` / `*.now()` / `*.today()`。需要时间戳就经 `args` 传入、事后盖戳；需要随机就按 index 改变 prompt / label。
- **闭包陷阱**：列表推导式里直接写 `lambda: agent(...)` 会让所有 lambda 捕获同一个（最后的）变量。必须 `lambda x=x: agent(...)` 绑定，或直接用 `map_parallel`。
- 无文件系统、无网络旁路——一切通过原语。

## 并发与上限
- 并发 cap = `min(16, CPU核数 - 2)`；超出的 `agent()` 排队，有空位再跑。仍可向 `parallel`/`pipeline` 传很多项，任一时刻只有约 cap 个在跑。
- 单工作流生命周期内 agent 总数上限 **1000**（超限 `agent()` 返回 `None`，不重试——失控兜底）。
- 单次 `parallel` / `pipeline` 最多 **4096** 项，超出**显式报错**（非静默截断）。
- `workflow()` 嵌套**仅一层**。

## 结构化输出（schema）
- `schema=None` → 返回文本；`schema=<JSON Schema dict>` → 返回 dict；`schema=<pydantic 模型>` → 返回模型实例（属性访问 + 静态类型收窄）。
- 校验失败由模型自动重试；耗尽返回 `None`。用 `compact()` / `.filter` 过滤 `None` 再用。

## 预算 budget（硬天花板）
- `budget.total`（用户本轮 token 目标，未设为 `None`）、`budget.spent()`（已花输出 token，跨主循环 + 所有工作流共享）、`budget.remaining()`。
- **硬天花板**：一旦 `spent()` 达到 `total`，后续 `agent()` 报错。据此动态决定深度或静态伸缩扇出（见骨架）。没设 `total` 时 `remaining()` 是无穷——动态循环必须 `guard budget.total`，否则会一直跑到 1000 上限。（注：调用前硬拦截实现推进中，当前为计数 + 脚本自检。）

## 断点续跑（resume）
- `resume_id` = 上次运行的 **run_id**。续跑时**内容寻址**：未变的 `agent()` 调用瞬时复用缓存结果；**上游 prompt 改 → 下游签名变 → 自动重跑**（无需手动标记）。**同脚本 + 同 args → 100% 缓存命中**。
- 由异步工具执行框架维护（与参考工具的 runId 机制一致）。（执行推进中。）

## 编排模式库（按任务规模组合）
- **对抗验证**：每个发现派 N 个独立怀疑者去**反驳**，多数反驳则淘汰——防"看似合理实则错"。
- **多视角验证**：给每个验证者不同视角（正确性 / 安全 / 性能 / 能否复现），优于 N 个相同反驳者。
- **评审团**：从不同角度生成 N 个独立方案 → 并行打分 → 从胜者综合、嫁接亚军好点子。
- **loop-until-dry**：未知规模的发现，持续派 finder 直到连续 K 轮无新增（简单计数会漏长尾）。
- **多模态扫描**：并行 agent 各以不同方式搜（按容器 / 内容 / 实体 / 时间），彼此盲。
- **完整性批评**：末尾一个 agent 专问"还缺什么——没跑的模态、没验的断言、没读的来源"。
- **不静默截断**：若限了覆盖（top-N / 抽样），用 `log()` 说明丢了什么。
- 规模匹配用户措辞："找几个 bug"→少量 finder、单票验证；"彻底审计 / 力求全面"→更大 finder 池、3–5 票对抗、加综合阶段。

这些模式**并非穷举**——任务需要时自造新编排（锦标赛两两对裁、自修复循环、分级升级，等等）。

## 代码骨架（真实 swarmflow API）
多维评审 —— 默认 pipeline，每个维度评审一完成就立刻对抗验证（'bugs' 在验证时 'perf' 还在评审，不浪费墙钟）：

```python
async def run(args):
    dims = [{"key": "bugs", "prompt": "找 bug"}, {"key": "perf", "prompt": "找性能问题"}]

    async def review(_prev, d, _i):
        return await agent(d["prompt"], label=f"review:{d['key']}", phase="Review", schema=FINDINGS)

    async def verify(rev, _d, _i):
        findings = rev["findings"] if rev else []
        return await parallel([
            (lambda f=f: agent(f"对抗验证：{f['title']}", phase="Verify", schema=VERDICT))
            for f in findings
        ])

    results = await pipeline(dims, review, verify)
    return {"confirmed": [f for rev in compact(results) for f in compact(rev) if f.get("is_real")]}
```

栅栏正确的场景 —— 昂贵验证前对全量发现去重（确需一次性拿到全部）：

```python
async def run(args):
    raw = await parallel([(lambda d=d: agent(d, schema=FINDINGS)) for d in DIMENSIONS])
    deduped = dedupe_by_file_and_line([f for r in compact(raw) for f in r["findings"]])  # 纯代码，不用 agent
    return await parallel([(lambda f=f: agent(verify_prompt(f), schema=VERDICT)) for f in deduped])
```

loop-until-budget —— 按预算缩放深度（用 `budget.total` 做守卫）：

```python
async def run(args):
    bugs = []
    while budget.total and budget.remaining() > 50_000:
        r = await agent("找这个代码库里的 bug。", schema=BUGS)
        bugs.extend(r["bugs"] if r else [])
        log(f"已找到 {len(bugs)} 个，剩余 {budget.remaining() // 1000}k token")
    return {"bugs": bugs}
```

loop-until-dry —— 持续派 finder 直到连续 2 轮无新增（对 `seen` 去重，不对 `confirmed`，否则被裁掉的发现每轮重现、永不收敛）：

```python
async def run(args):
    seen, confirmed, dry = set(), [], 0
    while dry < 2:
        found = compact(await parallel([(lambda p=p: agent(p, schema=BUGS)) for p in FINDERS]))
        fresh = [b for r in found for b in (r["bugs"] if r else []) if b["id"] not in seen]
        if not fresh:
            dry += 1
            continue
        dry = 0
        for b in fresh:
            seen.add(b["id"])
        confirmed.extend(fresh)
    return {"confirmed": confirmed}
```

本工具用于**控制流应当确定性**（循环、条件、扇出）而非由模型即兴决定的多步编排。
