# IntelliRouter Allocation Strategy

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-17 |
| 范围 | `openjiuwen/agent_teams/models/`（pool + allocator）、`openjiuwen/agent_teams/schema/`（blueprint + team）、`tests/unit_tests/agent_teams/models/test_allocator.py`、`tests/system_tests/agent_swarm/agent_team_intelli_router_e2e.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 2005 passed / 16 skipped（其中 `test_allocator.py` 109 passed，新增 30）；E2E 真实端点跑通、exit 0 |
| Refs | #751 |

## 背景

`agent_teams` 原有三种分配策略（`round_robin` / `by_model_name` /
`router`）解决的是同一个问题：**把成员摊到多个端点上**，让并发调用不要
打爆单点。可靠性在这个模型里是 **allocator 的职责**——池里放 N 个端点，
分配器负责摊开。

`openjiuwen.core.foundation.llm` 侧另有一个 `IntelliRouterModelClient`
（包装第三方 `intelli_router.ReliableRouter`），它解决的是**同一个问题的
另一半**：一个 client 内部持有多个 deployment，**每次请求**在它们之间做
智能路由 + 自动重试 + 跨部署 failover + 限流感知（tpm/rpm）+ 健康检查。
可靠性在这个模型里是 **client 的职责**。

两者名字里都有 "router"，但处在不同层，此前没有任何接线：team 侧要用
IntelliRouter，只能手写 `model_pool` 条目、把 `api_provider` 填成
`intelli_router`、再把 deployments 塞进 `metadata.client` 的
`intelli_router_*` 键里——没有类型、没有校验、没有文档，填错就静默降级成
一个空凭证的 OpenAI client，直到第一次请求才炸。

本次把 IntelliRouter 提升为**与 `router` 并列的一等分配策略**，并在
`TeamAgentSpec` 上给它独立的结构化配置。

## 数据结构

```
TeamAgentSpec
├── model_pool: list[ModelPoolEntry]          ┐
├── model_router: ModelRouterConfig | None    ├─ 三者互斥
└── model_intelli_router: IntelliRouterConfig ┘
                    │
                    │ build() 展开（单向）
                    ▼
TeamSpec.model_pool: list[ModelPoolEntry]  +  model_pool_strategy="intelli_router"
                    │
                    ▼
        IntelliRouterAllocator（name → entry 唯一映射）
                    │
                    ▼
   ModelPoolEntry.to_team_model_config()
                    │  api_provider="intelli_router" → client_provider
                    │  metadata.client.intelli_router_* → ModelClientConfig extra
                    ▼
        create_model_client() → IntelliRouterModelClient → ReliableRouter
