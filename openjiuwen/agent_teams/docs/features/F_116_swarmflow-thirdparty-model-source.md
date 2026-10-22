# Swarmflow 三方节点的模型来源解析（`model` / `builtin_model` / `effort`，无回退）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-08 |
| 范围 | `workflow/backends/external_model.py`（新）、`external/cli_agent/model_compat.py`（新）、`workflow/engine/{primitives,backends/base,errors,journal,progress}.py`、`workflow/backends/{team_worker_backend,avatar_session_backend,external_turns}.py`、`workflow/{runner,tool_swarmflow}.py`、`external/member_runtime.py`、`spawn/external_cli_spawn.py`、`tools/{tool_member,tool_factory}.py`、`tools/locales/descs/{cn,en}/workflow/swarmflow.md` |
| 测试基线 | `test_external_model.py`（resolution 场景）、`test_engine_retryable.py`（非重试 + 签名）、`test_worker_backend.py::test_*_model*`（native pool-miss / 三方发射）、`test_avatar_session_backend.py`、`test_external_turns.py`（双受众认证文案）、`test_agent_session_model_lock.py`（首锁）、`test_engine.py` / `test_budget.py`（AGENT_FAILED 一次）、`test_journal.py`（`builtin_model`/`effort` 折入签名）、`test_member_runtime.py`、`test_cli_workflow_runtime.py`、`test_swarmflow_locales.py`；`workflow` + `external` + `tools` + `spawn` 全套通过 |
| 关联 | `F_114`（三方 worker / 会话）、`F_113`（内置模型选择）、`S_18`（WORKER 不变量 5 / 有状态会话契约）、`S_27`（`build_cli_workflow_runtime` 模型接线） |

## 背景

`F_114` 让 swarmflow 脚本把节点派给三方 CLI Agent，但模型只有一条路：`model` hint 命中团队 model pool
就用 pool 端点。两个缺口：

1. **订阅侧模型不可达**：Claude Code / Codex 跑在 CLI 自身登录（订阅）上时，leader 没有任何入口让某个
   三方节点用订阅内置模型 + 推理强度——`F_113` 给团队成员开了 `builtin_model` / `effort`，swarmflow 编排侧
   还是只能用 pool。
2. **失败即静默降级**：hint 落空（不在 pool / 协议不兼容 / 认证失败）时旧行为是回退——或继承团队 teammate
   模型、或透传入口的 `model_override`、或由引擎自动重试。leader 看到的是"跑完了但用了另一个模型"或反复
   重试后的 `None`，根因从不露面。

## 决策

1. **三参数互斥语义，`plan_external_model` 是唯一解析点**（`workflow/backends/external_model.py`）：
   `model` 走团队 model pool（按名分组取首条 + 该 CLI 的 provider 过滤），命中 → `ExternalCliModelConfig`
   端点配置（`api_base` / `api_key` / `provider="jiuwen"`）；`builtin_model` 走该 kind 声明目录（`effort`
   从该模型的 `efforts` 取，省略取 `default_effort`）→ 无端点配置的"CLI 自身登录"配置；两者**互斥**；
   `effort` **必须伴随 `builtin_model`**；三者全留空 → `primary=None`，CLI 用自己的默认模型。native 节点
   提交 `builtin_model` / `effort` 是脚本错误，`cli_agent=None` 规则非重试拒绝。`plan_external_model` 是
   纯解析器：只读 hint 与 team backend 的 pool / 目录，从不 spawn、不分配。
2. **绝不回退**。拒绝了四条路：v4 的 `model_override` 透传（入口不知道 pool 语义）、`cli_fallback_model`
   参数（多一个易被误填的配置面）、引擎自动回退（静默换模型，根因不可见）、无参数时用团队模型（三方节点
   的"默认"应是 CLI 自己登录的默认，不是团队的）。现在的行为：pool miss / 协议不兼容 / 目录外名字 /
   `effort` 越界 → **发射前非重试 `BackendError`**，文案给出可兼容的 pool 名单（该 kind 有目录时并给出
   内置名、指路 `builtin_model`，与 `F_113` 的 `_model_name_failure` 三档同族）；认证失败等环境问题 →
   `AGENT_FAILED` 带根因与改法，改脚本后重跑。没有一条路径"悄悄换个模型继续"。
3. **校验错误非重试**：`BackendError.retryable=False` 标记确定性配置错误，`_attempt_calls` 见到它直接
   返回失败结果，不再消耗 `rt.retries` 重试（重复必败）；`AGENT_FAILED` 只发一次，错误文案完整到达 leader。
   存量重试语义不变（默认 `retryable=True`）。
4. **上报模型链**：`AgentResult.model` / `BackendError.model` 携带 CLI 实际报告的模型
   （`ExternalHarnessMemberRuntime.reported_model`，runtime 自行捕获 `session/model_changed` / `system/init` 事件，不依赖可靠性上下文——swarmflow worker 不绑定可靠性上下文也能上报），`AGENT_COMPLETED` /
   `AGENT_FAILED` 事件据此命名真正服务的端点而非原始 option hint；`AGENT_STARTED.model` 仍是 best-known
   ——`describe_model` 用同一份 `plan_external_model` 算校验后的展示值，解析失败回退原始 hint。display-only，
   从不进 journal（不参与缓存签名）。`builtin_model` / `effort` 在**有值时**折入 `call_signature` identity
   （与 `isolation` / `agent_type` 同纪律），无值时字节不变，存量缓存不失效。
