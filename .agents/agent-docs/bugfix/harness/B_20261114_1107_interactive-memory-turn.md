---
id: B_20261114_1107_interactive-memory-turn
title: 交互式中断恢复后记忆丢失修复
type: B
author: robin1217
author_email: chenruikai@huawei.com
modules:
- openjiuwen/harness
files:
- openjiuwen/harness/rails/memory/jiuwen_memory_rail.py
- tests/unit_tests/harness/rails/test_jiuwen_memory_rail.py
- tests/unit_tests/harness/rails/test_jiuwen_memory_interrupts.py
designs: []
design_note: 本次按对话中确认的 D3 收敛方案修复，未建立独立 D 文档；外部 F08 的 D3 是决策编号，不是设计资产标识。
---

# 交互式中断恢复后记忆丢失修复

## 现象与影响

用户在原始请求中提供邮箱，agent 经 ask_user 索取补充信息后恢复并回复，但记忆轨道没有
把完整交互提交给 provider。非流式和流式入口均受影响。来源为 agent-memory 仓库
`docs/features/F08-agent-prefetch-recall-reusable-facts.md` 的 D3。

## 关联设计与当前契约

本次在用户确认的会话级内存聚合方案内实施，当前契约见
`specs/harness/S_04_rails-contract.md` 不变量 15。外部 F08 的 D2、D4、D6、D7 保持各自范围，
本记录仅承接 agent-core 的 D3，不改对端文件。

## 根因与修复决策

旧 after_invoke 同时要求用户字符串与助手输出非空。首次中断没有最终回复，恢复调用的
query 则是 InteractiveInput，因此两个调用都被跳过。流式中断的外层 result 为空。

JiuwenMemoryRail 在 before_invoke 保存用户请求，按真实会话标识维护一轮材料；
before_invoke 遇到实际待处理中断时追加普通文本，避免再次中断覆盖原请求。
after_tool_call 根据现有回答归属收集已完成的 ask_user 返回文本，不解析其排版；
因此 AskUserRail 接受的四种值形态均可保留，权限批准和过期答复不混入。
宿主将“取消问答/跳过”编码为普通答案文本时，本轨道保留该问答返回作为抽取上下文；
这与真正取消执行 task 不同，不在本修复中改变宿主的控制语义。
after_invoke 区分暂停、完成和失败，完成后先取出材料再提交一次 sync_turn。
project 在等待旧沉淀任务之前取值，避免异步阶段读取下一轮的宿主绑定。

后续 CodeCheck PR 3755 的 G.CTL.03 检查指出两个 after_invoke 条件包含过多布尔表达式。
将待处理中断、调用异常和错误结果分别提取为具名布尔值，控制流条件各自不超过三项；
该整理不改变原有判断语义与执行顺序。

聚合上限 16,000 字符，原请求超长保留首尾，补充按最近完整条目保留并标记截断。
缓存最多 128 个会话，30 分钟未活动在后续前台调用中淘汰；回答标识最多保留最近 128 个。
后台任务不碰缓存；熔断只阻止提交，轮次状态仍维护；结束会话或卸载清空 rail 的缓存。

未采用：宿主回填需要所有宿主配合；修改 DeepAgent 主流程扩大执行框架影响；扫描恢复对象
所有 answers 值可能引入过期或未接受的回答；持久化暂存和失败重试会增加独立的恢复与投递契约。

## 回归证据与适用范围

基线为本任务开始时 develop 的工作区（279086272）；证据对应本记录所列文件的未提交修改。
环境为 `uv sync --group dev --extra observability` 初始化的项目 `.venv`，Python 3.11.15。
验证使用公开工厂组装的 DeepAgent、真实 AskUserRail 和中断处理器、脚本模型与记录型
MemoryProvider，断言 sync_turn 的实际参数及提交次数；不以模型最终回复作为记忆写入证据。

覆盖范围：invoke/stream、两种回答寻址、四种合法回答形态、连续中断、纯文本续接、权限与
过期回答、后续新任务、跨会话隔离、心跳/cron、熔断、空结果/异常、容量/长度/过期、无暂存恢复。
另验证真实 task 取消后的清理、carried/重复回答、卸载清理，以及宿主以普通文本表达的
取消问答/跳过在恢复后仍保留原请求、轮次结束提交一次、之后新输入另起一轮。

定向回归命令（执行前激活 `.venv` 并设置 `PYTHONPATH=.:$PYTHONPATH`）：

```bash
python -m pytest tests/unit_tests/harness/rails/test_jiuwen_memory_rail.py tests/unit_tests/harness/rails/test_jiuwen_memory_interrupts.py tests/unit_tests/harness/rails/test_external_memory_rail.py tests/unit_tests/harness/rails/test_typed_interrupts.py tests/unit_tests/harness/test_deep_agent_rail_event_routing.py tests/unit_tests/harness/test_cli_jiuwen_memory_rail.py tests/unit_tests/harness/test_deep_agent_terminal_stream.py -q -o addopts='' --disable-warnings --show-capture=no --maxfail=3
```

