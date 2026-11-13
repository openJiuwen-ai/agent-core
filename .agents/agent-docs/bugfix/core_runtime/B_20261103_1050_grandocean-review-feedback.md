---
id: B_20261103_1050_grandocean-review-feedback
title: grandocean 七项检视意见的兼容性修复
type: B
author: alan_cheng
author_email: chengshuo5@huawei.com
modules:
  - openjiuwen/core/session
  - openjiuwen/core/context_engine
  - openjiuwen/core/controller
  - openjiuwen/core/runner/drunner/dmessage_queue
  - openjiuwen/extensions/a2a
files:
  - openjiuwen/core/session/session_controller/scope.py
  - openjiuwen/core/session/session_controller/chain_session.py
  - openjiuwen/core/session/vcs/models.py
  - openjiuwen/core/session/vcs/delta.py
  - openjiuwen/core/session/vcs/adapter.py
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/controller/modules/intent_recognizer.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py
  - openjiuwen/core/runner/runner_config.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py
  - openjiuwen/extensions/message_queue/message_queue_pulsar.py
  - openjiuwen/extensions/a2a/a2a_agent_executor.py
designs: []
design_note: 用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。
---

# grandocean 七项检视意见的兼容性修复

## 现象、根因与决策

基线：`5b19801797b422ca07587ce19c8c1497a4976d16` 加本地工作区差异；分支
`codex/fix-grandocean-review-feedback`。该基线为初次实施阶段；最新交付基线见下方更新记录。
检视意见来自用户 2026-10-22 在 GitCode agent-core 的评论；本次为后续独立修复，不修改历史 PR 的原提交。

