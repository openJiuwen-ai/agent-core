# Goal 模式端到端（E2E）用例

从 team 公共入口验证 Goal 模式（`LeaderSpec.goal` / `TeamAgentSpec.member_goal`）。配套设计与实施归档：
`.agents/agent-docs/designs/active/D-20261110-1903-team-goal-mode/` 与
`.agents/agent-docs/features/F_20261110_1922_team-goal-mode.md`。

## 这套 E2E 是什么

`agent_team_goal_mode_e2e.py` 一个文件，九个场景。每个场景组一个开了 goal 的小团队，经
`Runner.run_agent_team_streaming` 给 leader 一条请求，跑完后检查 goal 模式实际做了什么，并**独立复核
交付物**（重新运行交付的脚本、重算哈希链），不采信 agent 自己的说法。

| 场景 | 验证的行为 |
|---|---|
| `basic` | autonomous、1 名成员：认领立 goal，提交经评估，leader 核对后才结束团队 |
| `lazy_autonomous` | 成员被要求"不干活直接提交"：首次提交被拒，补做后接受 |
| `lazy_scheduled` | scheduled 调度 + 偷懒成员：未经认领开工的任务，提交时同样被评估 |
| `multi` | 3 名成员、3 个依赖任务：每个任务各自立 goal、各自关闭 |
| `review_send_back` | scheduled + reviewer：验证打回后任务得到新的 goal |
| `plan_mode` | plan_mode：任务在计划批准时开工，仍然得到 goal |
| `iteration_limit` | 成员每轮只有 4 次内部迭代：只有 goal 续跑能把任务做完 |
| `reassign` | leader 中途改派：原持有者的 goal 被清除 |
| `blocked` | 严格串行的任务 + 只允许 1 次续跑：成员 goal 触顶阻塞并上报，leader 接手 |

所有场景共有的检查：用户请求成为 leader 的 goal；leader 起过 goal round；`team.completed` 只在 leader
的 goal 被评估为完成之后出现。

## 怎么跑

需要真实模型端点，从 `tests/system_tests/config_llm_local.yaml` 读（被 gitignore，首次使用从
`config_llm_local.example.yaml` 复制后填写）。用 `OPENJIUWEN_E2E_MODEL=<endpoint>/<model>` 临时换模型，
用 `OPENJIUWEN_E2E_LLM_CONFIG=<path>` 指向别处的配置文件。

```bash
python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py            # 全部场景
python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --list     # 列出场景
python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --scenario blocked --scenario reassign
python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --jobs 2   # 并发数，默认 4
```

每个场景在独立子进程里跑（见下文"探针"），单个场景上限 900 秒。任一场景有检查不过，脚本以非零退出。

## 产物在哪

全部落在本目录的 `.e2e_workdir/`（gitignore）：

```
.e2e_workdir/
├── <scenario>.out              # 该场景子进程的完整输出
└── <scenario>/
    ├── trace.log               # 一行一个 JSON 事件：goal 的立项 / 续跑 / 评估 / 清除、被拒的提交、team.* 标记
    ├── verdict.json            # 每条检查的结果与细节
    ├── project/                # 团队的工作目录，交付物在这里
    ├── team.db                 # 团队 SQLite 库
    ├── logs/                   # 框架日志
    └── openjiuwen_home/        # 成员工作空间，不落到用户真实 home
```

定位问题先看 `trace.log`：它是 goal 模式行为的时间线。

## 探针

goal 状态存在每个成员自己的 session 里，公共 team facade 不暴露它。`_GoalProbe` 因此包了一层
`GoalManager` 的 `set` / `begin_attempt` / `apply_assessment` / `clear`、`TeamGoalRail._reject` 和
`TeamGoalCoordinator._on_goal_changed`——所有 goal 变化都经过这几处。探针只记录，原样返回被包方法的结果。

这些是进程级的类，所以**一个进程只跑一个场景**；并发靠子进程。

## 模型不是确定的

三个场景依赖模型自己做出某个动作，`verdict.json` 的 `depends_on` 字段写明了依赖什么：

- `lazy_autonomous` / `lazy_scheduled`：成员得真的照"先提交再干活"的指示做，闸才有东西可拒；
- `review_send_back`：验证者得真的在第一次验证时打回；
- `reassign`：leader 得在原持有者还握着进行中任务时完成改派。

模型没走这条路时对应检查会失败，脚本会提示去看 `trace.log`。这种失败说明"这次没观察到"，不等于
goal 模式坏了；其余检查失败才应当先怀疑实现。

## 已知的无关问题

plan_mode 下已指派（带 assignee）的任务调 `submit_plan` 会报 `Failed to reserve task for planning`
（`TeamTaskManager` 只把无主的 PENDING 任务预留进 PLANNING）。`plan_mode` 场景因此在请求里要求"建任务但
不指派"。这个问题与 goal 模式无关。
