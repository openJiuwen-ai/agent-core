---
author: Wang Tao
author_email: zzyyww0808@gmail.com
id: F-20261118-1150-team-document-memory
title: Team 接入 Jiuwen 文档记忆
type: F
modules: [openjiuwen/harness/rails, openjiuwen/agent_teams/memory, openjiuwen/agent_teams/harness]
files: []
designs: []
design_note: 依据 D-20261113-2017-jiuwen-memory-team-upgrade 及已确认的跨仓 Team 方案。
---

# Team 接入 Jiuwen 文档记忆

日期：2026-11-18。

## 当前群体记忆接线

`JiuwenTeamMemoryRail` 复用 Coding Rail 的工具、缓冲和写入排空。真人来源来自宿主可信上下文和已验证的 ask_user 答复。仅在含真人来源且成功完成的回合提交带角色的来源片段。内部派单通过子任务验收回写，交付和验收版本均取提交事务确认的任务修订号与评审轮次，Rail 按任务 ID 和验收版本消除本进程内重复通知。Core 不调用另一轮提取模型。

Team 和 Coding 的回调共用所有者查询及初始化检查，并使用同一写队列。子任务验收可合并同一任务、修订与评审轮次对应的成功执行回复。验收先到时等该执行回合结束后组合材料。收尾提前返回或异常时也清理交付登记。仅有文件而无实质交付文本时跳过抽取。已验证的问答卡片保留原始短答和匹配选项的上下文，问题归 Agent 来源。

团队暂停和结束只排空已接受的写入。专家模板引用通过成员装配上下文传给宿主，宿主可用稳定模板身份分配记忆归属。

显式专家入队和成员移出由 Team 工具完成后通知宿主。已经休眠或进入错误状态的成员被显式永久关闭时也通知宿主。自动休眠和会话结束保留持久成员权；同一模板仍有活动实例时，移出其中一个实例不撤掉该模板的团队成员权。

## 上游基线历史

新增 JiuwenTeamMemoryRail，复用 Coding 文档 Rail 的工具归属、轮次缓冲、上下文注入及排空机制；每个团队回答者由宿主创建独立实例。Core 不依赖 agent-memory 包，不解析项目键、团队键和用户配置。

成员正常非空回合结束后，普通 sync_turn 与团队专用抽取独立执行，任一失败不阻断另一条。团队抽取使用该回答者模型，只有条目收集工具，无文件工具；校验 confirmed=true 及四种标签，再调用宿主 add_team_memories。没有模型或无合格条目时不新增结论。宿主 content_allowed 同时约束自动写入材料及抽取结果。

Leader 的协调器在 pause 或从 running 状态 stop 时、释放会话和数据库前，读取任务及消息并调用 conclude_round。pause 后再 stop 不重复收口。原生 TeamMemoryManager 及原提示词不改，宿主选择 Jiuwen 时不装配旧 manager。

## 生命周期

独立 Rail 保留自己的缓存及写入队列。成员回合沿用现有超时；conclude_round 的已开始提取在调用方取消后仍纳入 drain。最后 owner 退出时先排空，再关闭 Provider；SDK Runtime 的引用计数由宿主持有者负责。共用 Runtime 不表示共享 Rail 或模型上下文。

## 拒绝的方案

不复用旧 TEAM_MEMORY.md 文件写入工具，不复制一套轮次聚合算法，不让 Core 持有 Swarm 配置或另建 Runtime 池。团队结论不走普通三类分类器；允许重复结论，不在本次增加语义治理。AutoMemory、Dreaming 和外部 CLI 能力不改造。

## 验证与边界

test_jiuwen_team_memory_rail.py 验证成员触发、模型缺失、失败独立、内容限制、取消后排空，以及专用抽取 Agent 经 Runner 的真实模型工具调用；使用模拟 LLM。已有 Coding Rail 测试验证原行为。未将模型质量和所有终端页面声明为已验收。

验证命令：`pytest -q -p no:cacheprovider -o addopts= tests/unit_tests/agent_teams/test_coordination_lifecycle.py tests/unit_tests/agent_teams/test_team_agent_coordination.py tests/unit_tests/harness/rails/test_jiuwen_memory_coding_rail.py tests/unit_tests/harness/rails/test_jiuwen_team_memory_rail.py`。149 项通过，19 项按既有条件跳过；收口测试验证 pause 后 stop 不重复且抽取早于会话释放。

## 生命周期检视修复

补齐 NativeHarness.dispose 及 TeamHarness.start/run_once 替换旧实例的记忆清理。复用 Rail.aclose 和 Provider 的租约归还；不在普通 stop/pause 时关闭，不用 dispose 整体销毁跨周期 sys_operation。原问题是 Rail 已有清理能力，但宿主退出路径未调用。

回归覆盖真实 NativeHarness 退出等待在途写入、重复退出幂等、其他成员继续召回，以及 start/run_once 连续重建不遗留旧 owner。模型使用已有替身，不要求在线服务。

四项检视修复后的本仓回归：199 项通过，覆盖 NativeHarness 交互测试目录、Coding/Team Rail、协调生命周期及后台控制器。测试使用离线模型及本地三仓源码，未重跑真实 LLM 或页面验收。


## PR 检视修复与边界澄清

2026-11-21：团队抽取前检查 Provider 的 add_team_memories 能力；不支持时不调用 LLM，每个 Rail 仅告警一次，普通同步继续。正式 Swarm Provider 已在 agent-memory 实现此方法，这项防护针对不支持的 Provider 被误装配；不向通用 MemoryProvider 添加空实现，以免掩盖未写入。

收口桥接改用 TeamRole.LEADER，并读取团队 memory.timezone_offset_hours。旧 Manager 被关闭时配置仍生效；缺省配置沿用 UTC+8，不改变原始消息时间戳。

保留成员回合和团队收口两个抽取点。正式 Swarm 装配关闭旧 Manager，避免两套整轮提取；当前可访问团队记录仍按原方式读取，增量读取和重复结论治理暂不扩展。普通同步与团队抽取并发，共用 130 秒外层上限，已完成写入不撤销；超时仍会取消未完成任务。

回归复用 Team Rail 与协调生命周期测试：覆盖缺少或不可调用写入方法、普通同步继续、LLM 不被调用、重复触发只告警一次，以及无配置、默认配置、UTC、负时区的实际材料渲染。新增用例在修复前复现 4 项失败，默认时区的 2 项对照通过。

修复后定向回归 544 项通过、19 项按既有条件跳过，覆盖回调模块、Work/Coding/Team Rail、协调生命周期和团队协调用例；使用本地 Python 3.12 与模拟模型。云端 CI 需提交后重跑，本次不扩大为真实 LLM 验收。
