## enable_task_verification（验证闸）

控制本次实例是否启用验证闸（reviewer 系统）。双层判定——用户配置是天花板，你在天花板内选择：

- 用户配置 false → 无论传什么都不生效，验证闸强制关闭
- 用户配置 true → 你自己决定：
  - 不传 / true：启用。生产代码、正式设计、核心功能——关键任务配 reviewer
  - false：关闭。原型探索、快速试验、一次性临时任务——任务直接完成，不经验收

**实际生效值以本工具的返回结果为准**（`task_verification=...`）：生效值为 false 时，
create_task / update_task 里写的 reviewer 会被忽略，不要再给任务指派验证者。
