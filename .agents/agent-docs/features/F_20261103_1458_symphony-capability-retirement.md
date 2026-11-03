---
id: F_20261103_1458_symphony-capability-retirement
title: Symphony 旧检索与独立经验库退役实施
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules: [symphony]
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/agent/retrieval_toolkit.py, openjiuwen/symphony/retrieval, openjiuwen/symphony/experience, tests/unit_tests/symphony, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/README.md]
designs: [D-20261103-1438-symphony-capability-retirement]
---

# Symphony 旧检索与独立经验库退役实施

## 依据、候选与决策关系

2026-11-03 实施 [D-20261103-1438-symphony-capability-retirement](../designs/active/D-20261103-1438-symphony-capability-retirement/design.md)，当前契约见 [S_01_configuration-contract](../specs/symphony/S_01_configuration-contract.md)。Core 候选是 99fa1600d 之上的本次工作区，Swarm 基线为 01c69908f3156efb57648df9b78cc79f92dec4c8；均未提交、推送。

局部替代 F_20261031_1614_symphony-config-contract 中保留旧模型检索、分支浏览和独立经验库的决定。旧记录的模型数量与验证结果保留为历史事实；嵌套 Pydantic、中文元数据、保留默认值及目标配置尚未接入 Runtime 的决定继续有效。

## 实际实施与保留边界

删除 Retriever/ProgressiveRetriever/FlatRetriever、检索服务和结果合并、紧凑编码与 logit/trie 选择、检索专用 OpenAI/vLLM/Transformers 后端、分支探索与预览、独立 experience 包及相关测试和提示资源，不留兼容别名。

配置模型由 63 降至 36（公共 48 → 33，内部 15 → 3）。内部只保留 FingerprintProtocolConfig、OneShotTreeBuildConfig、RetrievalModelConfig；根入口只保留 capability、storage、retrieval、orchestration、flow，retrieval 只保留 enabled/build/discovery。

删除 SkillIndexRuntimeConfig、SearchStep、MultiLevelSearchResult、ScoreTruncationConfig 及实现。构建去除 BuildMethod/method、IndexBuildRuntimeConfig、runtime_config 入参别名，统一使用 config；can_build_tree_with_llm 中同名单个实际参数继续保留。

AgenticSkillRetrievalToolkit 保留构建、更新、异步状态、取消、树读取及发布恢复，删除浏览状态和仅供浏览使用的 close。保留名称避免无必要的下游迁移。Discovery 以及增量分类路径仍需要 search/artifacts/loading.py；Discovery 还需要 search/runtime/lexical.py 和发布恢复函数，均保留原路径。

指纹、Fast/Beam、dynamic_graph_enabled、observation 和 Flow 保留。Flow 的 symphony.experience_recipe.v1、Swarm runtime/symphony/experience.py 均不同于已退役的独立经验库。旧会话的工具结果渲染和压缩继续保留。

Swarm 配套修改限于部署模板、配置合并测试及中英文指南：旧 retrieve 节点替换为 Discovery 默认项，指南使用现有 skill_index 的 list/search/read。实际业务调用已是保留接口，因此不需要改业务；Core pin 与 uv.lock 不变。

## 行为证据与环境

改动前保存配置 dump 和全部 Schema；改动后仅从基线删除 experience、retrieval.search、retrieval.toolkit 三个节点，其余 dump 完全相等。36 个保留模型的字段默认值、约束和引用逐项一致，职责说明按新边界更新。

固定 SkillRecord(name='alpha', description='Alpha') 与默认 SkillIndexBuildConfig 的构建指纹前后均为 `66efb832b43a198bdbe7c66828b6158b26cee233c2e47c48f095337078c43d11`。新配置没有接入运行签名或改变工件格式。

新增 test_discovery_index_lifecycle.py，从公开构建入口调用真实一次性构树、发布与索引读取，模型使用确定性 chat.completions 替身；独立读取临时工件，验证可见范围、分页、词法搜索、快照往返、发布中断恢复、失败保留旧索引及无索引平铺回退。既有递归、增量和编排测试继续保留。