```

`IntelliRouterConfig.to_pool_entries()` 的展开形状：

| pool entry | model_name | api_key / api_base_url | api_provider | metadata.client |
|---|---|---|---|---|
| 0 | `"*"` | `""` / `""` | `intelli_router` | 全量 deployments + 路由旋钮 |
| 1..N | 各 deployment 的 distinct model_name | `""` / `""` | `intelli_router` | **同上（全量）** |

两个关键点：

1. **每条 entry 都带全量 deployment 列表**——不是把 deployment 拆开分给
   不同成员。成员之间的差异只有"向 router 要哪个 model_name"，failover
   由 router 在请求期完成。
2. **entry 自身的 `api_key` / `api_base_url` 恒为空**——凭证是 per-deployment
   的；`intelli_router` 不在 foundation 层的 `_TOP_LEVEL_API_KEY_PROVIDERS` /
   `_TOP_LEVEL_API_BASE_PROVIDERS` 集合里，因此顶层空值合法。

## 决策

1. **并列策略，不是替代**。`model_pool_strategy` 增加第四个值
   `"intelli_router"`，`build_model_allocator` 增加一路派发。前三种策略把
   成员摊到端点上（allocator 负责可用性），`intelli_router` 把多端点整个
   下沉给 client（router 负责可用性）。**两者应二选一**——池里放多条
   intelli_router entry 再套 `round_robin` 是两层都在做负载均衡，职责重复。
   这条判断写进了 `allocator.py` 的模块 docstring。

2. **`IntelliRouterConfig` 是 spec 上的独立字段，与 `model_pool` /
   `model_router` 三者互斥**。原 `_validate_pool_router_exclusive` 只比较
   两个字段，改成列表式检查，冲突时把实际配了哪几个一并报出来。

3. **展开成扁平 pool 视图，下游零改动**。与 `model_router` 走同一条路子：
   `build()` 里 `to_pool_entries()` + 强制 strategy。`resolve_member_model` /
   `inherit_pool_ids` / `update_model_pool` / DB ref 全部复用既有路径，
   **没有一处 intelli_router 专用分支**。这是 S_11 既有设计的直接延续。

4. **`"*"` 统一路由排在展开结果首位**。`RouterAllocator.allocate()` 无 hint
   时返回 `pool[0]`，所以 leader 不配 `model_name` 就自动拿到跨全部
   deployment 的 failover——即池里**可用性最高**的那一档。这是"默认即最可靠"，
   不需要为 leader 加任何特判。用户可用 `model_names` 显式改顺序。

5. **`IntelliRouterAllocator` 继承 `RouterAllocator`，只加校验不加行为**。
   两者的分配语义**逐字相同**：name 唯一映射、无 hint 取首条、未知名返回
   `None`、无 rotation 计数器。差异只在 entry 的**含义**（一个远端端点
   vs 一整个 deployment 列表），那是 pool 怎么建出来的属性，不是怎么分配的
   属性。子类加的是构造期校验：`api_provider` 必须是 `intelli_router`、
   `metadata.client.intelli_router_deployments` 必须非空——针对用户**手写**
   pool + `strategy="intelli_router"` 的路径（`to_pool_entries` 生成的
   provider 是写死的，不可能错）。

6. **`IntelliRouterDeployment.api_base` 不带 `/v1`，且必须写进文档**。这是
   本次唯一一处与仓库其它地方**约定相反**的字段：openjiuwen 的
   `ModelClientConfig.api_base` 指向 OpenAI 兼容 API 根、通常以 `/v1` 结尾；
   而 intelli_router 的 provider adapter 自己拼
   `f"{api_base}/v1/chat/completions"`。传入带 `/v1` 的值会得到
   `/v1/v1/chat/completions` → 404。**且报错完全不像 404**：router 的错误
   处理路径在未 `read()` 流式响应的情况下读 body，抛出的是
   `ResponseNotRead`，真实的 404 被吞掉。这个坑在 e2e 上真实踩到过，
   因此 `api_base` 的 docstring 用了整段来讲，不是一句话带过。

7. **`model_names` 显式配置时校验"该名字有 deployment 服务"**。空/重复/
   空白照抄 `ModelRouterConfig` 的三条；额外加一条：每个名字必须是 `"*"`
   或某个 deployment 的 `model_name`。否则展开出的 entry 分给成员后，
   router 在请求期才发现路由不到——能在 spec 层拦下的错误不留到运行期。

## 拒绝的方案

- **不加新策略，让用户手写 `model_pool` + `provider=intelli_router`**。
  这是本次之前**已经能用**的路径，也正是问题所在：没有类型、没有校验、
  deployments 是一坨裸 dict 埋在 `metadata.client` 里，`api_base` 的 `/v1`
  陷阱毫无提示。"能用"和"能对着用"是两回事。

- **`IntelliRouterAllocator` 独立实现分配逻辑，不继承**。写出来会和
  `RouterAllocator` 逐字相同（同样的 `_by_name` 字典、同样的首条兜底、
  同样的 digest-only state）。为了"两个策略看起来对称"复制一遍实现，是用
  重复代码换视觉整齐。继承在这里是真实的 IS-A：intelli_router pool 就是
  router pool，只不过那个"单端点"在客户端。

- **`to_pool_entries()` 自动剥掉 `api_base` 尾部的 `/v1`**。静默修改用户
  输入。有的网关确实在 `/v1` 之下还有路径，猜错了就更难查。正确做法是把
  约定讲清楚 + 在 e2e 里显式转换（`_to_router_base`），让转换发生在**用户
  的代码**里而不是框架内部。

- **未知 `model_name` 回退到 `"*"` 统一路由**。表面上"更可靠"（IntelliRouter
  本来就能路由任意模型），实际是把配置错误（打错模型名）静默变成"跑起来了
  但用的不是你要的模型"。同时破坏 S_11 不变量 #4 的 `None` 语义。保持与
  `RouterAllocator` 一致：未知名 → `None` → 走 per-agent 兜底或在 leader
  路径上 fail fast。

- **每条 entry 只带"服务该模型的那几个 deployment"**（评审时提出：
  "intelli router 本质上是一次配置多个模型、成员只传模型名即可，为什么每个
  成员都拿全量配置各自建 client？"）。这是最直觉的优化，也**正是本设计里
  唯一不能动的地方**——

  `IntelliRouterModelClient` 按 client-config 缓存 `ReliableRouter`，
  cache key 由 **deployment 列表 + 路由旋钮**算出，**不含 `model_name` /
  `client_id`**。全量且逐字相同 ⇒ 所有成员命中**同一个 router 实例**（已
  实测：3 条 entry → 3 个 client 包装对象 → **1 个** `ReliableRouter`）。
  按模型名裁剪 deployments ⇒ key 发散 ⇒ 每个成员各建一个 router ⇒
  **各自独立计 rpm/tpm**，4 人团队实际花掉声明配额的 4 倍，failover 记录
  与健康检查也互不可见。

  所以"全量重复"不是冗余，是共享的**前提**；"各自建 client"也不是浪费——
  差异只在 `ModelRequestConfig.model_name`（每人 pin 的模型），client 包装
  是薄的，重的东西只有一份。用户面向的 API 依然是"配一次、成员只写
  `model_name`"（`IntelliRouterConfig` + `TeamMemberSpec.model_name`），
  展开纯属内部实现。

  代价是同一份 deployments 在 `TeamSpec.model_pool`（进 session checkpoint）
  里存 N 份（N = 逻辑模型名数量，通常个位数），量级几 KB，换掉的是打破
  `ModelPoolEntry` 自包含契约、改 `to_team_model_config()` 签名、波及全部
  四种策略。**代价不对等，接受重复。**

  该不变量隐式且脆弱（改 `_client_extra()` 就可能悄悄破坏，测试还全绿），
  故由 `test_intelli_router_all_entries_share_one_router_cache_key` 钉死，
  并做过 mutation 验证：按 model_name 过滤 deployments → 立即变红。

- **只展开一条 `model_name="*"` 的 entry**。最简单，但成员就没法 pin 具体
  模型（池内路由），失去"leader 用统一路由 / teammate 各用一档模型"这种
  表达力，而这正是 e2e 要验证的场景。

- **把 IntelliRouter 与 `round_robin` 叠加使用**（池里多条 intelli_router
  entry）。两层都做负载均衡，概念打架。选一层拥有多端点可靠性——这条写进
  了 allocator 模块 docstring，不只是本文档。

## 验证

- **单测**：`tests/unit_tests/agent_teams/models/test_allocator.py` → **109 passed**
  （新增 30 条，既有 79 条无回归）。覆盖展开形状、全量 deployment 下发、
  可选字段省略/保留、路由旋钮透传、生成键覆盖 metadata、per-entry metadata
  隔离、四条 `model_names` 校验、allocator 名字查找/默认/未知名/确定性、
  两条构造期校验、digest-only state round-trip、`build_model_allocator`
  派发、物化到 `ModelClientConfig`、`resolve_member_model`、凭证轮换破坏
  `model_id` 继承、三者互斥、build 强制 strategy、leader 默认 `"*"`、
  leader 显式 name、leader 未知 name fail fast。
  **单测不依赖可选包 `intelli_router`**——止步于 `ModelClientConfig` 物化，
  不构造真实 client，因此 CI 无需安装该包。

- **E2E**：`tests/system_tests/agent_swarm/agent_team_intelli_router_e2e.py`
  （1 leader + 3 teammate，真实端点，**exit 0 自动退出**）。离线断言（无网络）
  验证展开形状与四个成员的分配；在线部分驱动真实团队跑一轮。运行需
  `intelli_router` 可选依赖 + 真实端点。实跑结果：

  ```
  pool entries: 4 (每条都带全部 5 个 deployment)
  leader team_leader -> *                  (unified routing across all deployments)
  member alice       -> deepseek-v4-flash  (routes within 2 deployments)
  member bob         -> Qwen3.7-Plus       (routes within 1 deployment)
  member carol       -> GLM-5.2            (routes within 1 deployment)
  unknown model_name -> None
  → 三名成员各自的模型均成功应答，leader 汇总完成，0 次 execution failure
  ```

  E2E 用 `lifecycle: temporary` 而非 `persistent`：leader 完成即拆队，脚本
  自行退出（persistent 会 pause 等待下一轮输入而挂住），且每次运行从干净
  名册开始，规避下面"已知遗留"里 `model_ref` 陈旧的坑。

- **真实链路验证**（本次归档时手工确认）：`GLM-5.2` / `deepseek-v4-flash` /
  `"*"` 三者的 `invoke` 与 `stream` 均跑通；DB 中 leader 的 `model_ref`
  正确落为 `{'model_name': '*', 'model_index': 0}`，印证 S_11 不变量 #2
  的持久化身份。

- **E2E 的验证边界**：initial_query **不**让成员自报模型名。LLM 无法内省
  服务自己的部署，问了只会编（首版 query 这么写，三个成员一致答"Claude"）。
  成员用哪个模型由离线断言负责；在线一轮只证明"三条路由都真的解析并服务了
  请求"——三人各 pin 不同模型，三人都答上来即等价。

## 已知遗留

- **上游 `intelli_router` 包在 uv 的 git subdirectory 路径下装不上**
  （`pyproject.toml` 的 `intelli-router` extra 指向
  `git+https://gitcode.com/openJiuwen/agent-protocol.git@feature/intelliRouter#subdirectory=intelli_router`，
  uv 构建报 hatchling 找不到 packages）。从本地 clone 目录 `uv pip install
  <path>` 可正常安装，说明是 uv 的 subdirectory 解析问题而非包本身。
  未深究，也未改 `pyproject.toml`——上游归属不在本模块。