| PR | 根因与修复 |
|---|---|
| [#3278](https://gitcode.com/openJiuwen/agent-core/merge_requests/3278) | 对旧 key 无条件 unquote 改变身份。保留旧字面量语义，含冒号 ID 使用独立版本前缀；只限制解码字符仍无法区分旧 `%3A`，未采用。 |
| [#3275](https://gitcode.com/openJiuwen/agent-core/merge_requests/3275) | last_chunk 误绑列表最后一个元素。按 artifact ID 结束，并在纯状态终态、自然耗尽时以 append 空块结束已有产物。 |
| [#3274](https://gitcode.com/openJiuwen/agent-core/merge_requests/3274) | clear 全部 context 后重建丢运行配置和引用。枚举会话 context，仅删快照缺失项，保留已有实例；缺失实例仍依赖引擎默认配置。 |
| [#3269](https://gitcode.com/openJiuwen/agent-core/merge_requests/3269) | 未区分旧 WAL 与新转义路径。新增路径编码版本，旧版本序列化省略字段以保持历史 CRC。采用兼容旧 Pydantic v2 的 model_serializer。 |
| [#3268](https://gitcode.com/openJiuwen/agent-core/merge_requests/3268) | 按任意业务键前缀解码会破坏旧消息，旧 reader 也无法理解新转义。默认保留旧写入，显式 v2 使用 envelope 与 dict 容器；不可表达的旧格式写入明确失败，避免伪造双向兼容。 |
| [#3262](https://gitcode.com/openJiuwen/agent-core/merge_requests/3262) | schema 放宽而 Task 消费链仍要 ID。创建边界生成 ID，统一关联 task/context。 |
| [#3257](https://gitcode.com/openJiuwen/agent-core/merge_requests/3257) | 仅把 ID 写入 payload 不能阻止文件名碰撞。对 JSON 分量数组取稳定摘要作文件名，旧文件继续可读，flush 清理旧 locator。 |

此前未找到拥有这七项修复决策的专属 F/B/S；本记录新增故障证据，当前契约维护于
[运行时兼容规约](../../specs/core_runtime/S_01_runtime-data-compatibility.md)，不改写历史设计。
VCS 原有模块说明同步版本字段与实例保留约束。

## 回归证据

环境：macOS arm64、core 自身 `.venv`、Python 3.11.13，`uv sync --group test --extra all-a2a`。
执行前激活虚拟环境并设置 `PYTHONPATH=.:$PYTHONPATH`；未运行格式化和静态检查。

第一组：`pytest -o addopts='' -q` 加以下路径，**134 passed**：

- `tests/unit_tests/core/session/vcs`
- session_controller 的 `test_scope.py`、`test_group_user_subject_encoding.py`、`test_link_filename_collision.py`
- `tests/unit_tests/core/controller/test_intent_optional_target.py`
- A2A 的 `test_artifact_completion.py`、`test_artifact_last_chunk_terminal.py`
- MQ 的 `test_message_serializer.py`、`test_message_serializer_versions.py`

第二组：同一 pytest 命令加以下路径，**174 passed, 1 skipped**；两组存在 A2A 用例重叠，不相加为独立测试数：

- session_controller 的 `test_chain_session.py`、`test_scope_factory.py`
- `tests/unit_tests/core/controller/test_intent_recognizer.py`
- `tests/unit_tests/core/context_engine/test_context_engine.py`
- `tests/unit_tests/extensions/a2a`

实际证据覆盖：独立构造的旧 WAL/CRC、snapshot+新旧 WAL 恢复、真实 ContextEngine 的 processor/counter/引用保留与跨会话隔离、
真实 TaskManager 创建任务、真实 A2A TaskUpdater/EventQueue 事件、磁盘关系文件数量与重载、冻结的旧 reader 兼容。
初次新增 WAL 恢复测试漏写 HEAD，补全 fixture 后通过；A2A 测试漏 task_done 导致队列关闭等待，补全消费确认后通过。
这些属于测试构造问题，没有计作产品修复证据。

## 遗留与适用范围

初次实施时六个 PR 已合入，#3268 因未解决讨论而阻挡，本地 MQ 修复先独立基于当时 upstream serializer 实施。
随后确认 #3268 已于 2026-11-03 10:43:16 +08:00 合入，七个 PR 均已合入。
本地分支已 rebase 到 `69e0ce1b0`，解决 autostash 恢复时的 serializer 重叠，保留显式版本格式，
移除新合入的无版本前缀转义/反转义；原 PR 的保留键测试显式选择 `wire_version=2`。
本次没有解决他人的远端讨论或绕过合入条件。

更新基线后重跑第一组并增加 `test_message_serializer_reserved_keys.py`：**136 passed**。
其他模块实现未随本次 rebase 改变；第二组既有验证仍适用。

未运行全仓、真实 MQ 跨进程/混合部署或外部 A2A 服务测试；v2 MQ writer 需要调用方确认接收端能力后启用。
原始未转义历史可读；已写入但未标记版本的歧义转义数据需要外部版本依据才能迁移。
用户随后明确授权提交、推送并创建新 PR，无需再次确认。交付按运行时代码与测试、兼容规约与故障记录
两个提交组织，均复用原七个 PR 共同关联的 issue #2113；实际提交与 PR 状态以 Git/GitCode 记录为准。


## 2026-11-03 MQ 检视补充修复

PR !3553 的两条意见均可复现：删除无版本转义读取后，与 upstream `69e0ce1b0` 的收发端混用会改键；
内置 FakeMQ / Pulsar 未透传 `wire_version`，保留键载荷在默认格式拒绝后无法通过生产入口选择 v2。
此前“原始旧 reader 可读”的测试只模拟转义功能合入前的 reader，不能作为转义 reader 的兼容证据。

现通过 `MessageQueueConfig` 的 `wire_version` 和 `legacy_key_encoding` 显式配置读写：
默认 `1/literal` 保留原始历史键；`1/escaped` 双向兼容上游无版本转义格式；v2 信封独立决定读取规则。
配置贯穿工厂、FakeMQ、Pulsar 订阅和生产入口，`start_with_retry` 回退时保留配置。
同时修正工厂中已不存在的 Pulsar 模块导入路径，使真实工厂入口可用。读端单次解析 JSON，避免重复解析。

不恢复无条件反转义：未版本化格式本身存在歧义，不能判断前缀是业务键还是转义标记；
不遇错自动改写 v2：旧接收端无法识别信封。选择格式必须依据部署接收端及队列中历史消息的实际编码。
配置及示例以当前 [兼容规约](../../specs/core_runtime/S_01_runtime-data-compatibility.md) 为准。

新增验证覆盖冻结的旧转义字节、独立模拟的转义 reader、嵌套保留键 / datetime、v2 的前缀键保持、
FakeMQ 真实订阅交付、工厂构造 Pulsar 后捕获 SDK 发送字节、Pulsar 接收旧转义消息及确认、
重试回退后实际交付，以及非法配置拒绝。首次 Pulsar 接收测试使用了没有 `type` 的基础 QueueMessage fixture，
改为生产链路使用的 DmqResponseMessage 后通过；没有改变产品日志或收发语义来迁就 fixture。

本轮在 core `.venv` 中补齐 `pulsar-client` 可选依赖；未修改依赖清单或锁文件。
序列化与扩展 MQ 定向测试 **47 passed**；连同原 PR 的 VCS、scope、A2A 等回归、队列与配置测试及文档同步测试
合并执行共 **197 passed**（含前述 47 项，不相加）。未连接真实 Pulsar broker，未运行格式化或静态检查。
