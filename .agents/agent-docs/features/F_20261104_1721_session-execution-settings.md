---
id: F_20261104_1721_session-execution-settings
title: 成员执行环境与会话设置持久化
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams]
files:
  - openjiuwen/agent_teams/tools/database/session_settings_dao.py
  - openjiuwen/agent_teams/tools/database/__init__.py
  - openjiuwen/agent_teams/harness/native_harness.py
designs: []
design_note: 无独立 D；在现有 harness 工具调用与 team 会话存储契约上的小范围扩展。
---

# 成员执行环境与会话设置持久化

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-04 |
| 范围 | NativeHarness、TeamDatabase、SessionSettingsDao |
| 测试 | `tests/unit_tests/agent_teams/tools/test_session_settings.py` |

## 背景

team 子 session 共享 session ID，成员身份来自各自 BuildContext。会话策略需要在成员
重建和跨进程执行后继续生效，不能仅保存在 leader 的请求上下文中。

## 数据结构与决策

静态表 `team_session_settings` 以 session_id 为键，保存 JSON settings 与整数 version。
独立于 build_team 创建，允许首轮 leader 工具执行之前写入。initialize 只创建缺失行，
replace 以 expected_version 做 CAS，成功后版本递增；get 读取已提交状态。

NativeHarness 装配将成员 BuildContext 的 resolver 绑定到 AbilityManager。
冷恢复替换 live context 时同步深拷贝当前 seed，确保后续子进程派生使用相同策略；当前 seed 为空时清除旧 seed。
平台 resolver 每次工具执行读取共享数据库，不缓存会话开关，因此远程成员不用依赖广播到达。
跨机器部署必须使用同一团队数据库；数据库不可达时 resolver 失败，不能静默退回旧设置。
Core 不解释 settings JSON 的产品字段，Git 策略由 Swarm 实现。

## 拒绝的方案

- 根据共享 session_id 推断成员名：同一会话有多个执行成员。
- 只推送开关事件：离线、冷恢复或丢事件会形成长期旧值。
- 首次请求反复覆盖设置：恢复和聊天请求可能携带旧 UI 值，initialize 必须幂等。

## 验证

使用两个独立数据库连接验证读取更新、CAS 冲突、跨会话隔离、关闭重开后的恢复。

## 边界

settings 是宿主拥有的策略数据，不作为模型可调用的团队工具暴露。记录保留到数据库清理；
暂停、停止或销毁成员不删除会话设置。环境执行契约见 harness 的 S_05 与 F_20261104_1720_tool-execution-environment。