5. **`agent_session` 模型首锁**（引擎 `primitives._ensure_open`）：一个 avatar 只跑一个模型，首个 live
   turn 记录该会话的模型身份锁；后续 live turn 的 hint 解析结果与锁不一致 → `EngineError` 指路 `fork()`
   （换模型 = 新会话）。cache-hit 重放从不打开 avatar、从不加锁——resume 中"后面才 live 的 turn"以自己的
   hint 加锁；fork 子会话是全新 `AgentSession`，独立加锁；human 会话不锁（答话的是真人不是模型）；
   一次性 `agent()` 不锁（用完即弃）。
6. **双受众认证文案**：`ExternalMemberTurns` 对 `auth_required` 类失败重写为"`<provider> CLI login failed
   (<detail>) — environment problem, not a script error"——CLI 原始错误文本（status / reason / url）原样留在
   括号内（前端看到真根因），workaround 与可兼容 pool 名单放最后（leader 看到改法）；无兼容 pool 名字时
   退化为"修这台机器的 CLI 登录"。根因在前、改法在后，截断消息也先命中断言。
7. **工具描述与目录注入**：`builtin_model` / `effort` 进 `_ENGINE_OPTIONS` 白名单与 `options` 散文
   （互斥、登录语义、`effort` 伴随关系）；`<builtin_model_catalog>` 仅在任一 kind 声明目录时追加到
   swarmflow 工具描述末尾（`tool_swarmflow` 读 team backend 的 `external_cli_kinds`，镜像 `tool_member`
   的 `_builtin_model_catalog`）——无目录则无注入、散文也不引用，leader 看不到悬空标签。

## 拒绝的方案

- **`model_override` 透传**（v4 行为）：入口把 hint 硬塞进 `ExternalCliModelConfig.model`，不查 pool、不筛
  协议兼容性，落空时静默继承团队模型——根因不可见。
- **`cli_fallback_model` 参数**：为"pool miss 时用什么"再开一个配置面，与 hint 语义重叠、易误填。
- **引擎自动回退**：hint 解析失败时换一个模型继续跑——成功被伪装成原请求的结果，比失败更糟。
- **无参数时用团队模型**：三方节点的默认模型应是 CLI 自身登录的默认；硬绑团队模型让"什么都没写"的脚本
  在订阅登录上跑 pool 凭证，语义错位。

## 验证

- `test_external_model.py`：三参数逐一解析、互斥 / `effort` 伴随 / native 拒绝、pool 命中与 miss（含
  协议不兼容、内置名指路、无目录不引 catalog）、`builtin_model` 目录校验与 effort 越界。
- `test_engine_retryable.py`：`retryable=False` 一次失败即止、`AGENT_FAILED` 只发一次、`_build_opts` 接受
  `builtin_model` / `effort` 两键（未知键仍拒绝）。
- `test_journal.py::test_call_signature_folds_builtin_model_and_effort`：两键折入 `call_signature`（有值才变字节）。
- `test_agent_session_model_lock.py`：首锁后切换 raise 指路 `fork()`（参数化：换模型 / pool 与登录同名不同
  供应）、同 hint 放行、fork 独立锁、cache-hit 不锁、native 锁 hint、无 hint 锁按供应路径指名、失败首轮不消耗锁。
- `test_external_turns.py`：双受众文案（根因在前、pool 名单兜底、无名单退化为修登录）。

## 已知遗留

- **无 hint 锁的命名（已解决）**：锁错误按供应路径指名实际锁定物——native 经 backend 钩子 `describe_base_model()`（`TeamWorkerBackend` 从 worker 基 spec 提取具体模型名；默认钩子返回 `None` 时回退通用短语），三方由引擎按身份记录（`_ModelIdentity`，frozen dataclass；`supply` 字段区分 pool / builtin / both / none 供应路径）诚实指名 `the {cli} CLI's own default model`（CLI 默认模型对引擎不透明，指名其种类而非臆造具体名）。

- **label-less 认证文案**：双受众认证消息不携带节点 label，归位靠事件的 `agent_id`；文案里加节点名
  需要把 member_name 透传进 `_auth_failure_message`，当前不必要（事件已可归位）。
- **首锁身份按裸 hint 比较**：`builtin_model="X"`（吃默认档）与 `builtin_model="X", effort="<default>"`（显式默认档）视为不同身份会拒绝——严格方向（不会假 no-op），重申同一 hint 即可。
- 第三方轮次的 `reported_model` 由 runtime 直接捕获模型事件（swarmflow worker 不绑定可靠性上下文也能上报）；
  `None` 仅当 CLI 全程未报模型，此时不命名端点、退化为 option hint。
