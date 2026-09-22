# F_04 `permission_mode` 全链路生效（`severity_to_decision` 落地）

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature（bugfix，含可见产品行为修复） |
| 日期 | 2026-09-22 |
| 关联 spec | `S_08_security-engine.md`（§A.1 真值表 / 决策轴 / 显式 action 优先） |
| 关联仓库 | `agent-core`（核心修复 + 测试 + spec）、`jiuwenswarm`（compose 层同步修复 + 测试） |

## 背景

`config.yaml` 暴露 `permissions.permission_mode: normal / strict`（docs
`工具权限与安全防护.md` / `config.yaml` 注释）。jiuwenswarm 用户报：

1. **配置项 `permission_mode` 不生效**：`permission_mode: strict` 与 `normal` 行为完全一致，
   实际映射全是 `LOW=allow, MEDIUM=allow, HIGH=ask, CRITICAL=deny`——和文档声明的
   `strict: MEDIUM=ask, CRITICAL=deny` 不符。
2. **`normal` 模式下 MEDIUM 规则仍在弹窗**。

复现（fix 前）：见 `tests/unit_tests/agents/harness/test_permission_compose_p1.py::test_user_severity_only_rule_gets_product_action`
的旧断言；端到端 repro 脚本输出两 mode 完全相同的 action 序列。

根因（两层）：

| 层 | 文件 | 问题 |
|---|---|---|
| agent-core legacy host | `openjiuwen/harness/security/permission_engine/core.py::_fill_legacy_host_rule_actions` | 内嵌硬编码 `_LEGACY_SEVERITY_TO_ACTION` 表，注释里写"像 permission_mode=normal"，**完全没读 mode** |
| jiuwenswarm compose | `jiuwenswarm/agents/harness/common/rails/permissions/permission_compose.py::_apply_product_p1_rule_actions` | 内嵌硬编码 `_P1_SEVERITY_TO_ACTION` 表，同样 mode-blind |

附加：spec `S_08_security-engine.md` 在「关联模块」「不变量 1」「不变量 6」三处点名
`severity_to_decision(severity, permission_mode)`，但 `grep` 全仓找不到任何定义——文档
描述了一个从未实现的契约。

## 决策

1. **实现 `severity_to_decision(severity, permission_mode) -> str | None`** 作为 spec
   §A.1 的契约载体，位置 `permission_engine/toolguard/tool_policy.py`（与 `strictest`
   同一模块）。jiuwenswarm `permission_compose._severity_to_decision` 在可 import 时直接
   转调，缺 openjiuwen 时退化本地表，保证两仓一致。
2. **真值表**（不变契约，写入 spec）：
   - `normal`: LOW/MEDIUM=allow, HIGH/CRITICAL=ask
   - `strict`: LOW=allow, MEDIUM=ask, HIGH=ask, CRITICAL=deny
   - 未知 severity → `ask`（fail-safe）；空 severity → `None`（不动 caller 已有 action）
   - 缺 / 空 / 未知 `permission_mode` → 落 `normal`
3. **`_fill_legacy_host_rule_actions(cfg, permission_mode="normal")` 与
   `_apply_product_p1_rule_actions(rules, permission_mode="normal")`** 接受 mode 形参，
   内部分别通过 `_effective_permission_mode(cfg)`（agent-core）与 `_effective_permission_mode(layer)`
   （jiuwenswarm）归一后传入。两处实现归一函数行为一致。
4. **显式 `action` 永远优先**：规则已写 `action: allow/ask/deny` 时，severity 映射必须
   不覆盖。`_apply_product_p1_rule_actions` / `_fill_legacy_host_rule_actions` 用同一段
   顺序判断（先检查 `isinstance(action, str) and action.strip()` 再做映射）。
5. **compose 输出保留 `permission_mode`**：`compose_host_effective_permissions` 把
   `_effective_permission_mode(g)` 写进 `out["permission_mode"]`，下游消费者能读到归一值。
6. **MEDIUM 在 normal 仍弹窗的根因（bug 2 答复）**：fix 后纯 `severity: MEDIUM` 规则在
   normal 下走 `allow`，不再弹窗。用户仍见弹窗的四种合法路径（由 `permission.check.final`
   日志的 `matched_rule` 字段直接定位）：
   - `rules[<id>]` / `builtin[<id>]` → 规则带显式 `action: ask`
   - `tiered_policy:shell_guard:interpreter_sink` → 命令管道到 bash/python 等（默认开）
   - `tiered_policy:shell_ast:too_complex:*` / `…:parse_unavailable:*` → AST 解析失败（默认开）
   - `tiered_policy:fallback(no_config)` → 规则 pattern 未命中
   全是设计内行为，无引擎侧 bug。

