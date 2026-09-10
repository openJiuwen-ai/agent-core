# agent 层 span 与 team 增量拆成两个独立 rail

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-17 |
| 范围 | `openjiuwen/harness/observability/`（新 `rail.py` / `subagent.py`）、`openjiuwen/agent_teams/observability/rail.py`、`openjiuwen/agent_teams/rails/elements.py`、`openjiuwen/agent_teams/rails/subagent_elements.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/harness/manifest/builtin_elements.py`、`openjiuwen/extensions/observability/semconv.py`、`docs/specs/S_14`、`docs/features/F_37` |
| 测试基线 | `pytest tests/unit_tests/harness/observability tests/unit_tests/agent_teams tests/unit_tests/extensions/observability` → 2693 passed / 39 skipped / 3 xfailed |
| Refs | #1013 |

## 背景

agent 层 span（`agent.{name}.task_iteration.{n}` / `agent.{name}.invoke`）的开关、嵌套判据、
孤儿排空，对单 agent 和 team 成员是同一件事，但实现只有一份，且长在 team 包里
（`agent_teams/observability/rail.py`）。平台侧单 agent 想复用它，只能从外面打补丁：

- 伪造一个合成 `team_name`（`SINGLE_AGENT_TEAM_NAME = "single-agent"`）骗过 rail 的 team 门禁，
  否则单轮 agent 拿不到任何 agent 层 span，subagent 的 `invoke` span 直接平摊到 run root 下；
- 猴补丁改写 `ObservabilityRail._stamp_agent_attributes`，再重绑该 span 的 `set_attribute`，
  把 rail 刚写上去的 `agentteam.*` 逐键过滤掉——因为单 agent 根本没有 team 身份。

结果是同一段逻辑两套语境：team 场景的修复不会自动惠及单 agent，单 agent 攒下的修复（跨 task
的 root span 兜底、subagent 在派发点挂 rail、run root 先 end 再 flush）也回不到 team。补丁本身
还很脆——它依赖 rail 私有方法名和 span 对象可写属性。

## 决策

1. **agent 层下沉到 harness，team 层只保留增量**。`AgentObservabilityRail`
   （`harness/observability/rail.py`，priority 10）拥有 span 的开/关、`AgentSpanScope`、
   嵌套判据、孤儿排空，完全不认识 team；`TeamObservabilityRail`
   （`agent_teams/observability/rail.py`，priority 12）只做两件事：贡献 `agentteam.*` 身份块、
   把 leader 的轮次结果打到 team root span 上。
2. **用组合而不是继承，交接契约是 `AgentSpanDecoration`**。team rail 在 `before_*` 把
   `attributes` + `input/output_attribute_keys` 停放到 `ctx.extra`，agent rail 开 span 时套用、
   关 span 时把脱敏后的 output 镜像到指定键。可行性来自两个既有事实：回调按 priority 降序执行
   且 `after_*` 不反序（`core/runner/callback/framework.py`），一轮迭代/一次 invoke 只建一个
   `ctx` 且 before/after 与所有 rail 共享它（`task_loop_event_executor.py` / `deep_agent.py`）。
   因此"高优先级的贡献者先跑，低优先级的所有者后跑"在四个 hook 上都成立。
3. **门禁改为通用的 run root**。rail 不再问"有没有 team span"，只问
   `extensions.observability.span_context.get_root_span()` 有没有在录——team 的 `team.{name}`
   和单 agent 的 `agent.{mode}.{session}` 都是 run root。合成 team 名与属性过滤补丁随之删除，
   不是搬家而是结构性消失。
4. **孤儿判据补上 trace 维度**。`_drain_or_clear_stale` 原先只比 member 名，拆分后通用侧改用
   `deepagent.agent.name`（新增 semconv 键，见「拒绝的方案」第 3 条），必须同时满足
   **同名 + 同 trace** 才当作自己的孤儿去 end。并发 session 跑同一个 agent 时，名字必然相同，
   只按名字判会 end 掉另一条还在跑的 trace 的 agent span。
5. **挂载成对声明**。`core.observability` 降为 harness 内置元素，`core.team.observability` 留在
   team；`agent_configurator` 给每个成员挂这一对。SDK 侧 `maybe_observability_rails()` 是唯一
   的"挂 team 观测"入口，避免调用点各记一半。
6. **subagent 只挂 agent rail**。subagent 是被派发的工作，没有 member id / role / 信箱，
   `agentteam.member.name` 填进去的其实是 subagent 类型名，等于声称一个它没有的身份。
   团队归属靠结构表达——subagent span 嵌在派发它的 member span 下。

## 拒绝的方案

1. **team rail 继承 agent rail**。能跑，但把"团队身份"和"span 生命周期"重新焊在一条继承链上：
   子类要么覆写 `before_*` 再 `super()`，要么依赖父类留的 protected 钩子，两种都让父类的每次
   改动都要考虑子类语境——正是本次要拆掉的耦合。组合可行的前提已验证（决策 2），没有理由退回继承。
2. **保持单 rail，让它按 `team_name` 分支**。这就是现状：一个类里两套语境，team 侧的 bug 修完
   单 agent 不受益。分支不会随时间收敛，只会继续长。
3. **孤儿判据继续复用 `agentteam.member.name`**。通用逻辑不该依赖 team 命名空间；而且单 agent
   场景下这个属性被补丁抹掉了，比较**永远不相等**，一律走"别人的 span，不要动"分支——旧代码在
   单 agent 下的并发安全是巧合而非设计。改用 `deepagent.agent.name` 后必须显式补 trace 判据。
4. **team rail 用更高优先级抢在 agent rail 之后关 span**。priority 是每 rail 一个值，四个 hook
   共用同一个序：让 team 的 `after_*` 晚于 agent 关 span，就必然让它的 `before_*` 也晚于 agent
   开 span，那时贡献已经来不及了。output 镜像键随 scope 走到 `close()` 才是正解。
5. **给 subagent 也挂 team rail以保持 `agentteam.*` 齐全**。见决策 6：齐全的代价是写入一个假身份。

## 验证

- `pytest tests/unit_tests/harness/observability tests/unit_tests/agent_teams tests/unit_tests/extensions/observability`
  → 2693 passed / 39 skipped / 3 xfailed。
- team 既有的 70 个 span 树 / 属性断言**未改断言**，只把 `rail = ObservabilityRail()` 换成成对
  触发的 `_TeamRails()`——行为等价的主要证据。
- 新增覆盖：decoration 交接（含"不串到另一个 agent 的 span"）、priority 契约、两个元素名各自
  解析到哪个 rail、subagent 只拿到 agent rail、subagent 嵌到派发它的 tool span 下、
  并发 session 同名 agent 不被误 end（反向验证：去掉 trace 判据该用例立刻红）。
- 平台侧（jiuwenclaw）`pytest tests/unit_tests/agentserver` → 1871 passed，唯一失败是既有的
  warm-pool tmp_path 大小写用例，与本次无关。

## 已知遗留

- team 模式下 subagent 的 llm/tool 子 span 不再从父 span 复制 `agentteam.member.name`
  （`callback_handler._stamp_parent_member_name` 取不到就不写）。按 member 属性过滤的看板不会
  命中 subagent 那一段，需要改看树结构。若后续确认有强需求，正确做法是给 subagent 一个明确的
  "归属 member"属性，而不是把它伪装成 member。
- 一个 DeepAgent 实例被并发复用时，rail 实例上的 `_open_invoke_span` 仍是单槽（拆分前同样如此）。
  当前各 session 一个实例，未触发；真要并发复用需改为按 ctx 存放。
