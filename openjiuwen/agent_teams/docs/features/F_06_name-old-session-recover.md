# run_agent_team* input contract collapse to `spec+new_session` / `name+old_session`

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-13 |
| 范围 | `openjiuwen/core/runner/team_runner.py`；`tests/unit_tests/agent_teams/test_runner_team_runtime.py`；同步 `docs/specs/S_06_runtime-pool-dispatch.md` |
| 测试基线 | `make test TESTFLAGS="tests/unit_tests/agent_teams tests/unit_tests/core/runner"` → 1370 passed, 18 skipped |
| Refs | `#751` |
| Commit | `e2ed1d06` |

## 背景

F_05 把 dispatch truth table 收敛到 7 路、把 lifecycle 决策搬进 `TeamRuntimeManager.finalize`。
此时 `run_agent_team*` 这一对入口的合法调用形态实际已经只剩两种：

| 形态 | 触发路径 |
|---|---|
| `spec + new_session` | 首次启动（`CREATE`）；在新对话上把 stopped 团队复活（`NEW_TEAM_IN_SESSION` + `recover_team`） |
| `name + old_session` | 复用 pool 中的同会话 entry（`RESUME_FROM_PAUSE`），或在 pool 已被 `stop_team` / 进程重启清空后冷恢复（`COLD_RECOVER`） |

但 `_resolve_team_agent_spec` 的 `name` 分支当时只读 pool：

```python
entry = await self._get_team_runtime_manager().pool.get(agent_team)
if entry is None or entry.agent.spec is None:
    raise_error(AGENT_TEAM_CONFIG_INVALID, reason="team ... is not active; the first run_agent_team call ... must pass a TeamAgentSpec to seed the pool")
return entry.agent.spec
```

结果是：

- `stop_team` 之后 pool 被清空——按 F_05 的不变量，"stop_coordination ⇒ pool.remove" 是固化约定——下一次想用 `name` shorthand 触发 cold recover 直接被 reject。caller 必须再次重新构造一份 `TeamAgentSpec` 才能走 spec 路径，而那份 spec 在 session bucket 里其实早已落盘。
- pause 阶段进程异常退出（OOM / SIGKILL / crash）后，新进程的 pool 是空的，但 session checkpoint 仍带着完整的 `state["teams"][team_name]` bucket。重启后想用 `name + old_session` 顺势 cold recover——同样被这个 reject 挡掉。
- 错误消息 `"team ... is not active; the first run_agent_team call ... must pass a TeamAgentSpec to seed the pool"` 误导 caller："是不是这个 team 从来没活过？我应该重新创建？"——但其实 team 在 DB 里、spec 在 session bucket 里、只是 pool 没装。

也就是说，把"pool 是 in-memory cache，必要时从 session bucket 反推 spec"这条 fallback
明确化前，`name + old_session` 这条入口契约只剩"pool hit"一半可用，"pool miss → cold
recover" 那半没接通。

## 决策

在 `_resolve_team_agent_spec` 的 `name` 分支加一条 fallback：pool 缺失时尝试从入参
`session` 的 team bucket 读出 `spec_data` 反序列化。两源都拿不到才报错。

### 1. `_resolve_team_agent_spec` 新增 `session` 形参

签名变更：

```python
async def _resolve_team_agent_spec(
    self,
    agent_team: Union[str, "TeamAgentSpec"],
    *,
    session: Optional[Union[str, AgentTeamSession]] = None,
) -> "TeamAgentSpec": ...
```

`run_agent_team` / `run_agent_team_streaming` 把自身收到的 `session` 透传进去。`session`
默认 `None`——保留首次启动场景下 caller 不必传 session 的能力（spec 路径不需要从 bucket
读）。

### 2. `name` 分支两级 lookup：pool → session bucket → 拒绝

```python
if isinstance(agent_team, str):
    entry = await self._get_team_runtime_manager().pool.get(agent_team)
    if entry is not None and entry.agent.spec is not None:
        return entry.agent.spec
    if session is not None:
        spec_from_bucket = await self._resolve_spec_from_session_bucket(
            team_name=agent_team, session=session,
        )
        if spec_from_bucket is not None:
            return spec_from_bucket
    raise_error(
        StatusCode.AGENT_TEAM_CONFIG_INVALID,
        reason=(
            f"team '{agent_team}' has no live pool entry and no persisted "
            f"spec in the supplied session; first-time runs must pass a "
            f"TeamAgentSpec on a new session"
        ),
    )
```