## 拒绝的方案

1. **保留两份硬编码常量，仅修文档**：spec §A.1 真值表要靠单一函数承载才不会被旁路；保留
   副本就是 bug 复发源。
2. **`severity_to_decision` 不接受 `permission_mode` 形参**：等于把"如何归一"分散到所有调用
   点，必然再漏。集中归一 + 显式传参是唯一可推论形态。
3. **删除 `_P1_SEVERITY_TO_ACTION` 后只改 jiuwenswarm、保留 agent-core legacy 表**：
   第三方 host 直接调 `prepare_permissions_for_engine` 仍然 mode-blind，破坏 spec §A.1
   不变量 6/7。两层一起改才是 spec 一致性。
4. **jiuwenswarm 自己实现真值表不调 openjiuwen**：spec §A.1 写"调用点是同一份"，违反这条
   的话下次重构又会丢一致性。允许本地 fallback 仅在缺 openjiuwen 时降级，正常 import 路径
   必须走 `tool_policy.severity_to_decision`。

## 验证基线

- **单元测试**：
  - `tests/unit_tests/harness/security/test_severity_to_decision.py`（**新增**）：22 case
    —— 全真值表（8）+ 大小写 / 空白归一（3）+ 空 severity → None（2）+ 未知 severity
    fail-safe（1）+ 缺 / 异常 mode 落 normal（4）+ `prepare_permissions_for_engine`
    三 mode × action 保留（4）。
  - `tests/unit_tests/agents/harness/test_permission_compose_p1.py`（追加 5 case）：
    - `test_permission_mode_overrides_severity_to_action`：8-case 参数化 mode × severity
      → action；锁定 spec §A.1。
    - `test_permission_mode_normal_is_default_when_omitted`：未配置时落 `normal`，
      MEDIUM=allow。
    - `test_permission_mode_unknown_severity_escalates_to_ask`：typo `"MEDIUMS"` → ASK。
    - `test_permission_mode_does_not_override_explicit_action`：strict mode + LOW + 显式
      `action: ask` 保留 ask。
  - 已存在 case 全过：`test_permission_compose_p1.py` 25/25 通过；agent-core
    `tests/unit_tests/harness/security/` 整体 208/208 通过。
- **端到端 repro**：原 repro 脚本（直接调 `compose_host_effective_permissions` +
  `prepare_permissions_for_engine`）输出两 mode 现已分离并匹配 docs。
- **LSP / 类型**：被改两仓的 LSP 报错与 fix 无关（`permission_compose.py` 周围历史
  `None` 推断问题；`core.py` `_get_reason` `matched_rule: str | None`），未引入新错误。
- **不动手**：未 commit（用户未要求）；提交时按 `harness/docs/AGENTS.md` 须拆三连提交
  `fix(harness): ...` → `test(harness): ...` → `docs(harness): ...`，scope 全部 `harness`。

## 已知遗留 / 跟进

1. **未提交**：本会话只交付代码 + 测试 + spec + 本 feature 文档；commit 由用户验收后发起。
2. **jiuwenswarm pyproject.toml pin `openjiuwen` 到具体 git commit**：fix 后要消费修复，
   需要 bump 该 pin 到包含本次提交的 commit，或把 jiuwenswarm dev 装为 editable install。
   已用 `cp .venv/.../openjiuwen` 验证端到端，生产路径依赖 pin 升级。
3. **jiuwenswarm 前端 `channels/tui/frontend/src/core/commands/builtins/permissions.ts:91-102`
   仍硬编码 normal 模式 severity → action 推断**（注释明示"matches backend
   permission_mode=normal"）。Bug 1 不直接阻塞弹窗（前端只影响 TUI 显示分组），但 spec 一致
   角度看下一轮应该让前端读配置 mode 后再推断。本次未改前端以最小化 diff。
4. **bug 2 的四条"设计内弹窗"路径**已在决策 §6 写明，建议用户先看 `permission.check.final`
   日志的 `matched_rule` 字段，再决定关闭 `shell_guard.{interpreter_sink,unknown_structure}`
   或调整规则 pattern。