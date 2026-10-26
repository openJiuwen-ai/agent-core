---
title: F_01 任务级 Web 检索配置
author: l_x_d
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: F_01_task-scoped-web-research
type: F
modules:
- openjiuwen/harness
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: linxiangda@huawei.com
---

# F_01 任务级 Web 检索配置

日期：2026-09-08

论文优化任务需要独立代理与来源策略，避免一次任务修改环境变量影响其他任务。
Web 工具通过构造参数传入代理、允许来源和搜索引擎，复用 HTTP transport 的
代理认证、超时和现有环境配置回退。工具工厂默认行为保持兼容。

国内学术策略选择百度学术、百度网页、知网和万方入口；搜索结果按域名过滤，
Fetch 的受限模式关闭 jina reader 回退。调用方的论文下载工具共享这些设置。

没有采用进程环境切换来配置搜索开关，因为多个任务会共享同一进程。
没有把来源过滤描述为防火墙：自动重定向仍可访问域外目标，过滤仅约束采用的结果。

验证：增加 test_web_tools_v2.py 的任务级代理、来源过滤和搜索后端回归覆盖。
本次用户选择跳过 UT；用例未运行。真实搜索服务可用性与代理联网未验证。