错误消息也升级——明确两个源（pool / session bucket）都查过都空，引导 caller 改用
`spec + new_session`。

### 3. 新增 `_resolve_spec_from_session_bucket` 静态 helper

负责把"从 session 反推 spec"这条只读路径独立出来，保持 `_resolve_team_agent_spec`
本身只做参数类型识别 + 两源 dispatch：

```python
@staticmethod
async def _resolve_spec_from_session_bucket(
    *,
    team_name: str,
    session: Union[str, AgentTeamSession],
) -> Optional["TeamAgentSpec"]: ...
```

- 接受 `str | AgentTeamSession`：字符串走 `create_agent_team_session(session_id=...)`，
  对象直接用；与 Runner 一贯入参语义一致。
- `await team_session.pre_run()` 还原 checkpoint。失败（不存在的 session、序列化异常等）
  返回 `None` + warning log，**不上抛**——上层根据"两源都空"给出统一错误更清晰；
  让 checkpoint 内部异常贯穿出来反而会让 caller 误以为 "session 出问题"，跟实际语义
  （"该 team 没在这个 session 里跑过"）不符。
- `read_team_namespace(session, team_name)` 取 bucket；bucket 不存在或没有 `spec`
  键返回 `None`。
- `TeamAgentSpec.model_validate(spec_data)` 反序列化。解析失败同样吞掉 + warning。

### 4. `agent_customizer` 字段在 `name` 路径上不可恢复

`TeamAgentSpec.agent_customizer` 是 `Field(exclude=True)` 的 Callable，序列化时不写
入 session bucket。`recover_from_session` 的 `runtime_spec` 注入机制（F_05 之前就有）
专门为 cold recover 重新塞回这条 callback——但 `name` 路径上 caller 没传新 spec，
没有 `runtime_spec`，customizer 只能保持 `None`。

docstring 里把这条契约写明：依赖 customizer 的 caller（jiuwenclaw 等平台适配器）
必须用 `spec` 形态调用，不能用 `name`。

## 拒绝的方案

### A. 给 `team_info` 表加一列 `last_session_id`，`name` 不传 session 时自动反查

让 caller 退化到 `Runner.run_agent_team(agent_team="X")` 也能恢复——manager 从 DB
读 last_session_id，再用那个 session 拉 spec。

否决理由：

1. 引入新 schema 列必须同步 migration、`build_team` / `persist_leader_config` 两处写入，
   且要解决"多 session 同时跑同 team"下"哪个 session 算 last"的并发语义。本来一句
   "调用方必须显式传 session" 就够清楚的契约硬被一列 DB 字段承担。
2. 真实使用场景下 caller（jiuwenclaw / TUI / SDK 用户）都知道自己在哪个 session 上
   工作——session id 是业务层级的概念，没理由让基础设施层揣测。
3. 跟 F_05 的整体方向（去 in-memory 漂移 / 把状态权责往清晰边界搬）相反——这是把
   隐式状态加回来。

### B. 让 `name + new_session` 也合法，自动从 bucket 反推或重建

当前实现：`name + new_session`（bucket 不在该 session 里）→ 两源都空 → 报错。一种
替代是退化到"找任意一个存有该 team 的 session 反推 spec"，让 SDK 调用更宽松。

否决理由：

1. "任意 session" 的语义模糊——多 session 共用同一 team_name 时具体选哪个？最近的？
   随机的？没有自然答案。
2. session_id 本身就是 spec / context 的一部分（`persist_leader_config` 把当前
   session 关联的 spec 写下来）——跨 session 拿 spec 暗示"spec 与 session 解耦"，
   但实际两者强相关（model_allocator_state 等都按 session 持久化）。
3. 与 F_05 "切 session 一律 cold rebuild" 的方向冲突——cold rebuild 期望显式语义，
   不该让"找一个能用的 session"变成隐式 fallback。

### C. 在 `Manager.activate` 而非 `Runner._resolve_team_agent_spec` 上做反推

让 `manager.activate` 接受 `Union[str, TeamAgentSpec]`，把"字符串反推 spec" 从
Runner 层挪到 Manager 内部。

否决理由：

1. `Runner._resolve_team_agent_spec` 已经承担"参数类型识别"的层级职责——把 BaseTeam
   path / member path / spec path / name path 在同一处分流。把 `name` 反推塞进
   manager 会让"输入归一化"的责任分散到两层。
