---
id: F_20261124_1130_prompt-source-consolidation
title: 共享英文提示词与工具元数据原地精简
type: F
author: MengHan18
author_email: null
author_note: 按用户要求省略文档中的个人邮箱；保留公开 Git 署名。
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

## 2026-11-24 检视修正：共享工具说明

基于本特性提交后的两项检视意见，仅修正 tools 模块的英文说明，不修改 system sections、user/运行时附件、执行实现或参数 schema：

- `edit_file`：空 `old_string` 可以创建不存在的文件，或向已有空文件写入；不能清空已有非空文件。此前的 “creates or empties a file” 会误导模型，与执行实现不符，现明确该限制。
- `bash`：在共享工具说明中补回优先新提交、优先安全替代方案，以及未经用户明确授权不得绕过 hooks/签名的简短约束。不能只引用 Swarm Code 的 Git Safety Protocol，因为直接使用 core 或其他共享消费者不一定注入该 Code 专有段落。

来源复核：原始基线的 `edit_file` 说明正确；“creates or empties a file” 已出现在此前精简版本的真实 tools 中，由短说明改写引入，当前提交候选只是沿用。此前已测试的 Code 请求虽然使用精简后的 bash 说明，但 system 保留 Git Safety Protocol，因此不能认定其完整请求漏掉了 hooks/签名约束。本次将简写原地应用于共享英文元数据后，才需要补齐没有 Code 专有 system 的其他消费者。前一项是既有短文案语义纠错，后一项是共享使用范围的完整性修正；两者都不是工具执行实现错误，也不能据此推断此前 benchmark 得分受到影响。

在 Python 3.11.15、当前 core 源码下运行工具元数据、参数、描述、提示词契约，以及文件工具中 edit/empty/create 相关既有测试：63 项通过，68 项不相关测试未选中。另经 `build_tool_card(..., language="en")` 的共享注册入口读取两张工具卡，确认修正文案已进入模型可见元数据且不依赖 Code system prompt。

未新增重复文案快照测试；未重跑云端请求或 benchmark。上文抓包属于修正前候选，不能将其工具逐字一致性或已记录的输入用量直接视为这两处说明修正后的新实测值。两个英文 description 变长也可能改变对应的 BM25 检索文本，其他入口仍需按实际使用范围评估。
