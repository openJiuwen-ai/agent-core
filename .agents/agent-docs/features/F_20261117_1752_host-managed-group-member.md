---
id: F_20261117_1752_host-managed-group-member
title: 宿主管理群成员角色
type: F
author: weichenhao
author_email: weichenhao3@huawei.com
modules: [openjiuwen/agent_teams]
files: [openjiuwen/agent_teams/tools/team.py, openjiuwen/agent_teams/group_chat/message_handler.py, openjiuwen/agent_teams/schema/team.py]
designs: [jiuwenclaw/D-20261116-1201-colleague-swarmchat-membership]
---

# 宿主管理群成员角色

SwarmChat 宿主需要接入已有 AI 同事，core 为其登记独立 HOST_MANAGED 身份，不创建另一套智能体。
spawn_host_managed_member 写入 READY 名册，发布成员事件；register_host_managed_inbound 管理独立回调。
群广播处理器向被提及的宿主回调传递已持久化 ConversationMessage，消费是否完成由宿主决定。

预置装配、启动器、冷恢复、群未读接收人和关闭路径均识别新角色：不启动内部运行时，不阻塞普通群成员的未读完成判定，关闭直接落终态。
普通专家和现有人类协作路径保持原语义。

验证：core 既有群聊与人类协作 117 项通过；swarm 的真实 SQLite 集成用例验证角色注册、回调、幂等回帖及关闭。
未使用真实模型或外部渠道进行联调。代码未提交或发布，swarm 当前通过本地 editable 安装联调；正式依赖仍需另行发布固定版本。

## 冷恢复名册投影修复（2026-11-17）

Web 真实 @ 路径进入 RecoveryManager.recover_team 时，list_member_roster 返回 MemberRosterEntry，只有名称、显示名和状态。
原宿主管理角色分支错误读取 member.role，导致整个群冷启动失败。
改为经 TeamBackend.get_member 读取当前持久化角色；已删除成员跳过，HOST_MANAGED 仍不重启运行时，普通专家保留原恢复与并发领取逻辑。
回归使用真实 MemberRosterEntry，同时验证宿主管理身份跳过和普通专家恢复。恢复／新角色 7 项通过，既有群聊与人类协作 117 项通过。
实际 Web 群完成冷恢复，消息进入同事执行并完成；宿主另修复无 IM app_id 的本地发件箱校验后，页面收到小九回复。

## rebase 后恢复接入

在新基线保留原有 Bridge Agent 和群消息生命周期逻辑，恢复遗漏的 HOST_MANAGED 枚举、登记、消息回调、未读筛选和冷恢复分支。
新基线下 core 群聊与恢复回归 122 项通过，宿主同事接入回归 30 项通过。


## 未读与消息投递隔离

未读接收者查询统一排除 HOST_MANAGED，避免旧广播、普通广播或私信使团队无法结束。宿主通知作为有界后台任务运行，慢回调不占用专家唤醒和群消息派发。暂停和停止取消通知任务，宿主可按消息表补齐。
core 群聊、数据库与协调生命周期回归 321 项通过，SwarmChat 宿主回归 30 项通过。


## 群消息重投、直发拒绝与建团重试

core 按会话、消息 ID 和宿主管理成员去重，并允许失败接收者在重投时重试。SwarmChat 接收器使用异步回调调度提交，持久消费去重与补扫仍由宿主负责。
团队私信和任务入口在产生不可消费的记录前拒绝宿主管理成员。预置身份先校验，数据库注册失败时清理本次新建团队，避免重试误走接管路径。
core 定向回归 498 项通过、1 项跳过，覆盖重复回调、失败重投、直发、任务分配和预置成员建团重试。SwarmChat 服务回归 59 项，验证无需等到补扫即可接受实时通知。
