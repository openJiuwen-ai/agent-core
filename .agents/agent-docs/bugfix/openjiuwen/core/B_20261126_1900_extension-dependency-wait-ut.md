---
id: B_20261126_1900_extension-dependency-wait-ut
title: 跨域依赖等待成功场景的 UT 时序容差
type: B
author: "MaxMadMax"
author_email: "chengkunck@gmail.com"
modules: [core.extension]
files: [tests/unit_tests/core/extension/test_install_report.py]
designs: []
design_note: 仅修正现有成功场景测试的等待预算和失败诊断，生产契约不变，无独立设计。
---

# 跨域依赖等待成功场景的 UT 时序容差

## 现象与归属

PR !3893 门禁评论 204222668（2026-11-26 18:28 +08）的完整 UT 报告记录 GIT_COMMIT=58e432f71b5dec974cd7e22af5f1813530f01926；29,621 passed、493 skipped、2 xfailed、1 failed。唯一失败为 test_cross_domain_wait_and_reverse_dependency_protection：依赖 b 加载成功后，a 返回 FAILED。报告未保留该返回值的错误码，不能据此确认 CI 内部的具体失败原因。测试及 Extension 生产实现在本地 develop 46014823e 与候选中一致，属于基线成功场景的时序风险；本次仅在用户要求修复最新 UT 的范围内做测试调整，不扩展 Team 生命周期实现。

## 最小修复与依据

此测试原先只给 a 的依赖等待配置 1 秒，预算包含等待观察及 b 的文件操作、加载和报告提交。对 b 启动注入 1.2 秒延迟后，原测试稳定返回 EXTENSION_DEPENDENCY_TIMEOUT 并失败，支持 CI 调度或 I/O 延迟触发超时的解释，但不能替代 CI 原始错误码证据。将这一成功场景的 a 配置为 timeout=5，与相邻成功等待测试一致；失败断言附带 error，便于后续门禁区分超时和其他原因。保留所有加载、反向依赖保护及卸载断言。专门的 timeout=0.05 超时测试、生产默认 300 秒及 S_02_registry-and-bus 契约均不变。

## 验证与限制

候选为 58e432f71 上的未提交测试修复。Python 3.11、本地真实文件报告与 Extension Registry：修复前无注入的 test_install_report.py 全部 34 项通过；同一 1.2 秒延迟注入下原测试 1 项失败（EXTENSION_DEPENDENCY_TIMEOUT），候选 1 项通过；修复后 test_install_report.py 全部 34 项通过，包含显式等待超时、依赖环和多进程用例。主代理完成最小差异复查，无生产逻辑或接口变化。未运行本地静态工具或完整 CI；5 秒仍是有界预算，新一轮远端结果需推送后确认。PR 已被机器人按 72 小时门禁策略关闭，本次未重开、提交或推送。