| 验证 | 实际结果 | 适用范围 |
|---|---|---|
| Symphony 全部 + Harness snapshot/evolution | 1704 passed，2 个既有警告 | Core Python 3.11.15，候选源码 |
| 配置/导出最终回归 | 229 passed，2 个既有警告 | 说明同步后运行，与上行重叠 |
| 公共 API 退役约束补充 | 8 passed，2 个既有警告 | 其中 6 项为综合回归后新增，拒绝旧关键字与浏览入口 |
| Swarm 消费回归 | 769 passed | Python 3.12.13，实际导入候选 Core |
| 基线对比与配置导出 | 通过 | 默认值/约束/指纹不变，全量配置标题保留 |

Core 命令（主仓已有虚拟环境；PYTHONPATH 指向候选 Core 与 /tmp/symphony-retirement-deps）：

```bash
python -m pytest tests/unit_tests/symphony tests/unit_tests/harness/rails/test_symphony_snapshot.py tests/unit_tests/harness/rails/evolution -o addopts='' -q --disable-warnings
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -o addopts='' -q --disable-warnings
python -m pytest tests/unit_tests/symphony/test_public_api.py -o addopts='' -q --disable-warnings
python scripts/export_symphony_config.py
```

Swarm 在独立工作树使用 /tmp/symphony-retirement-swarm-venv，JIUWENSWARM_DATA_DIR=/tmp/symphony-retirement-swarm-data：

```bash
python -m pytest tests/unit_tests/symphony tests/unit_tests/test_config.py tests/unit_tests/agentserver/test_code_adapter_symphony.py tests/unit_tests/agentserver/test_stream_event_rail_symphony.py tests/unit_tests/agentserver/test_symphony_orchestration_needs_input.py tests/unit_tests/agentserver/test_symphony_retrieval_context_processor.py tests/unit_tests/agentserver/test_symphony_timeout_lifecycle.py tests/unit_tests/agentserver/test_symphony_web_panel.py tests/unit_tests/agentserver/test_system_prompt_restructure.py tests/unit_tests/agentserver/permissions/test_session_observation_fast_path.py tests/unit_tests/channel/test_web_channel_symphony_status.py -o addopts='' -p no:cacheprovider -q --disable-warnings
```

现有环境缺 OpenTelemetry SDK/导出器、协议包和 A4P，补充到 /tmp，不改共享环境和依赖文件。OpenTelemetry 使用 Core 锁文件的 1.39.1/0.60b1，协议为 9ec03708，A4P 为 1699ea35；其余包复用既有环境，不声称完整锁文件安装。临时解释器以 .pth 固定候选 Core、Swarm 及临时依赖路径，独立子进程也核对来源。日志分别为 /tmp/symphony-core-tests.log、/tmp/symphony-swarm-tests.log。

首轮测试发现并修正了误删的增量分类路径加载器导入、空测试块和展示大小写断言。Swarm 子进程会重置 PYTHONPATH，初次因此缺协议包失败，固定临时解释器后全部通过；没有修改业务来绕过环境问题。

## 检视、偏差与遗留

按 jw-review 自检工作区差异、Core/Swarm 直接消费者、公共导出、共享恢复路径、Schema、文档和包资源 glob。旧指南仍推荐分支工具，已修订。未开展独立子 Agent 评审。

无方向偏差。文档同步工具自动补充的无关历史 frontmatter 在收尾恢复，避免夹带其他模块迁移。历史配置整理记录追加后继链接，不改写旧测试事实。

定向范围通过。未调用真实在线模型、未构建发布包、未升级 Swarm Core pin、未提交推送；旧独立检索/经验库外部调用者须迁移，已确认兼容边界是 Swarm develop 主链路。Runtime 统一配置接入留到后续；代码、依赖或工件协议改变时重验受影响范围。未运行格式化或静态检查。
