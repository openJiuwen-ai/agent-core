---
id: F_20261109_1752_shared-capability-fingerprints
title: Symphony 共享指纹与会话能力检索
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-09
updated: 2026-11-10
modules: [symphony, core/single_agent, agent_teams/tools, jiuwenswarm/jiuwenswarm/runtime/symphony]
files: [openjiuwen/symphony/tree_engine.py, openjiuwen/symphony/discovery/directory_toolkit.py, openjiuwen/symphony/discovery/skillfs.py, openjiuwen/symphony/discovery/capability_search.py, openjiuwen/symphony/shared/fingerprint/service.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/core/single_agent/ability_manager.py, openjiuwen/agent_teams/tools/tool_expert.py, jiuwenswarm/jiuwenswarm/runtime/symphony/capabilities.py, jiuwenswarm/jiuwenswarm/agents/harness/common/tools/skill_retrieval_toolkits.py]
designs: [D-20261109-1745-shared-capability-fingerprints]
---

# Symphony 共享指纹与会话能力检索

## 实施

依据 [共享指纹设计](../designs/active/D-20261109-1745-shared-capability-fingerprints/design.md) 将 Skill 指纹服务注入 Core 检索构建。两侧共用已配置集合的 `fingerprint.json` 与增量缓存，图版本使用构图任务实际持有的快照。无模型且允许构树回退时，用确定性元数据构建指纹。

Swarm 从本地目录读取专家与连接器元数据、从已配置服务读取 MCP 描述，按类型集合发布指纹。检索从当前 Skill Discovery、专家工具范围及 AbilityManager 注册状态取得候选，以匹配内容哈希的指纹补充描述。MCP 工具权限撤回后，候选和延迟工具 Schema 查询使用同一实时许可范围。已选中的随包 Skill 可作为连接器的动作引用。

## 接口合并

首版曾并列注册 `skill_index` 和 `capability_search`，并在后者为 Skill 构造第二套候选。根据用户后续要求，模型只注册 `capability_search`，使用单个 `type` 路由。SkillFS 直接读取指纹并复用原目录搜索、分类和游标。宿主回调仅处理专家、连接器和 MCP，沿用会话实时权限。合并阶段曾保留 Core SDK 的 `skill_index()`。

## 首版验证

- Core 基线 `29fe0e61e` 的本地候选使用 Python 3.11.15 运行 `tests/unit_tests/symphony`、MCP allowlist 和专家工具回归，985 项通过。需要本地回环监听的连接复用用例单独通过，共 986 项。新增用例通过正常 Runtime 构建、会话查询和构图入口验证同内容提取复用、快照绑定和过期回退。
- Swarm 基线 `47b98f63c` 的本地候选使用 Python 3.12.13 和同一 Core 候选源码，Symphony、Code Adapter、专家目录与连接器名称回归共 299 项通过。新增用例通过宿主构建和工具入口验证四类指纹发布、跨类型同名、MCP 动作反向召回、撤权和断开。
- 使用 Swarm 当前固定的旧 Core 提交验证原有 Runtime 接入与无模型构建，3 项通过。

## 合并后验证

- 本地 Core 候选运行 Symphony、MCP allowlist 和专家工具用例，含单独运行的本机 HTTP 连接复用，共 986 项通过。覆盖共享指纹召回与过期回退、Skill 目录游标及旧 SDK 调用。
- Swarm 对接本地 Core 候选共 590 项通过，覆盖单工具注册、四类检索、团队 Skill 可见性、撤权和断开、权限快路径与工具结果。
- Swarm 使用固定的旧 Core 提交运行兼容用例，180 项通过。覆盖原有 Runtime、无模型构建和只读权限。另验证适配器只注册 `skill_index` 并能找到会话可见 Skill。
- Web 的时间线回归 2 项通过。Node 行为检查覆盖新旧工具名、默认搜索和分类浏览的展示。

## 退役旧入口

2026-11-10，用户确认 Core 与 Swarm 的版本绑定。模型、SDK 和管理端统一使用 `capability_search`，移除旧 SDK 方法、双工具注册、旧 Core 探测以及旧入口独有的路径查询、读取和输出 pipeline。Skill 分类、批量检索、游标和输出预算由同一个会话管理。Swarm 的调用切换和 Core 固定版本在同一个提交交付。

- Core 回归 986 项通过，覆盖旧 SDK 入口移除、共享指纹、分类浏览、批量搜索和游标。
- Swarm 对接本地 Core 候选的回归 810 项通过，覆盖四类检索、管理端、权限、提示和事件。
- Web 时间线回归 2 项通过，另通过 Node 验证新工具的搜索词和分类浏览展示。

## 交付边界

跨仓回归使用配套 Core 候选。Swarm 的依赖声明、锁文件和容器参数绑定到包含检索接口的 Core 提交。真实外部模型和 MCP 服务的连接、授权及调用仍需联调。