- **上游 `ResponseNotRead` 掩盖真实 HTTP 错误**：`intelli_router` 的错误
  处理路径在流式响应上未 `read()` 就读 body，导致任何上游 4xx/5xx 都表现为
  `ResponseNotRead`，排查成本极高。本次只在 `api_base` docstring 里记下这个
  现象，未向上游提修复。

- **persistent team 改 `model_names` 后 DB 里的 `model_ref` 不会更新**。
  这是框架既有行为（member 行一旦建立，spec 变更不回写；见 S_11 不变量 #2），
  不是本次引入。但对反复改配置的场景是个真实绊脚石——本次调试中就因为 DB
  残留旧的 `Qwen3.7-Max`（改名前的值）而让一个成员 `resolve_member_model`
  拿到 `None`，报 "model_client_config is required"，错误信息完全指不到根因。
  E2E 已改用 `temporary` 规避，脚本 docstring 也注明了 persistent 场景下改名
  需清理 team 行。是否值得提供"spec 变更时对齐 member model_ref"的能力（或
  至少在 pool 里找不到某个已存 `model_ref` 时告警），留待后续判断。

- `IntelliRouterConfig` 未暴露 `intelli_router` 的 `tag-based` 策略所需的
  per-request tag 选择——`tags` 已能配在 deployment 上，但团队侧没有把
  "这个成员只用带某 tag 的部署"表达出来。有需求时再加，不预先设计。
