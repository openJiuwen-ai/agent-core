---
id: B_20261102_1120_avatar-child-session-trace-root
title: Swarmflow avatar child session 找不到 Team 轨迹 root
type: B
author: wangcan103
author_email: wangcan103@huawei.com
modules:
- openjiuwen/agent_teams/workflow
- openjiuwen/extensions/observability
files:
- openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py
- openjiuwen/extensions/observability/span_context.py
- tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py
- tests/unit_tests/extensions/observability/test_span_context.py
designs: []
design_note: 普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计
---

# Swarmflow avatar child session 找不到 Team 轨迹 root 修复记录

## 现象与影响

Swarmflow `agent_session` / `human_session` 的 harness 使用 avatar child session id，Team root span 则按外层 Team session id 登记。Agent observability rail 按 callback session id 查询 root 时无法命中，avatar agent/model/tool spans 因而不会挂到 Team trace。

## 关联设计与当前契约

`S_18` 规定 avatar 使用稳定 child session id 保存和恢复自身状态；Team trace root 仍属于外层 Team session。两种 ID 各自承担不同职责，不能通过更换 avatar 的持久化 session id 来解决轨迹关联。本修复保持 callback 的精确 session 查询规则，不让 rail 猜测 root。

## 根因与修复决策

`AvatarSessionManager` 在 `harness.start()` 周围绑定了成员 `ExecutionSubject`，但没有把 Team root span 与 callback 使用的 child session id 关联。child id 形如 `{team}/{workflow}/{member}`，不含外层 session id；将其加入全局 root registry 会让并发的同 team/workflow/member 运行互相覆盖。

新增 `bind_root_span_context()`，只在当前上下文临时绑定 root span 与 child session id，不写全局 registry。avatar 启动 supervisor 前按外层 Team session 精确取得 root，再将临时绑定和现有成员 subject 一起放在 `harness.start()` 外层；supervisor 创建时复制上下文，后续 callback 因而能按 child id 找到正确 root。缺少外层 session 或 recording root 时不做绑定，不回退到其他运行的 root。

拒绝在通用 rail 增加 subject-based fallback：那会扩大行为影响范围，并可能把继承了 Team subject 的独立 callback 并入 Team trace。也拒绝全局 child-id 别名：该 ID 不是跨 Team session 唯一键。

## 回归证据与适用范围

- 候选：当前 `develop` 工作区实现；Python 3.11.7，仓库 `.venv`，Windows。
- 命令：`.venv/Scripts/pytest.exe -q tests/unit_tests/extensions/observability/test_span_context.py tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py`
- 结果：49 passed，2 个既有 Pydantic deprecated 配置警告。
- 测试验证 context 绑定会被 supervisor task 继承、两个并发 child session 各自解析到自己的 root、退出上下文后 child ID 未进入全局 registry；avatar manager 测试验证启动时 child session 能解析到 Team root，同时保留外层成员 subject。
- 回归命令：`.venv/Scripts/pytest.exe -q tests/unit_tests/agent_teams/observability/test_team_root_session_registry.py tests/unit_tests/harness/observability/test_rail.py`
- 回归结果：45 passed，2 个相同的 Pydantic deprecated 配置警告；Team root registry 与通用 harness rail 行为未回归。
- 使用系统全局 pytest 时，pytest 在收集阶段因其加载 Pydantic 1（缺少 `field_validator`）失败；改用仓库 `.venv` 后测试通过。

## 防错模式与资料同步

本修复没有修改通用 callback lookup。`S_18` 已补充 avatar 的 root 归属和 context-local child session 关联约束；共享 observability helper 的局部绑定不改变全局 session registry 契约。

## 遗留与后续

验证覆盖当前进程内 NativeHarness supervisor 的 ContextVar 继承路径；未运行真实模型或外部 harness 集成测试。若 supervisor 将来改为不继承创建时上下文的执行机制，需重新验证并显式传递此关联。
