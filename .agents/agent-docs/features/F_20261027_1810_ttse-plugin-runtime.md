---
id: F_20261027_1810_ttse-plugin-runtime
title: TTSE 插件构造上下文与关闭生命周期
type: F
author: xinyang_bali
author_email: 835397263@qq.com
modules: [openjiuwen/harness]
files:
  - openjiuwen/harness/manifest/meta_elements.py
  - openjiuwen/harness/extension_binder.py
  - openjiuwen/harness/rails/evolution/ttse_rail.py
designs: []
design_note: 用户确认执行外部 TTSE Rail 插件适配方案，原文位于 D:/files/自演进/方案/ttse适配code agent。
---

# TTSE 插件构造上下文与关闭生命周期

日期：2026-10-27。关联契约：S_13_resources-packages、S_04_rails-contract。

## 决策

2026-10-29 修订：按用户要求删除 FILE_RAIL_CONTEXT_FACTORY_VERSION 能力常量。文件 provider 仍按类上的 from_plugin_context 是否可调用选择同步工厂或 rail_cls(**params)，继续校验返回类型并拒绝异步工厂；没有新增其他能力标志。跨仓 JiuwenSwarm 同步移除常量预检，runtime v2 服务字段校验保留。依赖不兼容时由实际加载及宿主回滚报告错误。

本次在 core 根目录以 JiuwenSwarm 现有解释器、PYTHONUTF8=1、PYTHONPATH=本地 core 运行 resources/manifest 回归：**40 passed**；宿主 TTSE runtime/package/chat-send 装备回归：**84 passed**。未提交或推送本次删除。

文件 Rail 新增同步 context 工厂，兼容原零参构造。宿主在构建前准备模型、轨迹和数据路径，core 不依赖 JiuwenSwarm。
TTSE 增加停止调度及有总时限的异步收尾，涵盖归纳、dream 和 consult 元数据写入；关闭超时保留任务引用，宿主继续看护并阻止替代实例写库。
`aclose(timeout=10, grace_timeout=5)` 不强制取消线程 I/O 的 awaiter；线程确认退出才释放写权。这是对外部方案“到期取消”建议的实施调整，避免旧线程迟到写盘覆盖新实例。
插件 Rail init 失败由 binder 清理部分初始化；已绑定资源仍按原有逆序回滚。

## 拒绝的方案

- 在 manifest 序列化模型或依赖任意 params 透传：运行句柄不可序列化，现有 JSON 通道不支持该行为。
- 在同步 uninit 内等待异步任务：会阻塞事件循环；宿主先异步关闭，再解绑。
- 只取消 dream 而不确认退出：可能造成旧实例在新 owner 接管后继续写库。

## 验证与交接

2026-10-28 扩大回归：**211 passed**。执行文件集：`tests/unit_tests/harness/resources/`、`tests/unit_tests/harness/manifest/`、`test_deep_agent.py`、`test_deep_agent_rail_event_routing.py`、`rails/evolution/test_ttse_rail.py` 和 `rails/evolution/test_ttse_close.py`。

在本仓根目录使用 JiuwenSwarm 现有 Python 3.13 环境执行 `python -m pytest -o addopts='' -q --tb=short --show-capture=no <上述文件集>`，PYTHONPATH 包含本地 core 与宿主 `.git/codex-merge-verify-deps`，设置 PYTHONUTF8=1。
本仓 Python 3.11 环境的首次定向集曾通过 113 项；扩大集发现缺少可选 OTel exporter，改用已有宿主环境。后续一项既有在线 RL 测试缺少可选 redis 依赖，将 redis 8.1.0 补在隔离依赖目录后全部通过；没有改动 RL 代码或连接 Redis 服务。

跨仓消费者为 JiuwenSwarm 的 TTSE 插件，其扩大回归 416 passed。旧 MCP Manager 1.0.3 的实际 ZIP 另做装载、提示段和工具卸载兼容验证并通过。首轮模型调用使用可控客户端，保留真实 Model/Runner/观测回调/文件插件路径，不代表外部模型质量评测。
本次不提交代码；发布需将 core 代码、测试、文档按模块规定拆分提交，并由宿主更新依赖版本。