最终回归（含取消问答/跳过的 invoke 和 stream 四个场景）为 125 passed、7 skipped，耗时 20.55 秒。
跳过来自既有用例，不表示真实服务验证通过。未运行格式化或静态检查。

负向对照在独立 Python 进程内加载 HEAD 的原始 JiuwenMemoryRail 后，运行新增的
`test_real_ask_user_turn_is_synced_once[answers-call-False]`。它在最终回复后因
`rail._sync_task is None` 失败，证明原实现没有提交这一轮；未修改仓库文件。

CodeCheck 后续检查在 `after_invoke` 两处报告 G.CTL.03（条件布尔表达式数量分别为 5/3、4/3）。
已将相关子条件命名为 `has_pending_interrupt`、`has_invocation_error` 与 `has_error_result`，
保留短路条件和原执行顺序。此次只针对规则可见性做局部重构，未重新运行行为测试或 CodeCheck。

### WorkSwarm 实测复核（2026-11-14）

复核运行环境的会话 history.jsonl、agent_server.log、USER.md、shadow.db 中的画像条目，
以及新会话 context_snapshots 中的 `<memory-context>`。使用只读访问，未发送新消息或重复发信。
以下证据来自本次修改后的本地部署；凭据与邮箱值不录入本文。

| 会话 / 时点 | 真实入口和观察结果 |
|---|---|
| `web_1a1d913911e_625af31c4b33`，16:03:14 | ask_user 的三个问题收到取消问答文本，通过 InteractiveInput 按 interrupt_id 恢复。该操作取消问答，原任务仍继续。 |
| 同会话，16:03:49 → 16:04:00 | 自动提交一次 sync_turn，原请求、取消问答返回及最终回复重建后的 1,230 字符与日志一致；写入完成，两个邮箱成为 USER.md 的 semantic 用户画像，未把取消标记写成画像。 |
| 同会话，16:04:14 → 16:06:48 | 用户另发普通消息提供授权码；该轮单独沉淀并写入 USER.md，与前一轮分离。 |
| `web_1a1d9189a4a_27427d847e79`，16:07:10 起 | 新会话仅提出发送文档，没有再次提供邮箱或授权码，也没有显式调用记忆工具；模型上下文的 memory-context 同时含原发件邮箱、收件邮箱和授权码，逐值比对一致。 |
| 新会话，16:11:20 → 16:11:36 | 自动沉淀一次并完成，模型使用已有信息尝试发信。SMTP 返回 535 登录失败，认证问题与记忆聚合/召回分开判定。 |

当前可复验范围内，已观察到真实 ask_user 恢复后的原请求沉淀、普通后续轮次独立沉淀和新会话
画像注入。取消文本未成为画像是这次模型抽取的实际结果，不代表轨道确定性过滤了取消/跳过标记。
实测仍需在部署代码、provider、模型或宿主恢复协议改变时重验。

## 防错模式与资料同步

已把逻辑轮次、暂停判据、隔离键、容量和生命周期边界同步到 S_04 不变量 15。
中英文 Rails API 文档补充交互式外部记忆的使用方式与暂存限制。
本地差异复查核对了实际回调路由、会话状态消费、后台与取消清理、缓存上限和异步提交边界；
上述范围内未发现阻塞本次 D3 修复提交的可确认问题。已完成上述本地 WorkSwarm 实测复核，
未开展独立评审或全渠道部署验收。
原始请求和交互回答属于同一个待沉淀轮次，不可用单次调用的 query/output 配对替代。
背景资料 F_22 仍只解释 CLI 装配，不用其历史真实服务测试证明本次恢复链路。

## 遗留与后续

- 内存暂存在 rail 重建或进程重启后丢失；TTL/容量淘汰及超限截断有明确的信息损失边界。
- sync_turn 的超时、熔断和外部失败不重试，本修复保证材料聚合和单次提交，不保证外部存储成功。
- 不收集中间工具轨迹，也不对其他工具的中断答复做用户画像材料展开。
- 同一会话依赖宿主串行驱动；本次不扩展 DeepAgent 的同会话并发执行契约。
- 宿主的取消问答/跳过仍是答案文本，是否排除其画像化由抽取器判断；结构化控制事件及
  “取消问答”和“停止任务”的产品语义属于后续跨仓调整，不因本次聚合修复改变。
- 助手承诺不保存授权码、而 agent-memory 现有策略仍保存的矛盾未由 D3 引入，需单独统一策略与文案。
- 本次本地渠道验证不代表其他部署、进程重启恢复或其他 provider 已通过。