2. Manager 的 `activate(spec, session, inputs)` 签名稳定（其他调用方也用），改成
   `Union[str, TeamAgentSpec]` 会拖出一票 type narrowing 改动。
3. session bucket 反推本身并不依赖任何 Manager 私有状态——纯粹是
   `(team_name, session)` 二元到 `Optional[TeamAgentSpec]` 的只读函数；放在
   Runner 内部 staticmethod 跟其它 `_resolve_*` helper 风格一致。

### D. 把现状当不需要修：让 caller 显式重传 spec

把 `name + pool miss` 当成"过期的捷径"，要求 caller 在 pool 失效后切回 spec 路径。

否决理由：

1. 这是 contract regression：F_05 之前 `name` shorthand 至少在 pool 命中时可用，
   F_05 之后我们承诺 `name + old_session` 同时覆盖 RESUME 与 COLD_RECOVER。如果 cold
   path 走不通，承诺等于没兑现。
2. 主要场景是 pause 异常退出后的 recover——caller 此刻能拿到的最方便的 handle
   就是 team_name + session_id（在业务层都已记账），让他们重新构造 spec 是反人类。
3. spec 在 session bucket 里完整存在却拒绝读，是把"已经持久化的事实"故意忽略——
   反 KISS。

## 数据结构 / 状态机

无新状态字段。`_resolve_team_agent_spec` 的决策表（输入 → 输出）：

| 入参 `agent_team` | 入参 `session` | pool 命中 | bucket 命中 | 行为 |
|---|---|---|---|---|
| `TeamAgentSpec` | * | * | * | 直接返回 spec |
| `str` | * | ✓ | * | 返回 `pool_entry.agent.spec` |
| `str` | non-None | ✗ | ✓ | 反序列化 bucket 中的 spec 返回 |
| `str` | None | ✗ | — | 报错（"has no live pool entry and no persisted spec ..."） |
| `str` | non-None | ✗ | ✗ | 报错（同上） |
| 其它类型 | * | * | * | 报错（"accepts str \| TeamAgentSpec ..."） |

注：

- `*` 表示该维度不影响判定。
- spec 路径不读 session bucket——caller 传 spec 时已经明确"我用这份 spec"，
  即便 session 里另有 bucket 也以入参为准。
- `str + non-None session + pool miss + bucket miss` 与 `str + None session + pool miss`
  共用同一错误消息——两种"没源"都属于"该 team 不存在 / 没在这个 session 跑过"，
  统一引导 caller 改用 `spec + new_session`。

## 验证

- `make test TESTFLAGS="tests/unit_tests/agent_teams tests/unit_tests/core/runner"` →
  1370 passed, 18 skipped。
- 现有用例 `test_run_agent_team_rejects_unactivated_team_name` 的错误消息断言从
  `"is not active"` 更新为 `"has no live pool entry"`，覆盖 caller 既没传 session
  也没活跃 pool 的拒绝路径。
- `test_run_agent_team_resolves_team_name_via_pool` 仍然覆盖"pool 命中"路径，
  无需调整。
- session bucket 反推路径目前依赖 `test_runner_session_switch_stops_and_rebuilds`
  这类间接断言（round-end persist_leader_config 写 bucket、下次 dispatch 读
  bucket）。下次往 `test_runner_team_runtime.py` 加 case 时应补一条"`name +
  old_session` 经过 process-restart 模拟后仍能 cold recover" 的直接断言。

## 已知遗留

1. `agent_customizer` 在 `name` 路径上始终为 `None`。jiuwenclaw 这类平台适配器
   如果走 `name + old_session` 走 cold recover，会丢失 customizer 注入的 rails / 工具
   hook。当前规避：依赖 customizer 的 caller 强约束走 `spec` 形态。下次评估是否
   把 customizer 改成"按 callable 注册表反查"——以名字索引、跨 session 持久化——而
   不是把 callable 直接塞 spec。
2. `Runner.run_agent_team(team_name, session=None)` 仍然报错（保留 F_05 之后的入参
   契约）。若未来产品侧需要"只传 team_name 自动恢复"，按 § 拒绝的方案 A 的代价
   重新评估。
3. 未来在 docs/specs/S_06 / S_04 增写"`name + old_session` 反推路径"的契约段时，
   注意 S_04 当前仍含 `unbind_session` 三方法的描述——那是 `2153078d` (contextvar
   单源) 没同步的遗留债务，不属于本 feature。同步时一并修订。
