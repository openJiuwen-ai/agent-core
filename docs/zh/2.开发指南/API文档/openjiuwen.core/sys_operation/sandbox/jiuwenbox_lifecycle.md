# JiuwenBox 作用域生命周期

以下接口位于 `openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox`，供持有沙箱生命周期的宿主调用。作用域为 `base_url + isolation_key`，不能把会话数量直接作为实例数量。

## 配置应用

```python
from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
    get_live_jiuwenbox_scopes,
    jiuwenbox_policy_update,
)

async with jiuwenbox_policy_update():
    live_scopes = await get_live_jiuwenbox_scopes()
    # 宿主在这里更新所有保留 card 的启动策略，完成旧 ACL 清理，
    # 然后按仍有持有者的 live_scopes 重建。不要把无持有者的实例重建出来。
```

`jiuwenbox_policy_update()` 等待已进入的创建/重建完成，并在上下文内阻止普通工具获取实例及丢失重建。宿主使用 `force_recreate_jiuwenbox_sandbox(..., reason="policy_changed")` 执行受控重建；旧实例删除与 ACL 撤销完成后才创建替代实例。该屏障不是正在执行命令的事务回滚机制；服务重载/实例删除仍负责停止旧执行。

`get_live_jiuwenbox_scopes()` 查询各已知服务的实例清单，与本进程缓存的实例 ID 取交集，并保留待删除失败的作用域供恢复。远端查询失败时保守保留已知作用域；只有成功清单才能用于判断 idle 回收。该接口必须在上述上下文内调用，避免遗漏在途创建。宿主另行保留重建失败但已无远端实例的待恢复状态。

宿主在 launcher 的 `extra_params` 中设置 `_policy_apply_pending=True`，可在应用失败、全局上下文已退出后继续阻止该 card 的工具执行；仅当应用或回滚成功才清除此内部标记。此错误不会进入宿主执行兜底。

## 最后一个持有者释放

```python
from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import release_jiuwenbox_scope

# 先停止并等待该持有者的执行任务，确认作用域已无其他持有者。
deleted_ids = await release_jiuwenbox_scope(base_url, isolation_key)
```

宿主负责引用计数，释放期间不得把同一个注册对象借给新会话。接口阻止该作用域的新获取，等待已有创建，严格执行远端删除和生命周期钩子，再失效对应 Gateway endpoint/provider 缓存；其他作用域和其他服务保持不变。

删除失败抛出异常并保留待删除身份，宿主必须保留可重试持有状态。调用者取消不取消在途删除任务；不能据取消结果声称资源已释放。成功后才注销持有者的 SysOperation。

工具获取实例时，删除任务仍在执行返回 `[SANDBOX_DELETION_PENDING]`；任务已退出但待删除身份仍保留时返回 `[SANDBOX_DELETION_FAILED]`。两者均表示本次操作未执行，不进入本地兜底。错误兼容 `RuntimeError` 并携带框架 `StatusCode`，文件工具的错误文本保留上述标识。

JiuwenSwarm 将这两类失败工具结果上报为带同名 `code` 的 `chat.error`，每个请求合并重复通知，最终 E2A 状态为 `failed`。普通文件操作错误保持工具级语义。恢复使用现有 `sandbox.restart`：先重试旧实例删除，全部清理成功后再重建；仍失败则保留阻断，不隐式修改 `sandbox.enabled`。

`SandboxGateway.invalidate_sandbox_cache(sandbox_type, base_url=None, *, isolation_key=None)` 新增可选作用域过滤，仅失效缓存并返回被移除的 provider，不单独执行远端删除。未传 `isolation_key` 的现有调用语义不变。
