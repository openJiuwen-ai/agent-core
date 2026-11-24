---
id: F_20261124_1130_prompt-source-consolidation
title: 共享英文提示词与工具元数据原地精简
type: F
author: MengHan18
author_email: hanmeng22@huawei.com
modules:
- openjiuwen/harness
files:
- openjiuwen/harness/prompts
- openjiuwen/harness/rails/subagent/subagent_rail.py
designs: []
design_note: 依据用户明确授权的提交版整理任务及已确认的共享英文原地精简范围，无独立仓内设计编号。
---

# 共享英文提示词与工具元数据原地精简

日期：2026-11-24。范围为已有英文提示词的维护方式收敛，保留工具集合、参数结构、Skills、权限及按需派生能力。

## 实施与取舍

直接在原 section、metadata provider 和宿主生成入口维护简明英文，不再复制完整英文再做条件替换。Code 专有任务规则保留在 Code builder；共享规则会同步影响 Work 等消费者的英文提示词。中文说明保留既有语言路径。

动态目录从实际配置构造，保留类型、用途、自定义说明与浏览器能力限制，不解析先前生成的长文本。UI 参数仅在既有模型视图注入点生成，不回写执行 schema。

拒绝继续维护原文指纹和第二套 brief 文案：这种方案会增加维护成本，并在原文更新后静默回退。也不全局按工具名改写第三方说明，不把冻结抓包名单当作当前注册表。

## 验证与交接

使用 Python 3.11 定向验证元数据、动态目录、恢复、授权、Code 装配及共享宿主消费路径；最终请求和候选差异另行记录。已完成最终候选的完整 AgentServer Code/TUI 真实云端请求：HTTP 200 / PONG，Skills 开启，22 个工具声明与已审视样本一致，17 个系统块保留。Code 探索建议迁移到 Code 自身工具规则；隔离路径变化单列，不修改其他规则来追求 token 数。

本地定向测试不替代 SWE-bench、Linux 平台或模型质量验证。正式集成应先合入 core，再更新宿主的官方依赖；不提交个人私仓依赖和测试构建版本。

本轮提示词与子 Agent 回归 117 项、工具检索和目录生命周期回归 29 项通过。65 个 provider 的可执行 schema 与中文 description 文本保持；共享英文元数据更新会影响使用它的检索索引文本，检索算法和权限机制没有改变，语义召回仍需真实任务评估。
