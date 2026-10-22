## 任务内联成员（`member`）

规划任务时可以**顺手把承担者带出来**：在任务节点里写 `member` 对象而不是 `assignee`，两者**二选一**。

```
{"task_id": "impl-api", "title": "...", "content": "...",
 "member": {"member_name": "api-dev-1", "display_name": "API 开发专家",
            "desc": "...", "prompt": "...", "lifetime": "dormant"},
 "reviewer": [...]}
```

- `member_name` / `display_name` / `desc` / `prompt` / `model_name` 的含义与 `spawn_teammate` 完全相同（命名约束、公开/私有边界一并适用）；`member_name` 不能与现有成员重名
- **`lifetime` 必填**，决定这个成员干完活之后怎么退场：
  - `shutdown`：名下任务全部终态后自动关闭，等价于你手动 `shutdown_member`；**名字不可复用**，同领域再建人请编号
  - `dormant`：名下任务全部终态后自动休眠——保留在名册、不处理任何事件、不占用资源；**只有被点名才醒**（`send_message(to=该成员)` / `targets` 含它 / 给它派新任务），广播不会唤醒。醒来处理完又会再次休眠
- 团队声明了外部 CLI 类型时，`member` 还可以带 `cli_agent`（含义同 `spawn_teammate.cli_agent`）让成员以外部 CLI 运行；模型参数（`fallback_model_name` 等）放进 `member.external_cli`。`lifetime` 对外部 CLI 成员同样生效
- 内联成员与 `spawn_teammate` 建的成员在运行时**没有任何区别**：一样被调度框架拉起开工、一样可被改派、一样出现在 `list_members`
- 内联成员的退场由调度框架自动完成；休眠成员要彻底移除时再对它调一次 `shutdown_member`（`clean_team` 前仍需全员 SHUTDOWN）
- 长期复用的成员（跨多个任务批次）仍用 `spawn_teammate` 建；只为一个任务存在的专家用内联
